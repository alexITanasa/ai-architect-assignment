"""Background job: poll LiteLLM /spend/logs and persist to SQLite."""
import logging

import httpx

from .config import settings
from .db import connect, insert_log, get_latest_start_time


logger = logging.getLogger(__name__)


async def fetch_and_store() -> dict:
    """
    Poll LiteLLM /spend/logs (unfiltered — the API returns summaries when date
    filters are used) and insert new records. Deduplication happens on
    request_id via INSERT OR IGNORE, and we skip client-side anything older
    than the newest start_time already stored.
    """
    with connect(settings.DB_PATH) as conn:
        cursor = get_latest_start_time(conn)

    url = f"{settings.LITELLM_BASE_URL}/spend/logs"
    headers = {"Authorization": f"Bearer {settings.LITELLM_MASTER_KEY}"}

    logger.info("Fetching /spend/logs (cursor=%s)", cursor)

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            logs = resp.json()
    except httpx.HTTPError as exc:
        logger.error("Failed to fetch /spend/logs: %s", exc)
        return {"fetched": 0, "inserted": 0, "error": str(exc)}

    if not isinstance(logs, list):
        logger.warning("Unexpected response shape: %s", type(logs))
        return {"fetched": 0, "inserted": 0, "error": "unexpected response"}

    # Guard: entries must have a request_id (raw log shape).
    logs = [l for l in logs if isinstance(l, dict) and l.get("request_id")]

    inserted = 0
    with connect(settings.DB_PATH) as conn:
        for log in logs:
            # Cursor-based skip: don't waste an INSERT if we're sure it's old.
            if cursor and (log.get("startTime") or "") < cursor:
                continue
            if insert_log(conn, log):
                inserted += 1

    logger.info("Fetched %d logs, inserted %d new", len(logs), inserted)
    return {"fetched": len(logs), "inserted": inserted}
