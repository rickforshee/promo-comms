from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from sqlalchemy import create_engine, func, or_, text, union
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

# ─── Status map cache ─────────────────────────────────────────────────────────

_job_status_map: dict[str, str] = {}
_po_status_map:  dict[str, str] = {}


def load_status_maps() -> None:
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


# ─── Shared helpers ───────────────────────────────────────────────────────────

def _enrich_threads(thread_ids: list[int], db: Session) -> list[dict]:
    """Fetch and enrich a list of thread IDs for display."""
    if not thread_ids:
        return []

    threads = (
        db.query(Thread)
        .filter(Thread.id.in_(thread_ids))
        .order_by(Thread.id.desc())
        .all()
    )

    # Latest email per thread
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

    # Job/PO counts
    job_counts = (
        db.query(ThreadJobLink.thread_id, func.count().label("cnt"))
        .filter(ThreadJobLink.thread_id.in_(thread_ids))
        .group_by(ThreadJobLink.thread_id)
        .all()
    )
    job_count_map = {r.thread_id: r.cnt for r in job_counts}

    po_counts = (
        db.query(ThreadPOLink.thread_id, func.count().label("cnt"))
        .filter(ThreadPOLink.thread_id.in_(thread_ids))
        .group_by(ThreadPOLink.thread_id)
        .all()
    )
    po_count_map = {r.thread_id: r.cnt for r in po_counts}

    return [
        {
            "thread":       t,
            "latest_email": latest_email_map.get(t.id),
            "job_count":    job_count_map.get(t.id, 0),
            "po_count":     po_count_map.get(t.id, 0),
        }
        for t in threads
    ]


def _search_thread_ids(q: str, link_filter: str, db: Session, limit: int = 200) -> list[int]:
    """
    Return thread IDs matching query string across PO#, job#, subject, vendor name.
    link_filter: "all" | "has_po" | "has_job" | "unlinked"
    """
    matched_ids: set[int] = set()
    pattern = f"%{q}%" if q else "%"

    if q:
        # PO number match
        po_matches = (
            db.query(ThreadPOLink.thread_id)
            .filter(ThreadPOLink.po_number.ilike(pattern))
            .all()
        )
        matched_ids.update(r.thread_id for r in po_matches)

        # Job number match
        job_matches = (
            db.query(ThreadJobLink.thread_id)
            .filter(ThreadJobLink.job_number.ilike(pattern))
            .all()
        )
        matched_ids.update(r.thread_id for r in job_matches)

        # Subject match
        subj_matches = (
            db.query(Thread.id)
            .filter(Thread.subject.ilike(pattern))
            .all()
        )
        matched_ids.update(r.id for r in subj_matches)

        # Vendor name match — find PO numbers for matching vendors, then thread IDs
        vendor_ids = (
            db.query(PaceVendorCache.vendor_id)
            .filter(
                or_(
                    PaceVendorCache.company_name.ilike(pattern),
                    (PaceVendorCache.contact_first_name + " " + PaceVendorCache.contact_last_name).ilike(pattern),
                )
            )
            .all()
        )
        if vendor_ids:
            vid_list = [r.vendor_id for r in vendor_ids]
            po_nums = (
                db.query(PacePOCache.po_number)
                .filter(PacePOCache.vendor_id.in_(vid_list))
                .all()
            )
            if po_nums:
                pn_list = [r.po_number for r in po_nums]
                vendor_thread_matches = (
                    db.query(ThreadPOLink.thread_id)
                    .filter(ThreadPOLink.po_number.in_(pn_list))
                    .all()
                )
                matched_ids.update(r.thread_id for r in vendor_thread_matches)

    # Apply link filter
    if link_filter == "has_po":
        po_thread_ids = {r.thread_id for r in db.query(ThreadPOLink.thread_id).distinct().all()}
        if q:
            matched_ids &= po_thread_ids
        else:
            matched_ids = po_thread_ids
    elif link_filter == "has_job":
        job_thread_ids = {r.thread_id for r in db.query(ThreadJobLink.thread_id).distinct().all()}
        if q:
            matched_ids &= job_thread_ids
        else:
            matched_ids = job_thread_ids
    elif link_filter == "unlinked":
        po_thread_ids   = {r.thread_id for r in db.query(ThreadPOLink.thread_id).distinct().all()}
        job_thread_ids  = {r.thread_id for r in db.query(ThreadJobLink.thread_id).distinct().all()}
        linked_ids      = po_thread_ids | job_thread_ids
        if q:
            matched_ids -= linked_ids
        else:
            # All unlinked threads
            all_thread_ids = {r.id for r in db.query(Thread.id).all()}
            matched_ids = all_thread_ids - linked_ids
    else:
        # "all" with no query — return empty (caller handles default inbox)
        if not q:
            return []

    return list(matched_ids)[:limit]


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
    thread_data = _enrich_threads(thread_ids, db)

    return templates.TemplateResponse("threads/list.html", {
        "request":      request,
        "current_user": current_user,
        "threads":      thread_data,
        "page":         page,
        "total":        total,
        "page_size":    page_size,
        "total_pages":  (total + page_size - 1) // page_size,
        "query":        "",
        "link_filter":  "all",
    })


# ─── Search endpoint (HTMX partial) ──────────────────────────────────────────

@router.get("/threads/search", response_class=HTMLResponse)
async def thread_search(
    request: Request,
    q: str = "",
    link_filter: str = "all",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    q = q.strip()

    # No query and no filter — return default inbox (first 50, newest first)
    if not q and link_filter == "all":
        threads = (
            db.query(Thread)
            .order_by(Thread.id.desc())
            .limit(50)
            .all()
        )
        thread_ids = [t.id for t in threads]
        thread_data = _enrich_threads(thread_ids, db)
        total = db.query(Thread).count()
    else:
        thread_ids = _search_thread_ids(q, link_filter, db)
        thread_data = _enrich_threads(thread_ids, db)
        total = len(thread_data)

    return templates.TemplateResponse("threads/results_partial.html", {
        "request":     request,
        "threads":     thread_data,
        "total":       total,
        "query":       q,
        "link_filter": link_filter,
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
