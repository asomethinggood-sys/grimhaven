"""Browser-playable demo console — the exact same game engine and dispatcher
that powers the Telegram bot, rendered as a chat-style UI.

Run:  python run_demo.py     →  http://localhost:8000
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..bot.handlers.callbacks import _dispatch
from ..bot.handlers.common import Ctx
from ..db.storage import Storage, bootstrap_world
from ..engine.models import new_user_doc, utcnow
from ..config import settings

STATIC = Path(__file__).resolve().parent / "static"

DEMO_ADMINS = {7777}  # the demo persona with God-Mode access


def create_app(database_path: Path | None = None) -> FastAPI:
    storage = Storage(database_path or settings.database_path)
    bootstrap_world(storage)
    ctx = Ctx(storage, admin_ids=DEMO_ADMINS | settings.admin_ids)

    app = FastAPI(title="Grimhaven — War of the Immortals", docs_url="/api/docs")
    app.state.ctx = ctx
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html")

    @app.post("/api/join")
    async def join(request: Request):
        data = await request.json()
        uid = int(data.get("user_id") or 1)
        user = storage.get_user(uid)
        created = False
        if not user:
            user = new_user_doc(uid, data.get("username") or f"cultivator_{uid}",
                                data.get("language") or "fa")
            storage.save_user(user)
            created = True
        return {"user_id": uid, "created": created, "language": user["account"]["language"]}

    @app.post("/api/action")
    async def action(request: Request):
        data = await request.json()
        uid = int(data.get("user_id") or 1)
        user = storage.get_user(uid)
        if not user:
            user = new_user_doc(uid, f"cultivator_{uid}")
            storage.save_user(user)
        settle_res = ctx.settle(user, now=utcnow())
        act = data.get("action") or "menu"
        arg = data.get("arg") or ""
        # demo-only: admin drops via button works through the normal dispatcher
        text, kb = _dispatch(ctx, user, act, arg, settle_res)
        ctx.save(user)
        keyboard = []
        if kb is not None:
            keyboard = [[{"label": b.text, "data": b.callback_data} for b in row]
                        for row in kb.inline_keyboard]
        return JSONResponse({
            "text": text,
            "keyboard": keyboard,
            "settle": {"gained": settle_res.get("gained", 0),
                       "events": settle_res.get("events", [])},
        })

    return app
