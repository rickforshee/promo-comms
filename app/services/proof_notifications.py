import logging

from app import config
from app.models import Email, EmailDirection, Thread, User
from app.services.graph_client import GraphClient

log = logging.getLogger(__name__)


def _check_dry_run(email: str) -> bool:
    """Return True if dry-run mode is active (caller should skip sending)."""
    if config.DRY_RUN:
        log.info("DRY RUN — would have sent to %s", email)
        return True
    return False


def _last_inbound(thread_id: int, db):
    return (
        db.query(Email)
        .filter(
            Email.thread_id == thread_id,
            Email.direction == EmailDirection.inbound,
        )
        .order_by(Email.received_at.desc())
        .first()
    )


def notify_client_proof_sent(proof, thread, db, override_email: str = None) -> None:
    """Reply into the thread to send the client a proof-approval request."""
    last = _last_inbound(thread.id, db)
    if not last or not last.message_id:
        log.warning("notify_client_proof_sent: no inbound message on thread %s", thread.id)
        return

    to_email = (override_email or last.sender_email or "").strip()
    if not to_email or "@" not in to_email:
        log.warning("notify_client_proof_sent: no valid recipient for proof %s", proof.id)
        return
    if _check_dry_run(to_email):
        return

    to_name = last.sender_name or ""
    portal_url = f"{config.BASE_URL}/proof/{proof.portal_token}"

    body = _proof_sent_body(thread, portal_url)
    try:
        GraphClient().reply_to_message(
            message_id=last.message_id,
            comment=body,
            to_recipients=[{"address": to_email, "name": to_name}],
        )
        log.info("Proof approval request sent to %s for proof %s", to_email, proof.id)
    except Exception:
        log.exception("Failed to send proof approval notification for proof %s", proof.id)


def notify_vivid_proof_decided(
    proof, thread, decision: str, client_name: str, notes: str, db
) -> None:
    """Reply into the thread to notify Vivid (+ assignee CC) of client decision."""
    last = _last_inbound(thread.id, db)
    if not last or not last.message_id:
        log.warning("notify_vivid_proof_decided: no inbound message on thread %s", thread.id)
        return

    if _check_dry_run(last.sender_email or ""):
        return

    cc = []
    if thread.assigned_to:
        assignee = db.query(User).filter(User.id == thread.assigned_to).first()
        if assignee and assignee.email:
            cc = [{"address": assignee.email, "name": assignee.display_name or ""}]

    body = _proof_decided_body(thread, decision, client_name, notes)
    try:
        log.info("notify_vivid_proof_decided: sending to message_id=%s cc=%s", last.message_id, cc)
        GraphClient().reply_to_message(
            message_id=last.message_id,
            comment=body,
            cc_recipients=cc or None,
        )
        log.info("Proof decision (%s) notification sent for proof %s", decision, proof.id)
    except Exception:
        log.exception("Failed to send proof decision notification for proof %s", proof.id)


def _proof_sent_body(thread, portal_url: str) -> str:
    subject = thread.subject or "your order"
    return (
        "<p>Hello,</p>"
        "<p>Your proof is ready for review. Please click the link below to view it and "
        "let us know if you approve or would like changes.</p>"
        f'<p><a href="{portal_url}" style="font-size:16px;font-weight:bold;">'
        "Review Proof &rarr;"
        "</a></p>"
        "<p>If the button above doesn't work, copy and paste this link into your browser:<br>"
        f'<a href="{portal_url}">{portal_url}</a></p>'
        "<p>Thank you,<br>Vivid Impact</p>"
    )


def _proof_decided_body(thread, decision: str, client_name: str, notes: str) -> str:
    who = f" by {client_name}" if client_name else ""
    header = f"Proof <strong>{decision}{who}</strong>"
    notes_block = f"<p><strong>Client notes:</strong><br>{notes}</p>" if notes else ""
    thread_url = f"{config.BASE_URL}/threads/{thread.id}"
    return (
        f"<p>{header}</p>"
        f"{notes_block}"
        f'<p><a href="{thread_url}">View thread &rarr;</a></p>'
    )
