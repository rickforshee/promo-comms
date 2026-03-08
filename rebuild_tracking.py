from dotenv import load_dotenv
load_dotenv()

from app.database import SessionLocal
from app.models import Email, ThreadTrackingLink, LinkSource
from app.services.pattern_matcher import extract_all
from sqlalchemy.dialects.postgresql import insert as pg_insert

db = SessionLocal()
emails = db.query(Email).all()
created = skipped = 0

for email in emails:
    matches = extract_all(
        subject=email.subject or '',
        body=email.body_text,
        body_html=email.body_html,
    )
    for t in matches.get('tracking_numbers', []):
        stmt = pg_insert(ThreadTrackingLink).values(
            thread_id       = email.thread_id,
            email_id        = email.id,
            carrier         = t['carrier'],
            tracking_number = t['number'],
            link_source     = LinkSource.auto,
        ).on_conflict_do_nothing(constraint='uq_thread_tracking')
        result = db.execute(stmt)
        if result.rowcount:
            created += 1
        else:
            skipped += 1

db.commit()
print(f'Tracking links created: {created}, skipped (duplicates): {skipped}')
db.close()