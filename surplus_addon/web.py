import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict
from starlette.middleware.trustedhost import TrustedHostMiddleware


class Control(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["start", "pause", "main_feed", "new_demo", "advance_demo", "simulate_cycle"]


class AISettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str
    enabled: bool


def create_app(service):
    token = secrets.token_urlsafe(32)

    @asynccontextmanager
    async def lifespan(app):
        await service.start()
        try:
            yield
        finally:
            await service.stop()

    app = FastAPI(title="Surplus Research Add-on", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])

    @app.middleware("http")
    async def local_control(request: Request, call_next):
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            if not secrets.compare_digest(request.headers.get("x-control-token", ""), token):
                return Response("Local research control token required", status_code=403)
            origin = request.headers.get("origin")
            if origin and origin != f"{request.url.scheme}://{request.headers.get('host')}":
                return Response("Cross-origin research control rejected", status_code=403)
        response = await call_next(request)
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; "
                "connect-src 'self'; frame-ancestors 'none'",
            }
        )
        return response

    assets = Path(__file__).parent / "web"
    app.mount("/static", StaticFiles(directory=assets), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return (assets / "index.html").read_text().replace("__CONTROL_TOKEN__", token)

    @app.get("/api/state")
    async def state():
        return service.state()

    @app.get("/api/export")
    async def export():
        return {
            "evidence_note": "Research only; synthetic cycles are not IBKR executions",
            "decisions": service.ledger.history(),
            "cycles": service.ledger.cycles(),
        }

    @app.post("/api/control")
    async def control(body: Control):
        try:
            return await service.control(body.action)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @app.post("/api/ai/settings")
    async def ai_settings(body: AISettings):
        try:
            return service.advisor.configure(body.model, body.enabled)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    return app
