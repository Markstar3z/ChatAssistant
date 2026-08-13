import asyncio
import json
import os
from collections import defaultdict, deque

import httpx
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
AGENTROUTER_API_KEY = os.getenv("AGENTROUTER_API_KEY", "").strip()
AGENTROUTER_MODEL = os.getenv("AGENTROUTER_MODEL", "gpt-5.6-sol").strip()

# Keep this configurable in Railway in case AgentRouter changes the gateway.
AGENTROUTER_BASE_URL = os.getenv(
    "AGENTROUTER_BASE_URL",
    "https://agentrouter.org/v1"
).rstrip("/")

if not BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is missing.")

if not AGENTROUTER_API_KEY:
    raise RuntimeError("AGENTROUTER_API_KEY is missing.")

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
AGENTROUTER_CHAT_URL = f"{AGENTROUTER_BASE_URL}/chat/completions"

with open("knowledge.json", "r", encoding="utf-8") as f:
    KNOWLEDGE = json.load(f)

# Conversation memory per Telegram chat.
# This resets whenever Railway restarts.
history = defaultdict(lambda: deque(maxlen=16))

# Maps business_connection_id -> Telegram user ID of your Kairo account.
business_owners = {}

SYSTEM_PROMPT = f"""
You are Kairo Assistant, an AI assistant helping Kairo manage Web3 conversations on Telegram.

Your role:
- Reply naturally, briefly, and professionally.
- Answer questions about Kairo's services using ONLY the knowledge base below.
- Help understand what a prospect or project needs.
- Ask useful follow-up questions when appropriate.
- Keep conversations warm and human, not robotic.
- Most replies should be concise.

Important:
- You are Kairo Assistant, not Kairo himself.
- Never say Kairo personally read or saw something unless you know that is true.
- Never invent clients, partnerships, achievements, testimonials, credentials, prices, dates, or availability.
- Never promise guaranteed growth, token performance, listings, funding, or results.
- Never agree to final pricing.
- Never agree to contracts or binding commitments.
- Never send or request passwords, seed phrases, private keys, login codes, or sensitive credentials.
- Never approve payments or wallet transfers.
- If the conversation reaches final negotiation, payment, contracts, sensitive access,
  or something that requires Kairo personally, say Kairo will take over from there.
- Stay focused on Web3, content, community, growth, collaborations, and related work.
- Do not answer unrelated personal questions as if you were Kairo.

Knowledge base:
{json.dumps(KNOWLEDGE, ensure_ascii=False, indent=2)}
""".strip()


async def telegram_call(method: str, payload: dict):
    async with httpx.AsyncClient(timeout=45) as client:
        response = await client.post(
            f"{TG_API}/{method}",
            json=payload
        )

        if response.status_code >= 400:
            print(
                f"Telegram {method} error:",
                response.status_code,
                response.text
            )

        response.raise_for_status()

        data = response.json()

        if not data.get("ok"):
            raise RuntimeError(data)

        return data["result"]


async def ask_agentrouter(chat_id: int, user_text: str) -> str:
    messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT
        }
    ]

    messages.extend(list(history[chat_id]))

    messages.append(
        {
            "role": "user",
            "content": user_text
        }
    )

    headers = {
        "Authorization": f"Bearer {AGENTROUTER_API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": AGENTROUTER_MODEL,
        "messages": messages,
        "temperature": 0.6,
        "max_tokens": 450
    }

    async with httpx.AsyncClient(timeout=90) as client:
        response = await client.post(
            AGENTROUTER_CHAT_URL,
            headers=headers,
            json=payload
        )

        if response.status_code >= 400:
            print(
                "AgentRouter HTTP error:",
                response.status_code,
                response.text
            )

        response.raise_for_status()

        data = response.json()

    answer = data["choices"][0]["message"]["content"].strip()

    history[chat_id].append(
        {
            "role": "user",
            "content": user_text
        }
    )

    history[chat_id].append(
        {
            "role": "assistant",
            "content": answer
        }
    )

    return answer


def should_ignore_message(message: dict) -> bool:
    connection_id = message.get("business_connection_id")

    if not connection_id:
        return True

    text = (message.get("text") or "").strip()

    if not text:
        return True

    sender = message.get("from") or {}
    sender_id = sender.get("id")

    # Ignore messages sent by bots.
    if sender.get("is_bot"):
        return True

    # Ignore messages sent by your own Kairo business account.
    owner_id = business_owners.get(connection_id)

    if owner_id and sender_id == owner_id:
        print("Ignoring outgoing message from Kairo.")
        return True

    # Telegram may identify messages sent by a connected business bot.
    if message.get("sender_business_bot"):
        print("Ignoring message sent by business bot.")
        return True

    return False


async def handle_business_message(message: dict):
    if should_ignore_message(message):
        return

    connection_id = message.get("business_connection_id")
    chat = message.get("chat") or {}
    chat_id = chat.get("id")

    text = (message.get("text") or "").strip()

    if not connection_id or not chat_id or not text:
        return

    print(
        "Incoming business message:",
        {
            "chat_id": chat_id,
            "from": (message.get("from") or {}).get("username"),
            "text": text[:120]
        }
    )

    try:
        answer = await ask_agentrouter(chat_id, text)

    except httpx.HTTPStatusError as error:
        print(
            "AgentRouter request failed:",
            error.response.status_code,
            error.response.text
        )
        return

    except Exception as error:
        print(
            "AgentRouter unexpected error:",
            repr(error)
        )
        return

    try:
        await telegram_call(
            "sendMessage",
            {
                "business_connection_id": connection_id,
                "chat_id": chat_id,
                "text": answer,
                "link_preview_options": {
                    "is_disabled": True
                }
            }
        )

        print("Reply sent successfully.")

    except Exception as error:
        print(
            "Telegram sendMessage failed:",
            repr(error)
        )


async def handle_update(update: dict):

    if "business_connection" in update:
        connection = update["business_connection"]

        connection_id = connection.get("id")
        owner = connection.get("user") or {}

        if connection_id and owner.get("id"):
            business_owners[connection_id] = owner["id"]

        print(
            "Business connection update:",
            {
                "id": connection_id,
                "owner_id": owner.get("id"),
                "username": owner.get("username"),
                "is_enabled": connection.get("is_enabled"),
                "rights": connection.get("rights")
            }
        )

        return

    if "business_message" in update:
        await handle_business_message(
            update["business_message"]
        )
        return

    # Don't reply again when someone simply edits their message.
    if "edited_business_message" in update:
        return

    if "deleted_business_messages" in update:
        return


async def poll():
    offset = 0

    print("Kairo Secretary bot is running...")
    print("AI provider: AgentRouter")
    print("Model:", AGENTROUTER_MODEL)
    print("Gateway:", AGENTROUTER_BASE_URL)

    while True:
        try:
            updates = await telegram_call(
                "getUpdates",
                {
                    "offset": offset,
                    "timeout": 30,
                    "allowed_updates": [
                        "business_connection",
                        "business_message",
                        "edited_business_message",
                        "deleted_business_messages"
                    ]
                }
            )

            for update in updates:
                offset = update["update_id"] + 1

                await handle_update(update)

        except httpx.HTTPError as error:
            print(
                "Network/API polling error:",
                repr(error)
            )
            await asyncio.sleep(3)

        except Exception as error:
            print(
                "Unexpected polling error:",
                repr(error)
            )
            await asyncio.sleep(3)


if __name__ == "__main__":
    asyncio.run(poll())
