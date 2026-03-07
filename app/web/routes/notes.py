from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from sqlalchemy.orm import Session

from app.models import Note, Thread, User
from app.web.auth import get_current_user, get_db

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


@router.get("/threads/{thread_id}/notes", response_class=HTMLResponse)
async def get_notes(
    request: Request,
    thread_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    notes = (
        db.query(Note)
        .filter(Note.thread_id == thread_id)
        .order_by(Note.created_at.asc())
        .all()
    )
    # Attach author to each note
    author_ids = {n.author_id for n in notes}
    authors = {u.id: u for u in db.query(User).filter(User.id.in_(author_ids)).all()}

    note_data = [{"note": n, "author": authors.get(n.author_id)} for n in notes]

    return templates.TemplateResponse("threads/notes_panel.html", {
        "request":      request,
        "thread_id":    thread_id,
        "notes":        note_data,
        "current_user": current_user,
    })


@router.post("/threads/{thread_id}/notes", response_class=HTMLResponse)
async def add_note(
    request: Request,
    thread_id: int,
    content: str = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    thread = db.query(Thread).filter(Thread.id == thread_id).first()
    if not thread:
        return HTMLResponse("Not found", status_code=404)

    content = content.strip()
    if not content or content == "<p><br></p>":
        return HTMLResponse("", status_code=204)

    note = Note(
        thread_id = thread_id,
        author_id = current_user.id,
        content   = content,
    )
    db.add(note)
    db.commit()
    db.refresh(note)

    # Return updated notes panel
    notes = (
        db.query(Note)
        .filter(Note.thread_id == thread_id)
        .order_by(Note.created_at.asc())
        .all()
    )
    author_ids = {n.author_id for n in notes}
    authors = {u.id: u for u in db.query(User).filter(User.id.in_(author_ids)).all()}
    note_data = [{"note": n, "author": authors.get(n.author_id)} for n in notes]

    return templates.TemplateResponse("threads/notes_panel.html", {
        "request":      request,
        "thread_id":    thread_id,
        "notes":        note_data,
        "current_user": current_user,
    })


@router.delete("/threads/{thread_id}/notes/{note_id}", response_class=HTMLResponse)
async def delete_note(
    request: Request,
    thread_id: int,
    note_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    note = db.query(Note).filter(
        Note.id == note_id,
        Note.thread_id == thread_id,
    ).first()

    # Only author or admin can delete
    if note and (note.author_id == current_user.id or current_user.role.value == "admin"):
        db.delete(note)
        db.commit()

    notes = (
        db.query(Note)
        .filter(Note.thread_id == thread_id)
        .order_by(Note.created_at.asc())
        .all()
    )
    author_ids = {n.author_id for n in notes}
    authors = {u.id: u for u in db.query(User).filter(User.id.in_(author_ids)).all()}
    note_data = [{"note": n, "author": authors.get(n.author_id)} for n in notes]

    return templates.TemplateResponse("threads/notes_panel.html", {
        "request":      request,
        "thread_id":    thread_id,
        "notes":        note_data,
        "current_user": current_user,
    })
