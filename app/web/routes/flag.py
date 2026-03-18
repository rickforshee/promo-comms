import logging
from datetime import date
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app import config
from app.models import Thread, Email, EmailDirection
from app.services.graph_client import GraphClient
from app.web.auth import get_current_user, get_db

router = APIRouter()
log = logging.getLogger(__name__)
templates = None

def set_templates(t):
    global templates
    templates = t

@router.post("/threads/{thread_id}/flag", response_class=HTMLResponse)
async def toggle_flag(
    request: Request,
    thread_id: int,
    flagged: bool = Form(False),
    flag_due_date: str = Form(""),
    flag_note: str = Form(""),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    thread = db.query(Thread).filter(Thread.id == thread_id).first()
    if not thread:
        return HTMLResponse("Not found", status_code=404)

    thread.flagged = flagged
    thread.flag_due_date = date.fromisoformat(flag_due_date) if flag_due_date else None
    thread.flag_note = flag_note.strip() or None
    db.commit()

    # Sync to M365 — use last inbound message_id
    if not config.DRY_RUN:
        last = (
            db.query(Email)
            .filter(Email.thread_id == thread_id, Email.direction == EmailDirection.inbound)
            .order_by(Email.received_at.desc())
            .first()
        )
        if last and last.message_id and not last.message_id.startswith("outbound-"):
            try:
                GraphClient().set_message_flag(
                    last.message_id,
                    flagged=flagged,
                    due_date=flag_due_date or None,
                )
            except Exception:
                log.exception("Failed to sync flag to M365 for thread %s", thread_id)
    else:
        log.info("DRY RUN — would have set flag=%s on thread %s", flagged, thread_id)

    return templates.TemplateResponse("threads/flag_widget.html", {
        "request": request,
        "thread": thread,
        "current_user": current_user,
        "today": date.today(),
    })
