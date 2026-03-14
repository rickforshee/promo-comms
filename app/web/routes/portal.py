"""
Public proof portal — no authentication required.
Clients access via a token URL to approve or request revisions.
"""
import logging
import os
from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

from app import config
from app.database import SessionLocal
from app.services.proof_notifications import notify_vivid_proof_decided
from app.models import Proof, ProofHistory, ProofStatus, Attachment, Thread

router = APIRouter()
templates = None


def set_templates(t):
    global templates
    templates = t


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _get_proof_or_404(token: str, db: Session) -> Proof:
    proof = db.query(Proof).filter(Proof.portal_token == token).first()
    if not proof:
        raise HTTPException(status_code=404, detail="Proof not found")
    return proof


@router.get("/proof/{token}", response_class=HTMLResponse)
def portal_view(request: Request, token: str, db: Session = Depends(get_db)):
    proof = _get_proof_or_404(token, db)
    attachment = db.query(Attachment).filter(
        Attachment.id == proof.attachment_id
    ).first()
    thread = db.query(Thread).filter(Thread.id == proof.thread_id).first()
    history = (
        db.query(ProofHistory)
        .filter(ProofHistory.proof_id == proof.id)
        .order_by(ProofHistory.changed_at.asc())
        .all()
    )
    already_decided = proof.status in (
        ProofStatus.approved, ProofStatus.rejected
    )
    return templates.TemplateResponse("portal/proof.html", {
        "request":        request,
        "proof":          proof,
        "attachment":     attachment,
        "thread":         thread,
        "history":        history,
        "ProofStatus":    ProofStatus,
        "already_decided": already_decided,
        "token":          token,
    })


@router.get("/proof/{token}/image")
def portal_image(token: str, db: Session = Depends(get_db)):
    proof = _get_proof_or_404(token, db)
    attachment = db.query(Attachment).filter(
        Attachment.id == proof.attachment_id
    ).first()
    if not attachment or not attachment.storage_path:
        raise HTTPException(status_code=404)
    path = os.path.join(config.ATTACHMENT_STORAGE_PATH, attachment.storage_path)
    if not os.path.exists(path):
        raise HTTPException(status_code=404)
    return FileResponse(
        path,
        media_type=attachment.content_type or "image/jpeg",
        headers={"Content-Disposition": "inline"},
    )


@router.post("/proof/{token}/approve", response_class=HTMLResponse)
def portal_approve(
    request: Request,
    token: str,
    client_name: str = Form(""),
    db: Session = Depends(get_db),
):
    proof = _get_proof_or_404(token, db)
    if proof.status == ProofStatus.approved:
        return HTMLResponse("<p>Already approved.</p>")
    if proof.status not in (
        ProofStatus.sent_for_approval, ProofStatus.received
    ):
        raise HTTPException(status_code=400, detail="Proof not awaiting approval")

    proof.status = ProofStatus.approved
    note = f"Approved via client portal"
    if client_name.strip():
        note += f" by {client_name.strip()}"
    history = ProofHistory(
        proof_id=proof.id,
        status=ProofStatus.approved,
        changed_by=None,
        notes=note,
    )
    db.add(history)
    db.commit()

    _thread = db.query(Thread).filter(Thread.id == proof.thread_id).first()
    notify_vivid_proof_decided(
        proof, _thread, "approved",
        client_name=client_name.strip(), notes="", db=db,
    )

    return templates.TemplateResponse("portal/proof_result.html", {
        "request": request,
        "status":  "approved",
        "message": "Thank you! Your approval has been recorded.",
    })


@router.post("/proof/{token}/revision", response_class=HTMLResponse)
def portal_revision(
    request: Request,
    token: str,
    client_name: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    proof = _get_proof_or_404(token, db)
    if proof.status == ProofStatus.approved:
        return HTMLResponse("<p>This proof has already been approved.</p>")
    if not notes.strip():
        raise HTTPException(status_code=400, detail="Please describe the revisions needed")

    proof.status = ProofStatus.revision_requested
    note = notes.strip()
    if client_name.strip():
        note = f"{client_name.strip()}: {note}"
    history = ProofHistory(
        proof_id=proof.id,
        status=ProofStatus.revision_requested,
        changed_by=None,
        notes=note,
    )
    db.add(history)
    db.commit()

    _thread = db.query(Thread).filter(Thread.id == proof.thread_id).first()
    notify_vivid_proof_decided(
        proof, _thread, "revision requested",
        client_name=client_name.strip(), notes=note, db=db,
    )

    return templates.TemplateResponse("portal/proof_result.html", {
        "request": request,
        "status":  "revision",
        "message": "Thank you! Your revision request has been sent.",
    })
