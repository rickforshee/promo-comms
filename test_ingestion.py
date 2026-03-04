#!/usr/bin/env python3
"""
Smoke test for the email ingestion pipeline.
Fetches one page of messages from the live mailbox and reports results.
"""
import logging
from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)

from app.database import SessionLocal
from app.services.ingestion import IngestionService
from app.models import Email, Thread, ThreadJobLink, ThreadPOLink

def main():
    db = SessionLocal()
    try:
        print("\n── Running ingestion (1 page, inbox) ──────────────────────")
        svc     = IngestionService(db=db)
        summary = svc.run(folder="inbox", max_pages=1)

        print(f"\n── Summary ─────────────────────────────────────────────────")
        print(f"  Ingested : {summary['ingested']}")
        print(f"  Skipped  : {summary['skipped']}")
        print(f"  Failed   : {summary['failed']}")

        print(f"\n── Database counts ─────────────────────────────────────────")
        print(f"  Threads        : {db.query(Thread).count()}")
        print(f"  Emails         : {db.query(Email).count()}")
        print(f"  Job links      : {db.query(ThreadJobLink).count()}")
        print(f"  PO links       : {db.query(ThreadPOLink).count()}")

        print(f"\n── Sample emails ───────────────────────────────────────────")
        emails = db.query(Email).order_by(Email.received_at.desc()).limit(5).all()
        for e in emails:
            print(f"  [{e.received_at}] {e.sender_email} — {e.subject[:60]}")

    finally:
        db.close()

if __name__ == "__main__":
    main()