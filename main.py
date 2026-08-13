import asyncio
import json
import os
from collections import defaultdict, deque

import httpx
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat").strip()

if not BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is missing from .env")
if not DEEPSEEK_API_KEY:
    raise RuntimeError("DEEPSEEK_API_KEY is missing from .env")

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"

with open("knowledge.json", "r", encoding="utf-8") as f:
    KNOWLEDGE = json.load(f)

# Short per-chat memory. This clears on restart.
history = defaultdict(lambda: deque(maxlen=16))

SYSTEM_PROMPT = f"""
You are Kairo Assistant, an AI assistant that replies on behalf of Kairo inside Telegram private chats.

Your goals:
- Reply naturally, briefly, and professionally.
- Help with Web3 outreach conversations.
- Answer questions using ONLY the information in the knowledge base below.
- Understand what the other person/project needs.
- Ask useful follow-up questions when needed.
- Keep the conversation moving without sounding robotic.

Important boundaries:
- Never pretend to be Kairo personally.
- Never claim Kairo has seen a message unless he actually has.
- Never invent clients, partnerships, achievements, prices, dates, availability, or credentials.
- Never make binding commitments.
- Never agree to final pricing, contracts, payments, wallet transfers, seed phrases, or credentials.
- If a conversation reaches negotiation, payment, legal terms, sensitive access, or anything requiring Kairo personally,
  say that Kairo will take over from there.
- Stay focused on Web3/content/community/growth related conversations.
- Keep most replies under 120 words unless the user clearly needs more detail.

Knowledge base:
{json.dumps(KNOWLEDGE, ensure_ascii=False, indent=2)}
""".strip()


async def tg_call(method: str, payload: dict):
    async with httpx.AsyncClient(timeout=45) as client:
        r = await client.post(f"{TG_API}/{method}", json=payload)
        r.raise_for_status()
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(data)
        return data["result"]


async def deepseek_reply(chat_id: int, user_text: str) -> str:
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(history[chat_id])
    messages.append({"role": "user", "content": user_text})

    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": messages,
        "temperature": 0.55,
        "max_tokens": 450,
    }

    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(DEEPSEEK_URL, headers=headers, json=payload)
        r.raise_for_status()
        data = r.json()

    text = data["choices"][0]["message"]["content"].strip()

    history[chat_id].append({"role": "user", "content": user_text})
    history[chat_id].append({"role": "assistant", "content": text})
    return text


def should_ignore_business_message(message: dict) -> bool:
    """
    Avoid replying to our own outgoing business messages or unsupported payloads.
    """
    if message.get("from", {}).get("is_bot"):
        return True

    # Telegram business messages can represent messages in chats managed by the bot.
    # We only respond to text messages from the other human participant.
    text = (message.get("text") or "").strip()
    if not text:
        return True

    return False


async def reply_via_business_connection(message: dict):
    business_connection_id = message.get("business_connection_id")
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    text = (message.get("text") or "").strip()

    if not business_connection_id or not chat_id or not text:
        return

    if should_ignore_business_message(message):
        return

    try:
        answer = await deepseek_reply(chat_id, text)
    except httpx.HTTPStatusError as e:
        print("DeepSeek HTTP error:", e.response.status_code, e.response.text)
        answer = "I hit a temporary issue while processing that. Kairo will take over shortly."
    except Exception as e:
        print("DeepSeek error:", repr(e))
        answer = "I hit a temporary issue while processing that. Kairo will take over shortly."

    payload = {
        "business_connection_id": business_connection_id,
        "chat_id": chat_id,
        "text": answer,
        "disable_web_page_preview": True,
    }

    try:
        await tg_call("sendMessage", payload)
    except Exception as e:
        print("Telegram sendMessage error:", repr(e))


async def handle_update(update: dict):
    if "business_connection" in update:
        bc = update["business_connection"]
        print(
            "Business connection update:",
            {
                "id": bc.get("id"),
                "is_enabled": bc.get("is_enabled"),
                "user_chat_id": bc.get("user_chat_id"),
                "rights": bc.get("rights"),
            },
        )
        return

    if "business_message" in update:
        await reply_via_business_connection(update["business_message"])
        return

    if "edited_business_message" in update:
        # We deliberately do not auto-reply to edits to avoid duplicate replies.
        return

    if "deleted_business_messages" in update:
        return


async def poll():
    offset = 0
    print("Kairo Secretary bot is running...")

    while True:
        try:
            updates = await tg_call(
                "getUpdates",
                {
                    "offset": offset,
                    "timeout": 30,
                    "allowed_updates": [
                        "business_connection",
                        "business_message",
                        "edited_business_message",
                        "deleted_business_messages",
                    ],
                },
            )

            for update in updates:
                offset = update["update_id"] + 1
                await handle_update(update)

        except (httpx.HTTPError, RuntimeError) as e:
            print("Polling error:", repr(e))
            await asyncio.sleep(3)
        except Exception as e:
            print("Unexpected error:", repr(e))
            await asyncio.sleep(3)


if __name__ == "__main__":
    asyncio.run(poll())
