from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from sqlalchemy.orm import Session

from app.models import Thread, User
from app.web.auth import get_current_user, get_db

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


@router.post("/threads/{thread_id}/assign", response_class=HTMLResponse)
async def assign_thread(
    request: Request,
    thread_id: int,
    assigned_to: str = Form(default=""),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    thread = db.query(Thread).filter(Thread.id == thread_id).first()
    if not thread:
        return HTMLResponse("Not found", status_code=404)

    # Empty string means unassign
    if assigned_to == "":
        thread.assigned_to = None
    else:
        user = db.query(User).filter(User.id == int(assigned_to), User.active == True).first()
        if user:
            thread.assigned_to = user.id

    db.commit()
    db.refresh(thread)

    # Reload assigned user for display
    assigned_user = db.query(User).filter(User.id == thread.assigned_to).first() if thread.assigned_to else None
    all_users = db.query(User).filter(User.active == True).order_by(User.display_name).all()

    return templates.TemplateResponse("threads/assignment_widget.html", {
        "request":       request,
        "thread":        thread,
        "assigned_user": assigned_user,
        "all_users":     all_users,
    })
