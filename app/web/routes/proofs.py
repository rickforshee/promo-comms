import os
from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app import config
from app.web.auth import get_current_user, get_db
from app.models import Email, EmailDirection
from app.services.proof_notifications import notify_client_proof_sent
from app.models import (
    Attachment, AttachmentType, Proof, ProofHistory, ProofStatus,
    Thread, Email, User
)

router = APIRouter()
templates = None


def set_templates(t):
    global templates
    templates = t


@router.get("/attachments/{attachment_id}/file")
def serve_attachment(
    attachment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    attachment = db.query(Attachment).filter(Attachment.id == attachment_id).first()
    if not attachment or not attachment.storage_path:
        raise HTTPException(status_code=404, detail="Attachment not found")
    path = os.path.join(config.ATTACHMENT_STORAGE_PATH, attachment.storage_path)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="File not found on disk")
    return FileResponse(
        path,
        media_type=attachment.content_type or "application/octet-stream",
        headers={"Content-Disposition": "inline"},
    )


@router.post("/threads/{thread_id}/attachments/{attachment_id}/mark-proof",
             response_class=HTMLResponse)
def mark_as_proof(
    request: Request,
    thread_id: int,
    attachment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    attachment = db.query(Attachment).filter(Attachment.id == attachment_id).first()
    if not attachment:
        raise HTTPException(status_code=404)

    existing = db.query(Proof).filter(Proof.attachment_id == attachment_id).first()
    if not existing:
        attachment.attachment_type = AttachmentType.proof
        proof = Proof(
            attachment_id=attachment_id,
            thread_id=thread_id,
            status=ProofStatus.received,
        )
        db.add(proof)
        db.flush()
        history = ProofHistory(
            proof_id=proof.id,
            status=ProofStatus.received,
            changed_by=current_user.id,
            notes="Marked as proof",
        )
        db.add(history)
        db.commit()

    return HTMLResponse('<span class="proof-marked-badge">&#10003; Proof</span>')


@router.get("/threads/{thread_id}/proofs", response_class=HTMLResponse)
def proofs_tab(
    request: Request,
    thread_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    thread = db.query(Thread).filter(Thread.id == thread_id).first()
    if not thread:
        raise HTTPException(status_code=404)

    proofs = (
        db.query(Proof)
        .filter(Proof.thread_id == thread_id)
        .order_by(Proof.created_at.asc())
        .all()
    )

    proof_data = []
    for proof in proofs:
        attachment = db.query(Attachment).filter(Attachment.id == proof.attachment_id).first()
        history = (
            db.query(ProofHistory, User)
            .join(User, ProofHistory.changed_by == User.id)
            .filter(ProofHistory.proof_id == proof.id)
            .order_by(ProofHistory.changed_at.asc())
            .all()
        )
        last_inbound = (
            db.query(Email)
            .filter(Email.thread_id == thread_id,
                    Email.direction == EmailDirection.inbound)
            .order_by(Email.received_at.desc())
            .first()
        )
        client_email = (last_inbound.sender_email or "") if last_inbound else ""
        proof_data.append({
            "proof":        proof,
            "attachment":   attachment,
            "history":      history,
            "client_email": client_email,
        })

    return templates.TemplateResponse("threads/proofs_tab.html", {
        "request":      request,
        "current_user": current_user,
        "thread":       thread,
        "proof_data":   proof_data,
        "ProofStatus":  ProofStatus,
    })


_TRANSITIONS = {
    ProofStatus.received:           [ProofStatus.sent_for_approval],
    ProofStatus.sent_for_approval:  [ProofStatus.approved,
                                     ProofStatus.revision_requested,
                                     ProofStatus.rejected],
    ProofStatus.rejected:           [ProofStatus.sent_for_approval],
    ProofStatus.revision_requested: [ProofStatus.sent_for_approval],
    ProofStatus.approved:           [],
}

_NOTE_REQUIRED = {ProofStatus.rejected, ProofStatus.revision_requested}


@router.post("/proofs/{proof_id}/status", response_class=HTMLResponse)
def update_proof_status(
    request: Request,
    proof_id: int,
    new_status: ProofStatus = Form(...),
    notes: str = Form(""),
    override_email: str = Form(""),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    proof = db.query(Proof).filter(Proof.id == proof_id).first()
    if not proof:
        raise HTTPException(status_code=404)

    allowed = _TRANSITIONS.get(proof.status, [])
    if new_status not in allowed:
        raise HTTPException(status_code=400, detail="Invalid status transition")

    if new_status in _NOTE_REQUIRED and not notes.strip():
        raise HTTPException(status_code=400, detail="Notes are required for this status")

    proof.status = new_status
    history = ProofHistory(
        proof_id=proof.id,
        status=new_status,
        changed_by=current_user.id,
        notes=notes.strip() or None,
    )
    db.add(history)
    db.commit()

    if new_status == ProofStatus.sent_for_approval:
        _thread = db.query(Thread).filter(Thread.id == proof.thread_id).first()
        notify_client_proof_sent(
            proof, _thread, db,
            override_email=override_email.strip() or None,
        )

    attachment = db.query(Attachment).filter(Attachment.id == proof.attachment_id).first()
    history_rows = (
        db.query(ProofHistory, User)
        .join(User, ProofHistory.changed_by == User.id)
        .filter(ProofHistory.proof_id == proof.id)
        .order_by(ProofHistory.changed_at.asc())
        .all()
    )
    thread = db.query(Thread).filter(Thread.id == proof.thread_id).first()

    _last = (
        db.query(Email)
        .filter(Email.thread_id == proof.thread_id,
                Email.direction == EmailDirection.inbound)
        .order_by(Email.received_at.desc())
        .first()
    )
    _client_email = (_last.sender_email or "") if _last else ""
    return templates.TemplateResponse("threads/proof_card.html", {
        "request":      request,
        "current_user": current_user,
        "thread":       thread,
        "item": {
            "proof":        proof,
            "attachment":   attachment,
            "history":      history_rows,
            "client_email": _client_email,
        },
        "ProofStatus":  ProofStatus,
    })
