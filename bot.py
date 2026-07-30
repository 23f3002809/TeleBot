import json
import time
import os
import base64
import requests
from openai import OpenAI
from telegram import Update
from fastapi import FastAPI, Request
from telegram.ext import Application, MessageHandler, ContextTypes, filters

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
AIPIPE_TOKEN = os.environ["AIPIPE_TOKEN"]
GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]


GITHUB_REPO = "23f3002809/TeleBot"
GITHUB_FILE_PATH = "run.jsonl"

LOG_URL = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/{GITHUB_FILE_PATH}"

client = OpenAI(base_url="https://aipipe.org/openai/v1", api_key=AIPIPE_TOKEN)
LOG_FILE = "run.jsonl"
conversation_history = {}


def log_event(event: dict):
    event["timestamp"] = time.time()
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(event) + "\n")


def upload_log():
    """Append the local run.jsonl content to the GitHub-hosted copy via the Contents API."""
    api_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE_PATH}"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }

    try:
        with open(LOG_FILE, "r") as f:
            local_content = f.read()

        # Get current file (need its sha to update; may not exist yet)
        resp = requests.get(api_url, headers=headers)
        if resp.status_code == 200:
            remote_data = resp.json()
            sha = remote_data["sha"]
            remote_content = base64.b64decode(remote_data["content"]).decode("utf-8")
        elif resp.status_code == 404:
            sha = None
            remote_content = ""
        else:
            resp.raise_for_status()
            return

        
        # if local_content not in remote_content:
        #     new_content = remote_content + local_content
        # else:
        #     new_content = remote_content
        
        new_content = local_content
        payload = {
            "message": "Update run log",
            "content": base64.b64encode(new_content.encode("utf-8")).decode("utf-8"),
        }
        if sha:
            payload["sha"] = sha

        put_resp = requests.put(api_url, headers=headers, json=payload)
        put_resp.raise_for_status()

    except Exception as e:
        print("Upload failed:", e)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_text = update.message.text
    log_event({"type": "incoming", "chat_id": chat_id, "text": user_text})
    history = conversation_history.setdefault(chat_id, [])
    history.append({"role": "user", "content": user_text})
    system_prompt = (
        "You are a careful data analyst. The user's LAST message asks a data-analysis "
        "question and tells you exactly what JSON shape to reply with. Work out the "
        "real answer (use any public data you know, e.g. MOSPI statistics, general "
        "world knowledge, or arithmetic on numbers given in the message). "
        "Reply with ONLY that exact JSON object and absolutely nothing else — no "
        "explanation, no markdown, no code fences, just the raw JSON."
    )
    response = client.chat.completions.create(
        model="gpt-5-mini",
        messages=[{"role": "system", "content": system_prompt}] + history[-6:],
    )
    reply_text = response.choices[0].message.content.strip()
    history.append({"role": "assistant", "content": reply_text})

    try:
        parsed = json.loads(reply_text)
    except json.JSONDecodeError:
        start, end = reply_text.find("{"), reply_text.rfind("}")
        parsed = json.loads(reply_text[start:end + 1])

    parsed["log_url"] = LOG_URL
    final_reply = json.dumps(parsed)
    log_event({"type": "outgoing", "chat_id": chat_id, "text": final_reply})
    upload_log()
    await update.message.reply_text(final_reply)


telegram_app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
telegram_app.add_handler(
    MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
)
app = FastAPI()


@app.on_event("startup")
async def startup():
    await telegram_app.initialize()
    await telegram_app.start()
    await telegram_app.bot.set_webhook(
        url=f"{os.environ['RENDER_EXTERNAL_URL']}/webhook"
    )


@app.on_event("shutdown")
async def shutdown():
    await telegram_app.stop()
    await telegram_app.shutdown()


@app.get("/")
async def root():
    return {"status": "running"}


@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)
    return {"ok": True}
