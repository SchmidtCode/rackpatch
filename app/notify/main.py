from __future__ import annotations

import sys

import requests
from fastapi import FastAPI

from common import config, db, runtime_settings


app = FastAPI(title="rackpatch-notify", version=config.APP_VERSION)
SESSION = requests.Session()


@app.on_event("startup")
def on_startup() -> None:
    db.init_db()


def telegram_api(bot_token: str, method: str, payload: dict) -> dict:
    response = SESSION.post(
        f"https://api.telegram.org/bot{bot_token}/{method}",
        json=payload,
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/notify")
def notify(payload: dict) -> dict:
    message = str(payload.get("message", "")).strip()
    if not message:
        return {"status": "ignored"}
    telegram_settings = runtime_settings.get_telegram_settings(include_secret=True)
    if telegram_settings["bot_token"] and telegram_settings["chat_ids"]:
        for chat_id in telegram_settings["chat_ids"]:
            telegram_api(telegram_settings["bot_token"], "sendMessage", {"chat_id": chat_id, "text": message})
    else:
        print(message, file=sys.stdout, flush=True)
    return {"status": "ok"}
