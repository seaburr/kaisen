import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select

from . import auth, config, db
from .github import RealGitHub
from .llm import AnthropicClient, ModelError
from .models import Run
from .routes import router
from .worker import Worker

CSP = ("default-src 'none'; style-src 'self'; img-src 'self'; form-action 'self'; "
       "base-uri 'none'; frame-ancestors 'none'")


def default_model_client(rs):
    if not rs.anthropic_api_key:
        raise ModelError("Anthropic API key is not set (Settings)")
    return AnthropicClient(rs.anthropic_api_key)


def create_app(model_client_factory=default_model_client, github_factory=RealGitHub,
               start_worker: bool | None = None) -> FastAPI:
    cfg = config.load()
    db.configure(cfg.database_url, cfg.database_ca_cert)
    run_worker = cfg.worker_enabled if start_worker is None else start_worker
    logging.basicConfig(level=logging.INFO)

    @asynccontextmanager
    async def lifespan(app):
        auth.ensure_setup_code()
        worker = Worker(app.state.model_client_factory) if run_worker else None
        if worker:
            worker.start()
        yield
        if worker:
            worker.stop.set()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.model_client_factory = model_client_factory
    app.state.github_factory = github_factory
    app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

    @app.middleware("http")
    async def session_and_headers(request: Request, call_next):
        request.state.sess = auth.load_session(request)
        resp = await call_next(request)
        resp.set_cookie(auth.COOKIE, request.state.sess.dumps(), max_age=auth.SESSION_AGE, httponly=True,
                        secure=config.get().cookie_secure, samesite="strict")
        resp.headers["Content-Security-Policy"] = CSP
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.get("/healthz")
    def healthz():  # static: no DB access
        return PlainTextResponse("ok")

    @app.get("/metrics")
    def metrics():
        lines = ["kaisen_up 1"]
        try:
            with db.session_scope() as s:
                for status, n in s.execute(select(Run.status, func.count()).group_by(Run.status)):
                    lines.append(f'kaisen_runs{{status="{status}"}} {n}')
        except Exception:
            lines.append("kaisen_db_up 0")
        return Response("\n".join(lines) + "\n", media_type="text/plain")

    app.include_router(router)
    return app


def app_factory() -> FastAPI:
    return create_app()
