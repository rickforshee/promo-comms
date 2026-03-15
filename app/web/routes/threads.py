from datetime import date
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from sqlalchemy import create_engine, func, or_, text
from sqlalchemy.orm import Session, joinedload

from app import config
from app.models import (
    Attachment, Note,
    Thread, Email, ThreadJobLink, ThreadPOLink, ThreadTrackingLink,
    PaceJobCache, PacePOCache, PaceVendorCache, PaceCustomerCache, LinkSource,
)
from app.web.auth import get_current_user, get_db
from app.models import User

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")

def _smart_date(dt):
    if dt is None:
        return "—"
    from datetime import datetime as _dt
    now = _dt.now(dt.tzinfo) if dt.tzinfo else _dt.now()
    delta = (now.date() - dt.date()).days
    if delta == 0:
        return dt.strftime("%-I:%M %p")
    elif delta == 1:
        return "Yesterday " + dt.strftime("%-I:%M %p")
    elif delta < 7:
        return dt.strftime("%a %-I:%M %p")
    elif dt.year == now.year:
        return dt.strftime("%b %d")
    else:
        return dt.strftime("%b %d, %Y")

templates.env.filters["smart_date"] = _smart_date

PACE_BASE    = "https://vicepace.vividimpact.com/epace/company:public/object"
UPS_URL      = "https://www.ups.com/track?tracknum={}"
FEDEX_URL    = "https://www.fedex.com/fedextrack/?tracknumbers={}"

def _parse_date(s):
    """Parse an ISO date string, returning None for empty/invalid input."""
    try:
        from datetime import date
        return date.fromisoformat(s) if s else None
    except (ValueError, TypeError):
        return None


STATUS_LABELS = {
    "open":     "Open",
    "pending":  "Pending",
    "resolved": "Resolved",
    "closed":   "Closed",
}
STATUS_COLORS = {
    "open":     "status-open",
    "pending":  "status-pending",
    "resolved": "status-resolved",
    "closed":   "status-closed",
}

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
        # Share with links router
        from app.web.routes.links import set_status_maps
        set_status_maps(_job_status_map, _po_status_map)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Could not load status maps: {e}")


# ─── Shared helpers ───────────────────────────────────────────────────────────

def _enrich_threads(thread_ids: list[int], db: Session) -> list[dict]:
    if not thread_ids:
        return []

    threads = (
        db.query(Thread)
        .filter(Thread.id.in_(thread_ids))
        .order_by(Thread.id.desc())
        .all()
    )

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

    tracking_counts = (
        db.query(ThreadTrackingLink.thread_id, func.count().label("cnt"))
        .filter(ThreadTrackingLink.thread_id.in_(thread_ids))
        .group_by(ThreadTrackingLink.thread_id)
        .all()
    )
    tracking_count_map = {r.thread_id: r.cnt for r in tracking_counts}

    # Threads with attachments
    attachment_thread_ids = set(
        row[0] for row in
        db.query(Email.thread_id)
        .join(Attachment, Attachment.email_id == Email.id)
        .filter(Email.thread_id.in_(thread_ids))
        .distinct()
        .all()
    )

    # Assigned users — single query for all threads
    assigned_user_ids = [t.assigned_to for t in threads if t.assigned_to]
    assigned_users = {}
    if assigned_user_ids:
        users = db.query(User).filter(User.id.in_(assigned_user_ids)).all()
        assigned_users = {u.id: u for u in users}

    # Customer name — batch resolve, no N+1
    # Priority: job link → PO link (via pace_po_cache.customer_id)

    # Step 1: first job link per thread
    first_job_links = (
        db.query(ThreadJobLink)
        .filter(ThreadJobLink.thread_id.in_(thread_ids))
        .order_by(ThreadJobLink.thread_id, ThreadJobLink.job_number)
        .all()
    )
    first_job_num_map: dict[int, str] = {}
    for jl in first_job_links:
        if jl.thread_id not in first_job_num_map:
            first_job_num_map[jl.thread_id] = jl.job_number

    job_num_to_cust_id: dict[str, str] = {}
    if first_job_num_map:
        _jobs = db.query(PaceJobCache).filter(
            PaceJobCache.job_number.in_(first_job_num_map.values())
        ).all()
        job_num_to_cust_id = {j.job_number: j.customer_id for j in _jobs if j.customer_id}

    # Step 2: for threads with no job link, try first PO link → pace_po_cache.customer_id
    po_only_thread_ids = [tid for tid in thread_ids if tid not in first_job_num_map]
    po_cust_id_map: dict[int, str] = {}
    if po_only_thread_ids:
        first_po_links = (
            db.query(ThreadPOLink)
            .filter(ThreadPOLink.thread_id.in_(po_only_thread_ids))
            .order_by(ThreadPOLink.thread_id, ThreadPOLink.po_number)
            .all()
        )
        first_po_num_map: dict[int, str] = {}
        for pl in first_po_links:
            if pl.thread_id not in first_po_num_map:
                first_po_num_map[pl.thread_id] = pl.po_number

        if first_po_num_map:
            _pos = db.query(PacePOCache).filter(
                PacePOCache.po_number.in_(first_po_num_map.values())
            ).all()
            po_num_to_cust_id = {p.po_number: p.customer_id for p in _pos if p.customer_id}
            for tid, pnum in first_po_num_map.items():
                cid = po_num_to_cust_id.get(pnum)
                if cid:
                    po_cust_id_map[tid] = cid

    # Step 3: batch fetch all needed customer names in one query
    all_cust_ids = set(job_num_to_cust_id.values()) | set(po_cust_id_map.values())
    cust_id_to_name: dict[str, str] = {}
    if all_cust_ids:
        _custs = db.query(PaceCustomerCache).filter(
            PaceCustomerCache.customer_id.in_(all_cust_ids)
        ).all()
        cust_id_to_name = {c.customer_id: c.cust_name for c in _custs if c.cust_name}

    # Step 4: assemble per-thread customer name
    thread_customer_map: dict[int, str] = {}
    for tid in thread_ids:
        if tid in first_job_num_map:
            cid = job_num_to_cust_id.get(first_job_num_map[tid])
        else:
            cid = po_cust_id_map.get(tid)
        thread_customer_map[tid] = cust_id_to_name.get(cid, "") if cid else ""

    return [
        {
            "thread":          t,
            "latest_email":    latest_email_map.get(t.id),
            "job_count":       job_count_map.get(t.id, 0),
            "po_count":        po_count_map.get(t.id, 0),
            "tracking_count":  tracking_count_map.get(t.id, 0),
            "assigned_user":   assigned_users.get(t.assigned_to),
            "status":          (getattr(t, "status", None).value if getattr(t, "status", None) else "open"),
            "has_attachments": t.id in attachment_thread_ids,
            "customer_name":   thread_customer_map.get(t.id, ""),
        }
        for t in threads
    ]


def _tracking_url(carrier: str, number: str) -> str:
    if carrier == "UPS":
        return UPS_URL.format(number)
    return FEDEX_URL.format(number)


def _search_thread_ids(q: str, link_filter: str, db: Session, current_user_id: int | None = None, date_from: date | None = None, date_to: date | None = None, status_filter: str = "all", sender_filter: str = "", assigned_filter: str = "all") -> list[int]:
    matched_ids: set[int] = set()
    pattern = f"%{q}%"

    if q:
        # PO number
        matched_ids.update(
            r.thread_id for r in
            db.query(ThreadPOLink.thread_id).filter(ThreadPOLink.po_number.ilike(pattern)).all()
        )
        # Job number
        matched_ids.update(
            r.thread_id for r in
            db.query(ThreadJobLink.thread_id).filter(ThreadJobLink.job_number.ilike(pattern)).all()
        )
        # Subject
        matched_ids.update(
            r.id for r in
            db.query(Thread.id).filter(Thread.subject.ilike(pattern)).all()
        )
        # Tracking number
        matched_ids.update(
            r.thread_id for r in
            db.query(ThreadTrackingLink.thread_id)
            .filter(ThreadTrackingLink.tracking_number.ilike(pattern))
            .all()
        )
        # Sender name / email
        matched_ids.update(
            r.thread_id for r in
            db.query(Email.thread_id)
            .filter(
                or_(
                    Email.sender_name.ilike(pattern),
                    Email.sender_email.ilike(pattern),
                )
            )
            .distinct()
            .all()
        )
        # Email body (text or HTML)
        matched_ids.update(
            r.thread_id for r in
            db.query(Email.thread_id)
            .filter(
                or_(
                    Email.body_text.ilike(pattern),
                    Email.body_html.ilike(pattern),
                )
            )
            .filter(Email.thread_id.isnot(None))
            .distinct()
            .all()
        )
        # Internal notes
        matched_ids.update(
            r.thread_id for r in
            db.query(Note.thread_id)
            .filter(Note.content.ilike(pattern))
            .distinct()
            .all()
        )
        # Customer name
        customer_ids = (
            db.query(PaceCustomerCache.customer_id)
            .filter(PaceCustomerCache.cust_name.ilike(pattern))
            .all()
        )
        if customer_ids:
            cid_list = [r.customer_id for r in customer_ids]
            # Via job links
            job_nums = (
                db.query(PaceJobCache.job_number)
                .filter(PaceJobCache.customer_id.in_(cid_list))
                .all()
            )
            if job_nums:
                jn_list = [r.job_number for r in job_nums]
                matched_ids.update(
                    r.thread_id for r in
                    db.query(ThreadJobLink.thread_id)
                    .filter(ThreadJobLink.job_number.in_(jn_list))
                    .all()
                )
            # Via PO links
            po_nums = (
                db.query(PacePOCache.po_number)
                .filter(PacePOCache.customer_id.in_(cid_list))
                .all()
            )
            if po_nums:
                pn_list = [r.po_number for r in po_nums]
                matched_ids.update(
                    r.thread_id for r in
                    db.query(ThreadPOLink.thread_id)
                    .filter(ThreadPOLink.po_number.in_(pn_list))
                    .all()
                )
        # Vendor name
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
                matched_ids.update(
                    r.thread_id for r in
                    db.query(ThreadPOLink.thread_id)
                    .filter(ThreadPOLink.po_number.in_(pn_list))
                    .all()
                )

    # Apply link filter
    if link_filter == "has_po":
        po_ids = {r.thread_id for r in db.query(ThreadPOLink.thread_id).distinct().all()}
        matched_ids = (matched_ids & po_ids) if q else po_ids
    elif link_filter == "has_job":
        job_ids = {r.thread_id for r in db.query(ThreadJobLink.thread_id).distinct().all()}
        matched_ids = (matched_ids & job_ids) if q else job_ids
    elif link_filter == "has_tracking":
        trk_ids = {r.thread_id for r in db.query(ThreadTrackingLink.thread_id).distinct().all()}
        matched_ids = (matched_ids & trk_ids) if q else trk_ids
    elif link_filter == "unlinked":
        linked = (
            {r.thread_id for r in db.query(ThreadPOLink.thread_id).distinct().all()} |
            {r.thread_id for r in db.query(ThreadJobLink.thread_id).distinct().all()}
        )
        if q:
            matched_ids -= linked
        else:
            all_ids = {r.id for r in db.query(Thread.id).all()}
            matched_ids = all_ids - linked
    elif link_filter == "mine":
        if current_user_id:
            mine_ids = {r.id for r in db.query(Thread.id).filter(Thread.assigned_to == current_user_id).all()}
            matched_ids = (matched_ids & mine_ids) if q else mine_ids
        else:
            return []
    else:
        # link_filter == "all" — if no query, seed with all IDs so
        # status/date filters below still have something to narrow
        if not q:
            matched_ids = {r.id for r in db.query(Thread.id).all()}

    # Apply status filter
    if status_filter and status_filter != "all":
        status_ids = {r.id for r in db.query(Thread.id).filter(Thread.status == status_filter).all()}
        matched_ids = (matched_ids & status_ids) if matched_ids else status_ids

    # Apply date range filter (based on latest email date)
    if date_from or date_to:
        date_q = db.query(Email.thread_id, func.max(Email.received_at).label("latest")).group_by(Email.thread_id)
        date_rows = date_q.all()
        date_map = {r.thread_id: r.latest for r in date_rows}

        filtered = set()
        for tid, latest in date_map.items():
            if latest is None:
                continue
            d = latest.date()
            if date_from and d < date_from:
                continue
            if date_to and d > date_to:
                continue
            filtered.add(tid)

        matched_ids = (matched_ids & filtered) if matched_ids else filtered

    # Apply sender filter
    if sender_filter and sender_filter.strip():
        spat = f"%{sender_filter.strip()}%"
        sender_ids = {
            r.thread_id for r in
            db.query(Email.thread_id)
            .filter(
                or_(
                    Email.sender_name.ilike(spat),
                    Email.sender_email.ilike(spat),
                )
            )
            .filter(Email.thread_id.isnot(None))
            .distinct()
            .all()
        }
        matched_ids = (matched_ids & sender_ids) if matched_ids else sender_ids

    # Apply assigned-to filter
    if assigned_filter and assigned_filter != "all":
        if assigned_filter == "unassigned":
            assigned_ids = {r.id for r in db.query(Thread.id).filter(Thread.assigned_to.is_(None)).all()}
        else:
            try:
                uid = int(assigned_filter)
                assigned_ids = {r.id for r in db.query(Thread.id).filter(Thread.assigned_to == uid).all()}
            except ValueError:
                assigned_ids = set()
        matched_ids = (matched_ids & assigned_ids) if matched_ids else assigned_ids

    return list(matched_ids)



# ─── Sort helper ──────────────────────────────────────────────────────────────
def _sort_thread_ids(thread_ids: list[int], sort_by: str, sort_dir: str, db: Session) -> list[int]:
    """Return thread_ids sorted server-side by the requested column."""
    if not thread_ids:
        return []
    asc = sort_dir == "asc"
    if sort_by == "subject":
        rows = db.query(Thread.id, Thread.subject).filter(Thread.id.in_(thread_ids)).all()
        rows.sort(key=lambda r: (r.subject or "").lower(), reverse=not asc)
    elif sort_by == "status":
        rows = db.query(Thread.id, Thread.status).filter(Thread.id.in_(thread_ids)).all()
        rows.sort(key=lambda r: r.status or "", reverse=not asc)
    elif sort_by == "from":
        latest_subq = (
            db.query(Email.thread_id, func.max(Email.received_at).label("max_received_at"))
            .filter(Email.thread_id.in_(thread_ids))
            .group_by(Email.thread_id)
            .subquery()
        )
        from_rows = (
            db.query(Email.thread_id, Email.sender_name, Email.sender_email)
            .join(latest_subq, (Email.thread_id == latest_subq.c.thread_id) &
                               (Email.received_at == latest_subq.c.max_received_at))
            .all()
        )
        from_map = {r.thread_id: (r.sender_name or r.sender_email or "").lower() for r in from_rows}
        rows = [(tid, from_map.get(tid, "")) for tid in thread_ids]
        rows.sort(key=lambda r: r[1], reverse=not asc)
    elif sort_by == "customer":
        # Resolve customer name via job link first, then PO link
        job_links = (
            db.query(ThreadJobLink.thread_id, PaceJobCache.customer_id)
            .join(PaceJobCache, PaceJobCache.job_number == ThreadJobLink.job_number)
            .filter(ThreadJobLink.thread_id.in_(thread_ids))
            .all()
        )
        cust_id_map: dict[int, str] = {}
        for tid, cid in job_links:
            if tid not in cust_id_map and cid:
                cust_id_map[tid] = cid

        po_only = [tid for tid in thread_ids if tid not in cust_id_map]
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
        cust_name_map: dict[str, str] = {}
        if all_cust_ids:
            custs = db.query(PaceCustomerCache).filter(
                PaceCustomerCache.customer_id.in_(all_cust_ids)
            ).all()
            cust_name_map = {c.customer_id: (c.cust_name or "").lower() for c in custs}

        rows = [(tid, cust_name_map.get(cust_id_map.get(tid, ""), "")) for tid in thread_ids]
        rows.sort(key=lambda r: r[1], reverse=not asc)
    elif sort_by == "assigned":
        rows = db.query(Thread.id, Thread.assigned_to).filter(Thread.id.in_(thread_ids)).all()
        rows.sort(key=lambda r: r.assigned_to or 99999, reverse=not asc)
    else:  # "latest" — sort by most recent email received_at
        latest_subq = (
            db.query(Email.thread_id, func.max(Email.received_at).label("max_received_at"))
            .filter(Email.thread_id.in_(thread_ids))
            .group_by(Email.thread_id)
            .all()
        )
        latest_map = {r.thread_id: r.max_received_at for r in latest_subq}
        rows = [(tid, latest_map.get(tid)) for tid in thread_ids]
        rows.sort(key=lambda r: r[1] or __import__("datetime").datetime.min, reverse=not asc)
    return [r[0] for r in rows]

# ─── Thread list ──────────────────────────────────────────────────────────────

@router.get("/threads", response_class=HTMLResponse)
async def thread_list(
    request: Request,
    page: int = 1,
    sort_by: str = "latest",
    sort_dir: str = "desc",
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

    thread_data = _enrich_threads([t.id for t in threads], db)

    users = db.query(User).filter(User.active == True).order_by(User.display_name).all()

    return templates.TemplateResponse("threads/list.html", {
        "request":         request,
        "current_user":    current_user,
        "threads":         thread_data,
        "page":            page,
        "total":           total,
        "page_size":       page_size,
        "total_pages":     (total + page_size - 1) // page_size,
        "query":           "",
        "link_filter":     "all",
        "sort_by":         sort_by,
        "sort_dir":        sort_dir,
        "status_filter":   "all",
        "date_from":       "",
        "date_to":         "",
        "sender_filter":   "",
        "assigned_filter": "all",
        "users":           users,
    })


# ─── Search (HTMX partial) ────────────────────────────────────────────────────

@router.get("/threads/search", response_class=HTMLResponse)
async def thread_search(
    request: Request,
    q: str = "",
    link_filter: str = "all",
    status_filter: str = "all",
    date_from: str | None = None,
    date_to: str | None = None,
    sender_filter: str = "",
    assigned_filter: str = "all",
    page: int = 1,
    sort_by: str = "latest",
    sort_dir: str = "desc",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    q = q.strip()
    sender_filter = sender_filter.strip()
    page_size = 50
    date_from_clean = date_from.strip() if date_from else ""
    date_to_clean = date_to.strip() if date_to else ""
    is_default = (
        not q and link_filter == "all" and status_filter == "all"
        and not date_from_clean and not date_to_clean
        and not sender_filter and assigned_filter == "all"
    )

    if is_default:
        total = db.query(Thread).count()
        offset = (page - 1) * page_size
        all_ids = [r.id for r in db.query(Thread.id).all()]
        sorted_ids = _sort_thread_ids(all_ids, sort_by, sort_dir, db)
        thread_data = _enrich_threads(sorted_ids[offset:offset + page_size], db)
    else:
        thread_ids = _search_thread_ids(
            q, link_filter, db,
            current_user_id=current_user.id,
            date_from=_parse_date(date_from),
            date_to=_parse_date(date_to),
            status_filter=status_filter,
            sender_filter=sender_filter,
            assigned_filter=assigned_filter,
        )
        total = len(thread_ids)
        offset = (page - 1) * page_size
        sorted_ids = _sort_thread_ids(thread_ids, sort_by, sort_dir, db)
        thread_data = _enrich_threads(sorted_ids[offset:offset + page_size], db)

    total_pages = (total + page_size - 1) // page_size
    users = db.query(User).filter(User.active == True).order_by(User.display_name).all()

    ctx = {
        "request":         request,
        "current_user":    current_user,
        "threads":         thread_data,
        "total":           total,
        "query":           q,
        "link_filter":     link_filter,
        "status_filter":   status_filter,
        "date_from":       date_from_clean,
        "date_to":         date_to_clean,
        "sender_filter":   sender_filter,
        "assigned_filter": assigned_filter,
        "page":            page,
        "total_pages":     total_pages,
        "page_size":       page_size,
        "status_labels":   STATUS_LABELS,
        "status_colors":   STATUS_COLORS,
        "sort_by":         sort_by,
        "sort_dir":        sort_dir,
        "users":           users,
    }

    is_htmx = request.headers.get("HX-Request") == "true"
    template = "threads/results_partial.html" if is_htmx else "threads/list.html"
    return templates.TemplateResponse(template, ctx)


# ─── Search CSV export ────────────────────────────────────────────────────────

@router.get("/threads/export.csv")
async def thread_search_export(
    request: Request,
    q: str = "",
    link_filter: str = "all",
    status_filter: str = "all",
    date_from: str | None = None,
    date_to: str | None = None,
    sender_filter: str = "",
    assigned_filter: str = "all",
    sort_by: str = "latest",
    sort_dir: str = "desc",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    import csv
    import io
    from fastapi.responses import StreamingResponse

    q = q.strip()
    sender_filter = sender_filter.strip()

    thread_ids = _search_thread_ids(
        q, link_filter, db,
        current_user_id=current_user.id,
        date_from=_parse_date(date_from),
        date_to=_parse_date(date_to),
        status_filter=status_filter,
        sender_filter=sender_filter,
        assigned_filter=assigned_filter,
    )
    sorted_ids = _sort_thread_ids(thread_ids, sort_by, sort_dir, db)
    threads = _enrich_threads(sorted_ids, db)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Customer", "Subject", "Sender", "Status", "Assigned To",
                     "Latest Activity", "Jobs", "POs", "Tracking"])
    for item in threads:
        latest = item["latest_email"]
        writer.writerow([
            item["customer_name"] or "",
            item["thread"].subject or "",
            (latest.sender_name or latest.sender_email or "") if latest else "",
            item["status"],
            item["assigned_user"].display_name if item["assigned_user"] else "",
            latest.received_at.strftime("%Y-%m-%d %H:%M") if latest and latest.received_at else "",
            item["job_count"],
            item["po_count"],
            item["tracking_count"],
        ])

    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="threads.csv"'},
    )


def _rewrite_cid_urls(html: str, attachments: list) -> str:
    """Replace cid:filename@... references with local attachment URLs."""
    if not html or not attachments:
        return html
    filename_to_id = {a.filename: a.id for a in attachments if a.filename}
    import re
    def replacer(m):
        filename = m.group(1).split('@')[0]
        att_id = filename_to_id.get(filename)
        return f'src="/attachments/{att_id}/file"' if att_id else m.group(0)
    return re.sub(r'src="cid:([^"]+)"', replacer, html)

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
        .options(joinedload(Email.attachments).joinedload(Attachment.proof))
        .order_by(Email.received_at.asc())
        .all()
    )

    # Jobs and POs are rendered via the links panel (links_panel.html)
    from app.web.routes.links import _render_links_panel as _build_links
    # Build links context inline for template include
    jobs = []
    for link in db.query(ThreadJobLink).filter(ThreadJobLink.thread_id == thread_id).all():
        job = db.query(PaceJobCache).filter(PaceJobCache.job_number == link.job_number).first()
        if not job:
            continue
        customer = db.query(PaceCustomerCache).filter(
            PaceCustomerCache.customer_id == job.customer_id
        ).first() if job.customer_id else None
        jobs.append({
            "job":           job,
            "link":          link,
            "status_label":  _job_status_map.get(str(job.admin_status), job.admin_status or "—"),
            "customer_name": customer.cust_name if customer else (job.customer_id or "—"),
            "pace_url":      f"{PACE_BASE}/Job/detail/{job.job_number}",
            "is_manual":     link.link_source == LinkSource.manual,
        })

    pos = []
    for link in db.query(ThreadPOLink).filter(ThreadPOLink.thread_id == thread_id).all():
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
            "link":         link,
            "status_label": _po_status_map.get(str(po.order_status), po.order_status or "—"),
            "vendor_name":  vendor_name,
            "pace_url":     f"{PACE_BASE}/PurchaseOrder/detail/{po.pace_internal_id}"
                            if po.pace_internal_id else None,
            "is_manual":    link.link_source == LinkSource.manual,
        })

    # Tracking numbers
    tracking = []
    for link in db.query(ThreadTrackingLink).filter(ThreadTrackingLink.thread_id == thread_id).all():
        tracking.append({
            "carrier":        link.carrier,
            "number":         link.tracking_number,
            "tracking_url":   _tracking_url(link.carrier, link.tracking_number),
        })

    assigned_user = db.query(User).filter(User.id == thread.assigned_to).first() if thread.assigned_to else None
    all_users = db.query(User).filter(User.active == True).order_by(User.display_name).all()

    # Rewrite cid: image references to local URLs
    for email in emails:
        if email.body_html and email.attachments:
            email.body_html = _rewrite_cid_urls(email.body_html, email.attachments)

    return templates.TemplateResponse("threads/detail.html", {
        "request":       request,
        "current_user":  current_user,
        "thread":        thread,
        "emails":        emails,
        "jobs":          jobs,
        "pos":           pos,
        "tracking":      tracking,
        "assigned_user": assigned_user,
        "all_users":     all_users,
        "status_labels": STATUS_LABELS,
        "status_colors": STATUS_COLORS,
    })
# update thread_detail() to add these two lines before the TemplateResponse:
#
#   all_users = db.query(User).filter(User.active == True).order_by(User.display_name).all()
#
#   "all_users":     all_users,