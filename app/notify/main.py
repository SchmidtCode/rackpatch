from __future__ import annotations

import sys

import requests
from fastapi import FastAPI

from common import config


app = FastAPI(title="ops-notify", version=config.APP_VERSION)
SESSION = requests.Session()


def telegram_api(method: str, payload: dict) -> dict:
    response = SESSION.post(
        f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/{method}",
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
    if config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_IDS:
        for chat_id in config.TELEGRAM_CHAT_IDS:
            telegram_api("sendMessage", {"chat_id": chat_id, "text": message})
    else:
        print(message, file=sys.stdout, flush=True)
    return {"status": "ok"}

