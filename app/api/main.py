from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import dashboard, insights, portfolio
from app.config import settings
from app.store.db import create_all

logging.basicConfig(level=logging.INFO)

WEB = Path(__file__).resolve().parent.parent / "web"


@asynccontextmanager
async def lifespan(_: FastAPI):
    create_all()
    yield


app = FastAPI(title="Portfolio Manager", version="1.0.0", lifespan=lifespan)

app.include_router(portfolio.router)
app.include_router(dashboard.router)
app.include_router(insights.router)


@app.get("/api/config")
def config() -> dict:
    """What the frontend needs to know before it renders. The anon key is a public
    client key by design -- Supabase expects it in the browser."""
    return {
        "auth_enabled": settings().auth_enabled,
        "supabase_url": settings().supabase_url,
        "supabase_anon_key": settings().supabase_anon_key,
    }


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB / "index.html")


app.mount("/static", StaticFiles(directory=WEB), name="static")
