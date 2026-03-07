from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.web.auth import get_current_user, get_db
from app.models import Thread, User

router = APIRouter()

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

_templates = None

def set_templates(t):
    global _templates
    _templates = t


@router.post("/threads/{thread_id}/status", response_class=HTMLResponse)
async def update_status(
    request: Request,
    thread_id: int,
    status: str = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    thread = db.query(Thread).filter(Thread.id == thread_id).first()
    if not thread:
        return HTMLResponse("Not found", status_code=404)

    valid = {"open", "pending", "resolved", "closed"}
    if status not in valid:
        return HTMLResponse("Invalid status", status_code=400)

    thread.status = status
    db.commit()

    return _templates.TemplateResponse("threads/status_widget.html", {
        "request":       request,
        "thread":        thread,
        "status_labels": STATUS_LABELS,
        "status_colors": STATUS_COLORS,
        "current_user":  current_user,
    })
