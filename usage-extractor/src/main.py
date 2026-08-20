"""FastAPI app: dashboard + JSON API + CSV export."""
import csv
import io
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from .config import settings
from .db import init_db, connect, get_summary, get_logs
from .fetcher import fetch_and_store


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Init DB + start background fetcher on startup."""
    init_db(settings.DB_PATH)
    logger.info("DB initialized at %s", settings.DB_PATH)

    # First fetch immediately, then every N seconds.
    scheduler.add_job(
        fetch_and_store,
        "interval",
        seconds=settings.FETCH_INTERVAL_SECONDS,
        next_run_time=None,  # run at first interval, not immediately
        id="fetcher",
    )
    scheduler.start()
    logger.info(
        "Scheduler started, interval=%ss", settings.FETCH_INTERVAL_SECONDS
    )

    # Kick off one fetch right away so the dashboard isn't empty on first load.
    try:
        await fetch_and_store()
    except Exception as exc:
        logger.warning("Initial fetch failed (non-fatal): %s", exc)

    yield

    scheduler.shutdown(wait=False)
    logger.info("Scheduler stopped")


app = FastAPI(
    title="LiteLLM Usage Extractor",
    description="Pulls per-request usage from LiteLLM and exposes it.",
    version="0.1.0",
    lifespan=lifespan,
)


templates_dir = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(templates_dir))


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    with connect(settings.DB_PATH) as conn:
        summary = get_summary(conn)
        recent = get_logs(conn, limit=20)
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "summary": summary,
            "recent": recent,
        },
    )


@app.get("/api/summary")
async def api_summary():
    with connect(settings.DB_PATH) as conn:
        return get_summary(conn)


@app.get("/api/usage")
async def api_usage(
    limit: int = Query(default=100, ge=1, le=1000),
    model_group: str | None = None,
):
    with connect(settings.DB_PATH) as conn:
        return get_logs(conn, limit=limit, model_group=model_group)


@app.get("/api/export.csv")
async def export_csv():
    with connect(settings.DB_PATH) as conn:
        rows = get_logs(conn, limit=10000)

    buf = io.StringIO()
    if rows:
        writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=usage_logs.csv"},
    )


@app.post("/api/refresh")
async def force_refresh():
    """Manually trigger a fetch (for testing / demos)."""
    result = await fetch_and_store()
    return JSONResponse(result)


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}
