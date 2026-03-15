from datetime import datetime, timedelta, date
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.web.auth import get_current_user, get_db
from app.models import (
    Email, EmailDirection, Thread, ThreadStatus, User,
    ThreadJobLink, ThreadPOLink, PacePOCache, PaceVendorCache,
    PaceJobCache, PaceCustomerCache,
)

router = APIRouter()
templates: Jinja2Templates = None


def set_templates(t: Jinja2Templates):
    global templates
    templates = t


def _parse_date(s: str | None) -> date | None:
    try:
        return date.fromisoformat(s) if s else None
    except (ValueError, TypeError):
        return None


@router.get("/reports", response_class=HTMLResponse)
def reports(
    request: Request,
    date_from: str | None = None,
    date_to: str | None = None,
    stale_days: int = 7,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    df = _parse_date(date_from)
    dt = _parse_date(date_to)

    # Default to last 30 days if no range specified
    if not df and not dt:
        df = (datetime.utcnow() - timedelta(days=30)).date()
        dt = datetime.utcnow().date()
        date_from = df.isoformat()
        date_to   = dt.isoformat()

    df_dt = datetime(df.year, df.month, df.day) if df else None
    dt_dt = datetime(dt.year, dt.month, dt.day, 23, 59, 59) if dt else None

    # ── Volume by direction ────────────────────────────────────────────────
    dir_q = db.query(Email.direction, func.count(Email.id).label("cnt")).group_by(Email.direction)
    if df_dt:
        dir_q = dir_q.filter(Email.received_at >= df_dt)
    if dt_dt:
        dir_q = dir_q.filter(Email.received_at <= dt_dt)
    direction_counts = {row.direction: row.cnt for row in dir_q.all()}
    inbound_count  = direction_counts.get(EmailDirection.inbound,  0)
    outbound_count = direction_counts.get(EmailDirection.outbound, 0)

    # ── Volume by team member (outbound emails sent) ───────────────────────
    by_user_q = (
        db.query(User.id, User.display_name, User.email, func.count(Email.id).label("cnt"))
        .join(Email, (Email.sender_email == User.email) |
                     (Email.sender_name == User.display_name))
        .filter(Email.direction == EmailDirection.outbound)
    )
    if df_dt:
        by_user_q = by_user_q.filter(Email.received_at >= df_dt)
    if dt_dt:
        by_user_q = by_user_q.filter(Email.received_at <= dt_dt)
    by_user = (
        by_user_q
        .group_by(User.id, User.display_name, User.email)
        .order_by(func.count(Email.id).desc())
        .all()
    )

    # ── Volume by vendor (via PO links) ───────────────────────────────────
    vendor_q = (
        db.query(PaceVendorCache.company_name, func.count(Email.id).label("cnt"))
        .join(Thread, Thread.id == Email.thread_id)
        .join(ThreadPOLink, ThreadPOLink.thread_id == Thread.id)
        .join(PacePOCache, PacePOCache.po_number == ThreadPOLink.po_number)
        .join(PaceVendorCache, PaceVendorCache.vendor_id == PacePOCache.vendor_id)
        .filter(PaceVendorCache.company_name.isnot(None))
    )
    if df_dt:
        vendor_q = vendor_q.filter(Email.received_at >= df_dt)
    if dt_dt:
        vendor_q = vendor_q.filter(Email.received_at <= dt_dt)
    by_vendor = (
        vendor_q
        .group_by(PaceVendorCache.vendor_id, PaceVendorCache.company_name)
        .order_by(func.count(Email.id).desc())
        .limit(20)
        .all()
    )

    # ── Daily volume (for sparkline table) ────────────────────────────────
    daily_q = (
        db.query(
            func.date_trunc("day", Email.received_at).label("day"),
            Email.direction,
            func.count(Email.id).label("cnt"),
        )
        .group_by("day", Email.direction)
        .order_by("day")
    )
    if df_dt:
        daily_q = daily_q.filter(Email.received_at >= df_dt)
    if dt_dt:
        daily_q = daily_q.filter(Email.received_at <= dt_dt)
    daily_rows = daily_q.all()

    daily: dict[str, dict] = {}
    for row in daily_rows:
        key = row.day.strftime("%Y-%m-%d") if row.day else "unknown"
        if key not in daily:
            daily[key] = {"inbound": 0, "outbound": 0}
        daily[key][row.direction.value] += row.cnt
    daily_sorted = sorted(daily.items())

    # ── Stale threads ──────────────────────────────────────────────────────
    stale_days = max(1, stale_days)
    stale_cutoff = datetime.utcnow() - timedelta(days=stale_days)
    stale_raw = (
        db.query(Thread, User)
        .outerjoin(User, User.id == Thread.assigned_to)
        .filter(Thread.status.in_([ThreadStatus.open, ThreadStatus.pending]))
        .filter(Thread.updated_at < stale_cutoff)
        .order_by(Thread.updated_at.asc())
        .all()
    )

    # Batch-resolve customer names for stale threads
    stale_thread_ids = [t.id for t, _ in stale_raw]
    stale_customer_map: dict[int, str] = {}
    if stale_thread_ids:
        # Job links first
        job_links = (
            db.query(ThreadJobLink.thread_id, PaceJobCache.customer_id)
            .join(PaceJobCache, PaceJobCache.job_number == ThreadJobLink.job_number)
            .filter(ThreadJobLink.thread_id.in_(stale_thread_ids))
            .all()
        )
        cust_id_map: dict[int, str] = {}
        for tid, cid in job_links:
            if tid not in cust_id_map and cid:
                cust_id_map[tid] = cid

        # PO links for threads with no job link
        po_only = [tid for tid in stale_thread_ids if tid not in cust_id_map]
        if po_only:
            po_links = (
                db.query(ThreadPOLink.thread_id, PacePOCache.customer_id)
                .join(PacePOCache, PacePOCache.po_number == ThreadPOLink.po_number)
                .filter(ThreadPOLink.thread_id.in_(po_only))
                .all()
            )
            for tid, cid in po_links:
                if tid not in cust_id_map and cid:
                    cust_id_map[tid] = cid

        all_cust_ids = set(cust_id_map.values())
        if all_cust_ids:
            custs = (
                db.query(PaceCustomerCache)
                .filter(PaceCustomerCache.customer_id.in_(all_cust_ids))
                .all()
            )
            cust_name_map = {c.customer_id: c.cust_name for c in custs if c.cust_name}
            for tid, cid in cust_id_map.items():
                stale_customer_map[tid] = cust_name_map.get(cid, "")

    now = datetime.utcnow()
    stale_threads = [
        {
            "thread":        t,
            "assigned_user": u,
            "customer_name": stale_customer_map.get(t.id, ""),
            "days_stale":    (now - t.updated_at).days if t.updated_at else None,
        }
        for t, u in stale_raw
    ]

    return templates.TemplateResponse("reports.html", {
        "request":        request,
        "current_user":   current_user,
        "date_from":      date_from or "",
        "date_to":        date_to or "",
        "inbound_count":  inbound_count,
        "outbound_count": outbound_count,
        "total_count":    inbound_count + outbound_count,
        "by_user":        by_user,
        "by_vendor":      by_vendor,
        "daily":          daily_sorted,
        "stale_threads":  stale_threads,
        "stale_days":     stale_days,
    })
