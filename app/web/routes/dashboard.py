from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timedelta, date as date_type

from app.web.auth import get_current_user, get_db
from app.models import Thread, ThreadStatus, User, ThreadJobLink, ThreadPOLink

router = APIRouter()
templates: Jinja2Templates = None


def set_templates(t: Jinja2Templates):
    global templates
    templates = t


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    status_counts = dict(
        db.query(Thread.status, func.count(Thread.id))
        .group_by(Thread.status)
        .all()
    )
    counts = {
        "open":     status_counts.get(ThreadStatus.open,     0),
        "pending":  status_counts.get(ThreadStatus.pending,  0),
        "resolved": status_counts.get(ThreadStatus.resolved, 0),
        "closed":   status_counts.get(ThreadStatus.closed,   0),
    }
    counts["total"] = sum(counts.values())

    by_assignee = (
        db.query(User.display_name, func.count(Thread.id).label("count"))
        .outerjoin(Thread, (Thread.assigned_to == User.id) & (Thread.status == ThreadStatus.open))
        .group_by(User.id, User.display_name)
        .order_by(func.count(Thread.id).desc())
        .all()
    )

    unassigned_open = (
        db.query(func.count(Thread.id))
        .filter(Thread.status == ThreadStatus.open, Thread.assigned_to == None)
        .scalar() or 0
    )

    stale_cutoff = datetime.utcnow() - timedelta(days=7)
    stale_threads = (
        db.query(Thread)
        .filter(Thread.status == ThreadStatus.open)
        .filter(Thread.updated_at < stale_cutoff)
        .order_by(Thread.updated_at.asc())
        .limit(10)
        .all()
    )

    linked_ids = (
        db.query(ThreadJobLink.thread_id)
        .union(db.query(ThreadPOLink.thread_id))
        .subquery()
    )
    unlinked_count = (
        db.query(func.count(Thread.id))
        .filter(Thread.status == ThreadStatus.open)
        .filter(~Thread.id.in_(linked_ids))
        .scalar() or 0
    )

    recent_cutoff = datetime.utcnow() - timedelta(hours=24)
    recent_threads = (
        db.query(Thread)
        .filter(Thread.updated_at >= recent_cutoff)
        .order_by(Thread.updated_at.desc())
        .limit(10)
        .all()
    )

    today = date_type.today()
    follow_ups = (
        db.query(Thread)
        .filter(Thread.flagged == True)
        .filter(Thread.status.in_(["open", "pending"]))
        .order_by(Thread.flag_due_date.asc().nullslast())
        .limit(15)
        .all()
    )

    return templates.TemplateResponse("dashboard.html", {
        "request":         request,
        "current_user":    current_user,
        "counts":          counts,
        "by_assignee":     by_assignee,
        "unassigned_open": unassigned_open,
        "stale_threads":   stale_threads,
        "unlinked_count":  unlinked_count,
        "recent_threads":  recent_threads,
        "follow_ups":      follow_ups,
        "today":           today,
        "now":             datetime.utcnow(),
    })