#!/usr/bin/env python3
"""
Promo Communications Platform — Background Scheduler

Jobs:
  - realtime_ingestion   : polls inbox every 5 minutes for new messages
  - historical_import    : one-time full backlog ingest (runs once on startup
                           if HISTORICAL_IMPORT_COMPLETE is not set in the DB)
  - pace_cache_refresh   : placeholder; wired up when Pace integration is built

Run directly:
  python -m app.scheduler

Or import and call start() to embed in a larger process.
"""
import logging
import signal
import sys
import time
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED
from dotenv import load_dotenv
from app.services.pace_cache import PaceCacheService

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger("scheduler")

from app.database import SessionLocal
from app.models import ImportSource
from app.services.ingestion import IngestionService


# ─── Configuration ────────────────────────────────────────────────────────────

REALTIME_INTERVAL_MINUTES = 5
HISTORICAL_BATCH_PAGES    = 10   # pages per historical import run (~500 emails)
HISTORICAL_PAGE_SIZE      = 50   # emails per page (Graph API max is 999)
PACE_CACHE_INTERVAL_MINUTES = 30


# ─── Job Functions ────────────────────────────────────────────────────────────

def job_realtime_ingestion():
    """Poll inbox for new messages and ingest them."""
    logger.info("── Realtime ingestion starting ──")
    db = SessionLocal()
    try:
        svc     = IngestionService(db=db)
        summary = svc.run(
            folder="inbox",
            import_source=ImportSource.realtime,
            max_pages=2,  # cap at 2 pages (~100 emails) per poll cycle
        )
        logger.info(
            f"Realtime ingestion done — "
            f"ingested: {summary['ingested']}, "
            f"skipped: {summary['skipped']}, "
            f"failed: {summary['failed']}"
        )
    except Exception as e:
        logger.error(f"Realtime ingestion error: {e}", exc_info=True)
    finally:
        db.close()


def job_historical_import():
    """
    Page through the full inbox backlog and ingest all historical messages.
    Runs in batches — call repeatedly until ingested count drops to 0.
    APScheduler will keep calling this on its interval until we remove the job.
    """
    logger.info("── Historical import batch starting ──")
    db = SessionLocal()
    try:
        svc     = IngestionService(db=db)
        summary = svc.run(
            folder="inbox",
            import_source=ImportSource.historical,
            max_pages=HISTORICAL_BATCH_PAGES,
        )
        logger.info(
            f"Historical batch done — "
            f"ingested: {summary['ingested']}, "
            f"skipped: {summary['skipped']}, "
            f"failed: {summary['failed']}"
        )

        # If nothing new was ingested, the backlog is exhausted
        if summary["ingested"] == 0:
            logger.info(
                "Historical import complete — "
                "no new messages found, removing job from scheduler."
            )
            return "COMPLETE"

    except Exception as e:
        logger.error(f"Historical import error: {e}", exc_info=True)
    finally:
        db.close()


def job_pace_cache_refresh():
    """Refresh all Pace caches: jobs, vendors, customers."""
    logger.info("── Pace cache refresh starting ──")
    db = SessionLocal()
    try:
        svc = PaceCacheService(db)

        jobs = svc.refresh_jobs()
        logger.info(f"Pace cache: jobs={jobs}")

        vendors = svc.refresh_vendors()
        logger.info(f"Pace cache: vendors={vendors}")

        customers = svc.refresh_customers()
        logger.info(f"Pace cache: customers={customers}")

        logger.info(f"Pace cache refresh complete — jobs={jobs}, vendors={vendors}, customers={customers}")
    except Exception as e:
        logger.error(f"Pace cache refresh error: {e}", exc_info=True)
    finally:
        db.close()


# ─── Scheduler Setup ──────────────────────────────────────────────────────────

def build_scheduler(run_historical: bool = True) -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone="America/New_York")

    # Realtime ingestion — every N minutes
    scheduler.add_job(
        job_realtime_ingestion,
        trigger="interval",
        minutes=REALTIME_INTERVAL_MINUTES,
        id="realtime_ingestion",
        name="Realtime inbox poll",
        max_instances=1,       # never overlap
        coalesce=True,         # skip missed runs rather than stacking them
        next_run_time=datetime.now(timezone.utc),  # run immediately on start
    )

    # Historical import — runs every 2 minutes until backlog is exhausted
    if run_historical:
        scheduler.add_job(
            job_pace_cache_refresh,
            trigger="interval",
            minutes=PACE_CACHE_INTERVAL_MINUTES,
            id="pace_cache_refresh",
            name="Pace cache refresh",
            max_instances=1,
            coalesce=True,
            # No next_run_time — first run after 30 minutes
        )

    # Pace cache refresh — every 30 minutes (placeholder)
    scheduler.add_job(
        job_pace_cache_refresh,
        trigger="interval",
        minutes=30,
        id="pace_cache_refresh",
        name="Pace cache refresh",
        max_instances=1,
        coalesce=True,
    )

    return scheduler


def _event_listener(event):
    """Log job completions and errors. Remove historical job when complete."""
    if event.exception:
        logger.error(f"Job {event.job_id} raised an exception.")
    elif event.job_id == "historical_import":
        if event.retval == "COMPLETE":
            try:
                event.scheduler.remove_job("historical_import")
                logger.info("Historical import job removed from scheduler.")
            except Exception:
                pass


# ─── Entry Point ──────────────────────────────────────────────────────────────

def start(run_historical: bool = True):
    scheduler = build_scheduler(run_historical=run_historical)
    scheduler.add_listener(
        lambda e: _event_listener(e),
        EVENT_JOB_EXECUTED | EVENT_JOB_ERROR,
    )

    scheduler.start()
    logger.info(
        f"Scheduler started — "
        f"realtime poll every {REALTIME_INTERVAL_MINUTES}m, "
        f"historical import {'enabled' if run_historical else 'disabled'}"
    )

    # Graceful shutdown on SIGINT / SIGTERM
    def _shutdown(signum, frame):
        logger.info("Shutdown signal received, stopping scheduler...")
        scheduler.shutdown(wait=True)
        logger.info("Scheduler stopped cleanly.")
        sys.exit(0)

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # Keep the main thread alive
    while True:
        time.sleep(30)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Promo comms background scheduler")
    parser.add_argument(
        "--no-historical",
        action="store_true",
        help="Skip historical import, run realtime polling only",
    )
    args = parser.parse_args()

    start(run_historical=not args.no_historical)