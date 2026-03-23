from __future__ import annotations

from fastapi import FastAPI

from common import config, db, notify as common_notify


app = FastAPI(title="rackpatch-notify", version=config.APP_VERSION)


@app.on_event("startup")
def on_startup() -> None:
    db.init_db()


def _public_delivery_state() -> dict:
    state = common_notify.delivery_state()
    return {
        "configured": state["configured"],
        "mode": state["mode"],
        "reason": state["reason"],
        "bot_token_configured": state["bot_token_configured"],
        "chat_ids_configured": state["chat_ids_configured"],
        "chat_count": state["chat_count"],
    }


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "notify",
        "version": config.APP_VERSION,
        "delivery": _public_delivery_state(),
    }


@app.get("/ready")
def ready() -> dict:
    return {
        "status": "ok",
        "ready": True,
        "service": "notify",
        "delivery": _public_delivery_state(),
    }


@app.post("/notify")
def notify(payload: dict) -> dict:
    message = str(payload.get("message", "")).strip()
    state = _public_delivery_state()
    if not message:
        return {"status": "ignored", "delivery": state}
    common_notify.send_message(message)
    return {"status": "ok", "delivery": state}
