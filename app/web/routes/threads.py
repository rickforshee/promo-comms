from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from sqlalchemy import create_engine, text
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


def _load_status_maps() -> tuple[dict, dict]:
    """Load job and PO status descriptions from Pace DB."""
    engine = create_engine(config.PACE_DB_URL)
    with engine.connect() as conn:
        job_rows = conn.execute(text("SELECT sysstatusid, sysdescription FROM jobstatus")).fetchall()
        po_rows  = conn.execute(text("SELECT sysstatusid, sysdescription FROM postatus")).fetchall()
    job_map = {str(r.sysstatusid): r.sysdescription for r in job_rows}
    po_map  = {str(r.sysstatusid): r.sysdescription for r in po_rows}
    return job_map, po_map


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

    thread_data = []
    for thread in threads:
        latest_email = (
            db.query(Email)
            .filter(Email.thread_id == thread.id)
            .order_by(Email.received_at.desc())
            .first()
        )
        job_count = db.query(ThreadJobLink).filter(ThreadJobLink.thread_id == thread.id).count()
        po_count  = db.query(ThreadPOLink).filter(ThreadPOLink.thread_id == thread.id).count()
        thread_data.append({
            "thread":       thread,
            "latest_email": latest_email,
            "job_count":    job_count,
            "po_count":     po_count,
        })

    return templates.TemplateResponse("threads/list.html", {
        "request":      request,
        "current_user": current_user,
        "threads":      thread_data,
        "page":         page,
        "total":        total,
        "page_size":    page_size,
        "total_pages":  (total + page_size - 1) // page_size,
    })


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

    # Load status lookup maps from Pace DB
    try:
        job_status_map, po_status_map = _load_status_maps()
    except Exception:
        job_status_map, po_status_map = {}, {}

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
            "status_label":  job_status_map.get(str(job.admin_status), job.admin_status or "—"),
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

        # Prefer company name from vendor cache if available
        if vendor:
            vendor_name = vendor.company_name or (
                ((vendor.contact_first_name or "") + " " + (vendor.contact_last_name or "")).strip()
            ) or po.vendor_id or "—"
        else:
            vendor_name = po.vendor_id or "—"

        pos.append({
            "po":           po,
            "status_label": po_status_map.get(str(po.order_status), po.order_status or "—"),
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
