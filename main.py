import asyncio
import json
import os
from collections import defaultdict, deque

import httpx
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-2.5-flash"
).strip()

if not BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is missing.")

if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY is missing.")

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

GEMINI_URL = (
    "https://generativelanguage.googleapis.com/"
    f"v1beta/models/{GEMINI_MODEL}:generateContent"
)

with open("knowledge.json", "r", encoding="utf-8") as f:
    KNOWLEDGE = json.load(f)

history = defaultdict(lambda: deque(maxlen=16))
business_owners = {}

SYSTEM_PROMPT = f"""
You are Kairo Assistant, an AI assistant helping Kairo manage Web3 conversations on Telegram.

Your role:
- Reply naturally, briefly, and professionally.
- Answer questions about Kairo using ONLY the knowledge base below.
- Understand what a prospect or Web3 project needs.
- Ask useful follow-up questions when appropriate.
- Keep conversations warm, conversational, and human.
- Avoid sounding like customer support or a generic AI bot.
- Keep most replies short enough for Telegram.

Important:
- You are Kairo Assistant, not Kairo himself.
- Never claim Kairo personally saw or read a message unless you know that is true.
- Never invent clients, partnerships, achievements, credentials, prices, dates, availability, or testimonials.
- Never guarantee growth, token performance, funding, listings, or results.
- Never finalize prices.
- Never agree to contracts or binding commitments.
- Never authorize payments or wallet transactions.
- Never request passwords, OTPs, private keys, seed phrases, or sensitive credentials.
- If final negotiation, payments, contracts, sensitive access, or binding decisions come up,
  explain that Kairo will take over personally.
- Focus primarily on Web3, content, community, growth, collaborations, projects, and related work.

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


def build_gemini_history(chat_id: int):
    contents = []

    for message in history[chat_id]:
        role = "user" if message["role"] == "user" else "model"

        contents.append({
            "role": role,
            "parts": [
                {
                    "text": message["content"]
                }
            ]
        })

    return contents


async def ask_gemini(chat_id: int, user_text: str) -> str:
    contents = build_gemini_history(chat_id)

    contents.append({
        "role": "user",
        "parts": [
            {
                "text": user_text
            }
        ]
    })

    payload = {
        "system_instruction": {
            "parts": [
                {
                    "text": SYSTEM_PROMPT
                }
            ]
        },
        "contents": contents,
        "generationConfig": {
            "temperature": 0.6,
            "maxOutputTokens": 400
        }
    }

    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": GEMINI_API_KEY
    }

    async with httpx.AsyncClient(timeout=90) as client:
        response = await client.post(
            GEMINI_URL,
            headers=headers,
            json=payload
        )

        print("Gemini status:", response.status_code)

        if response.status_code >= 400:
            print(
                "Gemini error:",
                response.text
            )

        response.raise_for_status()

        data = response.json()

    candidates = data.get("candidates", [])

    if not candidates:
        raise RuntimeError(
            f"Gemini returned no candidates: {data}"
        )

    parts = (
        candidates[0]
        .get("content", {})
        .get("parts", [])
    )

    answer_parts = []

    for part in parts:
        text = part.get("text")

        if text:
            answer_parts.append(text)

    answer = "\n".join(answer_parts).strip()

    if not answer:
        raise RuntimeError(
            f"Gemini returned no text: {data}"
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
    connection_id = message.get(
        "business_connection_id"
    )

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

    connection_id = message.get(
        "business_connection_id"
    )

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
        answer = await ask_gemini(
            chat_id,
            text
        )

    except httpx.HTTPStatusError as error:
        print(
            "Gemini HTTP failure:",
            error.response.status_code,
            error.response.text
        )
        return

    except Exception as error:
        print(
            "Gemini unexpected error:",
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
        connection = update[
            "business_connection"
        ]

        connection_id = connection.get("id")
        owner = connection.get("user") or {}

        if connection_id and owner.get("id"):
            business_owners[
                connection_id
            ] = owner["id"]

        print(
            "Business connection update:",
            {
                "id": connection_id,
                "owner_id": owner.get("id"),
                "username": owner.get("username"),
                "is_enabled": connection.get(
                    "is_enabled"
                ),
                "rights": connection.get(
                    "rights"
                )
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
    print("AI provider: Google Gemini")
    print("Model:", GEMINI_MODEL)

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
