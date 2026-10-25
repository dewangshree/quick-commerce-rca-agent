"""FastAPI application: REST API, auth, audit logging, request IDs."""
import asyncio
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from qc_rca.analytics.service import NotFoundError
from qc_rca.api.routes import admin, analytics, investigations, knowledge, legacy
from qc_rca.config import get_settings
from qc_rca.db.models import AuditLog
from qc_rca.db.session import get_readonly_engine, session_scope
from qc_rca.llm.ollama import ollama_status
from qc_rca.observability import configure_logging, get_logger, log_event, request_id_var
from qc_rca.retention import purge_expired

log = get_logger("api")


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Purge expired history at startup, then every RETENTION_INTERVAL_HOURS."""
    interval = get_settings().retention_interval_hours * 3600

    async def loop():
        while True:
            try:
                await asyncio.to_thread(purge_expired)
            except Exception:  # noqa: BLE001 - retention must never take the API down
                log.exception("retention purge failed")
            await asyncio.sleep(interval)

    task = asyncio.create_task(loop())
    try:
        yield
    finally:
        task.cancel()


def create_app() -> FastAPI:
    s = get_settings()
    configure_logging(s.log_level)
    app = FastAPI(
        lifespan=lifespan,
        title="Quick-Commerce AI Operations Intelligence API",
        version="2.0.0",
        description="Read-only SLA analytics, deterministic RCA, local RAG and LangGraph investigations. "
                    "Authenticate with the `X-API-Key` header.",
        # No public interactive docs in production.
        docs_url=None if s.env == "production" else "/docs",
        redoc_url=None if s.env == "production" else "/redoc",
        openapi_url=None if s.env == "production" else "/openapi.json",
    )
    @app.middleware("http")
    async def request_context(request: Request, call_next):
        rid = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        if len(rid) > 36:
            rid = str(uuid.uuid4())
        token = request_id_var.set(rid)
        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
        except Exception:  # noqa: BLE001 - answer here so the CORS middleware still adds its headers
            log.exception("unhandled error")
            response = JSONResponse(status_code=500, content={"detail": "Internal server error", "request_id": rid})
        try:
            status = response.status_code
            response.headers["X-Request-ID"] = rid
            return response
        finally:
            latency = round((time.perf_counter() - start) * 1000, 1)
            log_event(log, "request", method=request.method, path=request.url.path, status=status, latency_ms=latency)
            if request.url.path.startswith("/api/") or request.url.path == "/chat":
                await run_in_threadpool(_audit, request, rid, status, latency)
            request_id_var.reset(token)

    # Added last so it is the outermost middleware: error responses carry CORS headers too,
    # otherwise the browser reports a 500 as "Failed to fetch".
    app.add_middleware(CORSMiddleware, allow_origins=s.cors_origins, allow_credentials=False,
                       allow_methods=["GET", "POST"], allow_headers=["X-API-Key", "Content-Type", "X-Request-ID"])

    @app.exception_handler(NotFoundError)
    async def not_found(_: Request, exc: NotFoundError):
        return JSONResponse(status_code=404, content={"detail": str(exc), "suggestions": exc.suggestions})

    @app.exception_handler(RequestValidationError)
    async def invalid(_: Request, exc: RequestValidationError):
        errs = [{"loc": e["loc"], "msg": e["msg"]} for e in exc.errors()]
        return JSONResponse(status_code=422, content={"detail": "Invalid request", "errors": errs})

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception):
        log.exception("unhandled error")
        return JSONResponse(status_code=500, content={"detail": "Internal server error",
                                                      "request_id": request_id_var.get()})

    @app.get("/health", tags=["system"])
    def health():
        db_ok = True
        try:
            with get_readonly_engine().connect() as c:
                c.execute(text("SELECT 1"))
        except Exception:  # noqa: BLE001
            db_ok = False
        o = ollama_status()
        return {"status": "ok" if db_ok else "degraded", "database": db_ok,
                "ollama": {k: o.get(k) for k in ("reachable", "llm_model", "llm_installed", "embedding_model",
                                                 "embedding_installed")},
                "auth_enabled": s.auth_enabled}

    app.include_router(analytics.router)
    app.include_router(investigations.router)
    app.include_router(knowledge.router)
    app.include_router(admin.router)
    app.include_router(legacy.router)
    return app


def _audit(request: Request, rid: str, status: int, latency: float) -> None:
    principal = getattr(request.state, "principal", None)
    try:
        with session_scope() as s:
            s.add(AuditLog(
                request_id=rid, user_name=getattr(principal, "name", None),
                role=getattr(getattr(principal, "role", None), "value", None),
                action=getattr(request.state, "audit_action", request.url.path.split("/")[3]
                               if request.url.path.count("/") >= 3 else request.url.path),
                method=request.method, path=request.url.path[:255], status_code=status, latency_ms=latency,
                client_ip=request.client.host if request.client else None,
                details=getattr(request.state, "audit_details", None),
            ))
    except Exception:  # noqa: BLE001 - audit failure must not break the request
        log.exception("audit log write failed")


app = create_app()
