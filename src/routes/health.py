from fastapi import APIRouter, Response
from src.services import dynamodb as db
import logging
import time

router = APIRouter(prefix="/api")
logger = logging.getLogger(__name__)

@router.get("/health")
async def liveness():
    """
    Check if the process is running.
    """
    return {"status": "ok", "timestamp": int(time.time())}

@router.get("/ready")
async def readiness():
    """
    Check if the database connection is active.
    """
    try:
        # Simple probe: describe the session table
        await db._run(db._sessions_table.get_item, Key={"sessionId": "READY_PROBE"})
        return {"status": "ready", "db": "connected"}
    except Exception as e:
        logger.error(f"Readiness probe failed: {str(e)}")
        return Response(content='{"status": "not_ready", "error": "db_connection_failed"}', 
                        media_type="application/json", 
                        status_code=503)
