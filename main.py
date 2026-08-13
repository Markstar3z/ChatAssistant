import asyncio
import json
import os
from collections import defaultdict, deque

import httpx
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
AGENTROUTER_API_KEY = os.getenv("AGENTROUTER_API_KEY", "").strip()
AGENTROUTER_MODEL = os.getenv(
    "AGENTROUTER_MODEL",
    "claude-opus-4-8"
).strip()

AGENTROUTER_BASE_URL = os.getenv(
    "AGENTROUTER_BASE_URL",
    "https://agentrouter.org"
).rstrip("/")

if not BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is missing.")

if not AGENTROUTER_API_KEY:
    raise RuntimeError("AGENTROUTER_API_KEY is missing.")

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
AGENTROUTER_MESSAGES_URL = f"{AGENTROUTER_BASE_URL}/v1/messages"

with open("knowledge.json", "r", encoding="utf-8") as f:
    KNOWLEDGE = json.load(f)

history = defaultdict(lambda: deque(maxlen=16))
business_owners = {}

SYSTEM_PROMPT = f"""
You are Kairo Assistant, an AI assistant helping Kairo manage Web3 conversations on Telegram.

Your role:
- Reply naturally, briefly, and professionally.
- Answer questions using ONLY the knowledge base below.
- Understand what a prospect or project needs.
- Ask useful follow-up questions.
- Keep the conversation warm and human.
- Avoid sounding like a generic customer-support bot.

Important boundaries:
- You are Kairo Assistant, not Kairo himself.
- Never say Kairo personally saw or read a message unless you know he did.
- Never invent clients, partnerships, achievements, credentials, prices, dates, availability, or testimonials.
- Never guarantee growth, token performance, funding, listings, or results.
- Never finalize pricing.
- Never agree to contracts or binding commitments.
- Never approve payments or wallet transfers.
- Never request passwords, OTPs, private keys, or seed phrases.
- If the discussion reaches final negotiation, payment, contracts, sensitive access, or a binding decision, say that Kairo will take over personally.
- Focus primarily on Web3, content, community, growth, collaborations, and related work.

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


def convert_history(chat_id: int):
    result = []

    for msg in history[chat_id]:
        result.append({
            "role": msg["role"],
            "content": msg["content"]
        })

    return result


async def ask_agentrouter(chat_id: int, user_text: str) -> str:
    messages = convert_history(chat_id)

    messages.append({
        "role": "user",
        "content": user_text
    })

    headers = {
        "Authorization": f"Bearer {AGENTROUTER_API_KEY}",
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01"
    }

    payload = {
        "model": AGENTROUTER_MODEL,
        "max_tokens": 400,
        "system": SYSTEM_PROMPT,
        "messages": messages
    }

    async with httpx.AsyncClient(timeout=90) as client:
        response = await client.post(
            AGENTROUTER_MESSAGES_URL,
            headers=headers,
            json=payload
        )

        print("AgentRouter status:", response.status_code)

        if response.status_code >= 400:
            print(
                "AgentRouter error:",
                response.text
            )

        response.raise_for_status()

        data = response.json()

    content = data.get("content", [])

    answer = ""

    for block in content:
        if block.get("type") == "text":
            answer += block.get("text", "")

    answer = answer.strip()

    if not answer:
        raise RuntimeError(
            f"No text returned from AgentRouter: {data}"
        )

    history[chat_id].append({
        "role": "user",
        "content": user_text
    })

    history[chat_id].append({
        "role": "assistant",
        "content": answer
    })

    return answer


def should_ignore_message(message: dict) -> bool:
    connection_id = message.get("business_connection_id")

    if not connection_id:
        return True

    text = (message.get("text") or "").strip()

    if not text:
        return True

    sender = message.get("from") or {}

    if sender.get("is_bot"):
        return True

    sender_id = sender.get("id")
    owner_id = business_owners.get(connection_id)

    if owner_id and sender_id == owner_id:
        print("Ignoring outgoing Kairo message.")
        return True

    if message.get("sender_business_bot"):
        print("Ignoring business-bot message.")
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

    sender = message.get("from") or {}

    print(
        "Incoming business message:",
        {
            "chat_id": chat_id,
            "from": sender.get("username"),
            "text": text[:120]
        }
    )

    try:
        answer = await ask_agentrouter(chat_id, text)

    except httpx.HTTPStatusError as error:
        print(
            "AgentRouter HTTP failure:",
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
                "text": answer
            }
        )

        print("Reply sent successfully.")

    except Exception as error:
        print(
            "Telegram reply failed:",
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

    if "edited_business_message" in update:
        return

    if "deleted_business_messages" in update:
        return


async def poll():
    offset = 0

    print("Kairo Secretary bot is running...")
    print("AI provider: AgentRouter / Claude")
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
                "Polling HTTP error:",
                repr(error)
            )
            await asyncio.sleep(3)

        except Exception as error:
            print(
                "Polling error:",
                repr(error)
            )
            await asyncio.sleep(3)


if __name__ == "__main__":
    asyncio.run(poll())
