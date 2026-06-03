"""
CoreX Sales Service — HTTP entrypoint.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

_ROOT = Path(__file__).resolve().parents[2]
from src.config.env import load_project_env

load_project_env()

from src.log import configure_logging, get_logger, log_action

configure_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Uvicorn configures logging after import — re-apply structured formatter at startup.
    configure_logging(force=True)

    from src.db.connection import resolve_database_url
    from src.db.migrate import prepare_database_if_needed
    from src.db.pool import close_pool

    if resolve_database_url():
        try:
            result = prepare_database_if_needed()
            if result and result.get("applied"):
                log_action(
                    logger,
                    logging.INFO,
                    "STARTUP",
                    "db/migrate",
                    result,
                    traces=[("ok", f"applied {len(result.get('applied', []))} migration(s)")],
                )
        except Exception:
            log_action(
                logger,
                logging.ERROR,
                "STARTUP",
                "db/migrate",
                None,
                traces=[("error", "database preparation failed")],
                exc_info=True,
            )
            raise
    else:
        log_action(
            logger,
            logging.WARNING,
            "STARTUP",
            "db",
            None,
            traces=[("warn", "DATABASE_URL not configured — Postgres features disabled")],
        )
    yield
    close_pool()


def create_app() -> FastAPI:
    from src.api.middleware.request_logging import RequestLoggingMiddleware
    from src.api.routes.leads import router as leads_router
    from src.api.routes.runs import router as runs_router

    app = FastAPI(
        title="CoreX Sales Service",
        version="0.2.0",
        description="Lead discovery and qualification API for CoreXBiz.",
        lifespan=lifespan,
    )

    app.add_middleware(RequestLoggingMiddleware)

    cors_origins = (os.getenv("CORS_ALLOW_ORIGINS") or "*").split(",")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in cors_origins if o.strip()],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health() -> dict[str, object]:
        payload: dict[str, object] = {
            "ok": True,
            "service": "corex-sales-service",
            "env": os.getenv("COREX_SALES_SERVICE_ENV", "local"),
            "postgres_schema": os.getenv("POSTGRES_SCHEMA", "sales-service"),
        }

        from src.db.connection import resolve_database_url

        if not resolve_database_url():
            payload["database"] = "not_configured"
            return payload

        try:
            from src.db.pool import check_connection

            payload["database"] = check_connection()
        except Exception as exc:
            payload["ok"] = False
            payload["database"] = {"ok": False, "error": str(exc)}
            raise HTTPException(status_code=503, detail=payload) from exc

        from src.config.env import google_maps_configured

        payload["google_maps"] = {
            "configured": google_maps_configured(),
        }

        return payload

    app.include_router(runs_router, prefix="/api/v1")
    app.include_router(leads_router, prefix="/api/v1")

    return app


app = create_app()
