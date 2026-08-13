# Kairo Secretary + DeepSeek Bot

This version is built for Telegram Business / Secretary Mode.

It listens for `business_message` updates and replies through the connected
Telegram account using `business_connection_id`.

## What it does

Flow:

1. Someone messages or replies to your Telegram account.
2. Telegram sends the connected bot a `business_message` update.
3. The bot sends the incoming text to DeepSeek.
4. DeepSeek writes the response.
5. The bot calls Telegram `sendMessage` with the same `business_connection_id`.
6. The reply is sent on behalf of the connected account.

## 1. BotFather

Your bot must have Business / Secretary Mode enabled.

## 2. iMe / Telegram

Connect the bot in Chat Automation / Secretary Mode.

For your first test, use **Only Selected Chats**.

## 3. Configure locally

Copy:

`.env.example` -> `.env`

Then fill in:

- `TELEGRAM_BOT_TOKEN`
- `DEEPSEEK_API_KEY`

Do not upload `.env` to GitHub.

## 4. Customize what DeepSeek knows

Edit `knowledge.json`.

Only include information you are comfortable having the bot tell prospects.
Do not add fake credentials, clients, or promises.

## 5. Install

```bash
pip install -r requirements.txt
```

## 6. Run

```bash
python main.py
```

If the Telegram Business connection is active, the console should print a
business connection update when Telegram sends one.

## 7. First test

Use a second Telegram account and send a message into one of the chats that
you allowed the bot to manage.

Keep the test narrow at first.

## Safety behavior

This starter intentionally hands back sensitive topics rather than letting
DeepSeek negotiate or commit on your behalf.

The bot should not finalize:
- prices,
- contracts,
- payments,
- wallet actions,
- sensitive access,
- binding commitments.

## Memory

Conversation history is stored in RAM only.
Restarting the bot clears it.

For a permanent deployment, the next upgrade should use SQLite or Redis.
