"""
main.py — FastAPI application entry point with Enterprise-grade features.
"""
from dotenv import load_dotenv
load_dotenv()

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.config import settings
from src.routes import health, webhook, calendar
from src.utils.logging import setup_logging

# ── Enterprise Logging Setup ──────────────────────────────────────────────────
setup_logging()
logger = logging.getLogger(__name__)

# ── Lifespan (startup / shutdown hooks) ───────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    from src.services.agent_service import agent_service
    logger.info("🏥 Healix Hospital Chatbot starting up...")
    # Warm up LangChain Agent
    try:
        await agent_service.warm_up()
    except Exception as e:
        logger.error(f"Failed to warm up Agent: {e}")
    yield
    logger.info("👋 Healix shutting down.")

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Healix Hospital Chatbot API",
    description="WhatsApp chatbot for Healix hospital — booking, admin, calendar.",
    version="1.1.0",
    lifespan=lifespan,
    docs_url="/docs",
)

# ── Global Cache / State ──────────────────────────────────────────────────────
# (Can extend here for enterprise caching like Redis)

# ── Global Exception Handler ──────────────────────────────────────────────────
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    request_id = getattr(request.state, "request_id", "UNKNOWN")
    logger.error(f"UNHANDLED_ERROR: {str(exc)}", exc_info=exc, extra={"request_id": request_id})
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal Server Error",
            "message": "An unexpected error occurred. Our engineers have been notified.",
            "reference_id": request_id
        }
    )

# ── Enterprise Middleware ─────────────────────────────────────────────────────
@app.middleware("http")
async def enterprise_middleware(request: Request, call_next):
    start_time = time.time()
    request_id = str(uuid.uuid4())
    request.state.request_id = request_id
    
    # Process request
    response = await call_next(request)
    
    # Process metrics
    duration = time.time() - start_time
    
    # Log access with metadata
    logger.info(
        f"ACCESS: {request.method} {request.url.path} - {response.status_code}",
        extra={
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "duration_ms": int(duration * 1000)
        }
    )
    
    # Inject request_id into response headers for traceability
    response.headers["X-Request-ID"] = request_id
    return response

# ── CORS ──────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Request-ID"],
)

# ── Register routes ───────────────────────────────────────────────────────────
app.include_router(health.router)
app.include_router(webhook.router)
app.include_router(calendar.router)
