from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from sqlalchemy.orm import Session

from app.models import (
    Thread, Email, ThreadJobLink, ThreadPOLink,
    PaceJobCache, PacePOCache,
)
from app.web.auth import get_current_user, get_db
from app.models import User

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


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

    # Attach latest email and link counts to each thread
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

    # Linked jobs
    job_links = db.query(ThreadJobLink).filter(ThreadJobLink.thread_id == thread_id).all()
    jobs = []
    for link in job_links:
        job = db.query(PaceJobCache).filter(PaceJobCache.job_number == link.job_number).first()
        if job:
            jobs.append(job)

    # Linked POs
    po_links = db.query(ThreadPOLink).filter(ThreadPOLink.thread_id == thread_id).all()
    pos = []
    for link in po_links:
        po = db.query(PacePOCache).filter(PacePOCache.po_number == link.po_number).first()
        if po:
            pos.append(po)

    return templates.TemplateResponse("threads/detail.html", {
        "request":      request,
        "current_user": current_user,
        "thread":       thread,
        "emails":       emails,
        "jobs":         jobs,
        "pos":          pos,
    })
