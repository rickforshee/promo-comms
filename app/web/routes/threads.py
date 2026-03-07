from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from sqlalchemy import create_engine, func, text
from sqlalchemy.orm import Session

from app import config
from app.models import (
    Thread, Email, ThreadJobLink, ThreadPOLink,
    PaceJobCache, PacePOCache, PaceVendorCache, PaceCustomerCache,
)
from app.web.auth import get_current_user, get_db
from app.models import User

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")

PACE_BASE = "https://vicepace.vividimpact.com/epace/company:public/object"

# ─── Status map cache (loaded once at startup) ────────────────────────────────

_job_status_map: dict[str, str] = {}
_po_status_map:  dict[str, str] = {}


def load_status_maps() -> None:
    """Called once at app startup to populate status label caches."""
    global _job_status_map, _po_status_map
    try:
        engine = create_engine(config.PACE_DB_URL)
        with engine.connect() as conn:
            job_rows = conn.execute(text("SELECT sysstatusid, sysdescription FROM jobstatus")).fetchall()
            po_rows  = conn.execute(text("SELECT sysstatusid, sysdescription FROM postatus")).fetchall()
        _job_status_map = {str(r.sysstatusid): r.sysdescription for r in job_rows}
        _po_status_map  = {str(r.sysstatusid): r.sysdescription for r in po_rows}
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Could not load status maps: {e}")


# ─── Thread list ──────────────────────────────────────────────────────────────

@router.get("/threads", response_class=HTMLResponse)
async def thread_list(
    request: Request,
    page: int = 1,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    page_size = 50
    offset = (page - 1) * page_size

    total = db.query(Thread).count()
    threads = (
        db.query(Thread)
        .order_by(Thread.id.desc())
        .offset(offset)
        .limit(page_size)
        .all()
    )

    thread_ids = [t.id for t in threads]

    # Latest email per thread — single query
    latest_email_subq = (
        db.query(
            Email.thread_id,
            func.max(Email.received_at).label("max_received_at"),
        )
        .filter(Email.thread_id.in_(thread_ids))
        .group_by(Email.thread_id)
        .subquery()
    )
    latest_emails = (
        db.query(Email)
        .join(
            latest_email_subq,
            (Email.thread_id == latest_email_subq.c.thread_id) &
            (Email.received_at == latest_email_subq.c.max_received_at),
        )
        .all()
    )
    latest_email_map = {e.thread_id: e for e in latest_emails}

    # Job counts per thread — single query
    job_counts = (
        db.query(ThreadJobLink.thread_id, func.count().label("cnt"))
        .filter(ThreadJobLink.thread_id.in_(thread_ids))
        .group_by(ThreadJobLink.thread_id)
        .all()
    )
    job_count_map = {row.thread_id: row.cnt for row in job_counts}

    # PO counts per thread — single query
    po_counts = (
        db.query(ThreadPOLink.thread_id, func.count().label("cnt"))
        .filter(ThreadPOLink.thread_id.in_(thread_ids))
        .group_by(ThreadPOLink.thread_id)
        .all()
    )
    po_count_map = {row.thread_id: row.cnt for row in po_counts}

    thread_data = [
        {
            "thread":       t,
            "latest_email": latest_email_map.get(t.id),
            "job_count":    job_count_map.get(t.id, 0),
            "po_count":     po_count_map.get(t.id, 0),
        }
        for t in threads
    ]

    return templates.TemplateResponse("threads/list.html", {
        "request":      request,
        "current_user": current_user,
        "threads":      thread_data,
        "page":         page,
        "total":        total,
        "page_size":    page_size,
        "total_pages":  (total + page_size - 1) // page_size,
    })


# ─── Thread detail ────────────────────────────────────────────────────────────

@router.get("/threads/{thread_id}", response_class=HTMLResponse)
async def thread_detail(
    request: Request,
    thread_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    thread = db.query(Thread).filter(Thread.id == thread_id).first()
    if not thread:
        from fastapi.responses import Response
        return Response(status_code=404)

    emails = (
        db.query(Email)
        .filter(Email.thread_id == thread_id)
        .order_by(Email.received_at.asc())
        .all()
    )

    # Linked jobs — enrich with customer name, status label, Pace URL
    job_links = db.query(ThreadJobLink).filter(ThreadJobLink.thread_id == thread_id).all()
    jobs = []
    for link in job_links:
        job = db.query(PaceJobCache).filter(PaceJobCache.job_number == link.job_number).first()
        if not job:
            continue
        customer = db.query(PaceCustomerCache).filter(
            PaceCustomerCache.customer_id == job.customer_id
        ).first() if job.customer_id else None

        jobs.append({
            "job":           job,
            "status_label":  _job_status_map.get(str(job.admin_status), job.admin_status or "—"),
            "customer_name": customer.cust_name if customer else (job.customer_id or "—"),
            "pace_url":      f"{PACE_BASE}/Job/detail/{job.job_number}",
        })

    # Linked POs — enrich with vendor name, status label, Pace URL
    po_links = db.query(ThreadPOLink).filter(ThreadPOLink.thread_id == thread_id).all()
    pos = []
    for link in po_links:
        po = db.query(PacePOCache).filter(PacePOCache.po_number == link.po_number).first()
        if not po:
            continue
        vendor = db.query(PaceVendorCache).filter(
            PaceVendorCache.vendor_id == po.vendor_id
        ).first() if po.vendor_id else None

        if vendor:
            vendor_name = vendor.company_name or (
                ((vendor.contact_first_name or "") + " " + (vendor.contact_last_name or "")).strip()
            ) or po.vendor_id or "—"
        else:
            vendor_name = po.vendor_id or "—"

        pos.append({
            "po":           po,
            "status_label": _po_status_map.get(str(po.order_status), po.order_status or "—"),
            "vendor_name":  vendor_name,
            "pace_url":     f"{PACE_BASE}/PurchaseOrder/detail/{po.pace_internal_id}"
                            if po.pace_internal_id else None,
        })

    return templates.TemplateResponse("threads/detail.html", {
        "request":      request,
        "current_user": current_user,
        "thread":       thread,
        "emails":       emails,
        "jobs":         jobs,
        "pos":          pos,
    })