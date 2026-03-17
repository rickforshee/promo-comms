"""
Reply route — handles outbound email composition and sending.

POST /threads/{thread_id}/reply
    Sends a reply via Graph API and records the outbound email in the DB.

GET  /threads/{thread_id}/reply-compose
    Returns the compose panel partial (used for HTMX lazy-load on Reply click).
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app import config
from app.models import Email, Thread, ImportSource
from app.services.graph_client import GraphClient
from app.web.auth import get_current_user, get_db

logger = logging.getLogger(__name__)

router = APIRouter()
_templates: Jinja2Templates | None = None


def set_templates(t: Jinja2Templates) -> None:
    global _templates
    _templates = t


# ─── Compose Panel (GET) ──────────────────────────────────────────────────────

@router.get(
    "/threads/{thread_id}/reply-compose",
    response_class=HTMLResponse,
)
async def reply_compose(
    request: Request,
    thread_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Return the reply compose panel partial."""
    thread = db.query(Thread).filter(Thread.id == thread_id).first()
    if not thread:
        return HTMLResponse("<p class='empty'>Thread not found.</p>", status_code=404)

    # Find the most recent inbound email to reply to
    last_inbound = (
        db.query(Email)
        .filter(
            Email.thread_id == thread_id,
            Email.direction == "inbound",
        )
        .order_by(Email.received_at.desc())
        .first()
    )

    # Pre-fill To with the original sender
    to_email = ""
    to_name = ""
    if last_inbound:
        to_email = last_inbound.sender_email or ""
        to_name = last_inbound.sender_name or ""

    # Build Re: subject
    subject = thread.subject or ""
    if subject and not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"

    # The M365 message ID to reply to (used by Graph API)
    reply_to_message_id = last_inbound.message_id if last_inbound else None

    return _templates.TemplateResponse(
        "threads/reply_compose.html",
        {
            "request": request,
            "thread": thread,
            "to_email": to_email,
            "to_name": to_name,
            "subject": subject,
            "reply_to_message_id": reply_to_message_id,
            "current_user": current_user,
        },
    )


# ─── Closed / Dismissed ──────────────────────────────────────────────────────

@router.get(
    "/threads/{thread_id}/reply-compose-closed",
    response_class=HTMLResponse,
)
async def reply_compose_closed(
    request: Request,
    thread_id: int,
    current_user=Depends(get_current_user),
):
    """Return empty div to collapse the compose panel."""
    return HTMLResponse('<div id="reply-compose"></div>')


# ─── Send Reply (POST) ────────────────────────────────────────────────────────

@router.post(
    "/threads/{thread_id}/reply",
    response_class=HTMLResponse,
)
async def send_reply(
    request: Request,
    thread_id: int,
    to_email: str = Form(...),
    to_name: str = Form(""),
    cc_emails: str = Form(""),       # comma-separated, optional
    subject: str = Form(""),
    body: str = Form(...),
    reply_to_message_id: str = Form(""),   # M365 message ID to reply to
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Send reply via Graph API and record outbound email in DB."""
    thread = db.query(Thread).filter(Thread.id == thread_id).first()
    if not thread:
        return HTMLResponse("<p class='empty'>Thread not found.</p>", status_code=404)

    # Build recipient lists
    to_recipients = [{"address": to_email.strip(), "name": to_name.strip()}]

    cc_recipients = []
    if cc_emails.strip():
        for addr in cc_emails.split(","):
            addr = addr.strip()
            if addr:
                cc_recipients.append({"address": addr, "name": ""})

    # Convert plain text body to basic HTML
    body_html = _text_to_html(body)

    if config.DRY_RUN:
        logger.info(
            "DRY RUN — reply suppressed to %s; test copy would go to %s",
            to_email,
            config.ALLOWED_TESTING_EMAIL or "(none configured)",
        )
    else:
        try:
            client = GraphClient()

            if reply_to_message_id:
                # Reply to a specific message — preserves thread/conversation context
                client.reply_to_message(
                    message_id=reply_to_message_id,
                    comment=body_html,
                    to_recipients=to_recipients,
                    cc_recipients=cc_recipients if cc_recipients else None,
                )
            else:
                # No inbound message to reply to — send as new message
                client.send_new_message(
                    to_recipients=to_recipients,
                    subject=subject,
                    body_html=body_html,
                    cc_recipients=cc_recipients if cc_recipients else None,
                )

        except Exception as e:
            logger.error(f"Failed to send reply for thread {thread_id}: {e}")
            return _templates.TemplateResponse(
                "threads/reply_compose.html",
                {
                    "request": request,
                    "thread": thread,
                    "to_email": to_email,
                    "to_name": to_name,
                    "subject": subject,
                    "reply_to_message_id": reply_to_message_id,
                    "body": body,
                    "cc_emails": cc_emails,
                    "error": f"Send failed: {e}",
                    "current_user": current_user,
                },
            )

    # ── Record outbound email in DB ─────────────────────────────────────────
    all_recipients = [to_email.strip()]
    if cc_emails.strip():
        all_recipients += [a.strip() for a in cc_emails.split(",") if a.strip()]

    outbound = Email(
        message_id=f"outbound-{thread_id}-{int(datetime.now(timezone.utc).timestamp())}",
        thread_id=thread_id,
        direction="outbound",
        sender_email=current_user.email,
        sender_name=current_user.display_name or current_user.email,
        recipient_emails=all_recipients,
        subject=subject,
        body_text=body,
        body_html=body_html,
        received_at=datetime.now(timezone.utc),
        import_source=ImportSource.realtime,
    )
    db.add(outbound)
    db.commit()

    logger.info(
        f"Outbound reply sent — thread={thread_id}, "
        f"to={to_email}, by={current_user.email}"
    )

    # ── Return updated email list with success banner ────────────────────────
    emails = (
        db.query(Email)
        .filter(Email.thread_id == thread_id)
        .order_by(Email.received_at.asc())
        .all()
    )

    return _templates.TemplateResponse(
        "threads/email_list_partial.html",
        {
            "request": request,
            "thread": thread,
            "emails": emails,
            "sent_success": True,
            "current_user": current_user,
        },
    )


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _text_to_html(text: str) -> str:
    """Convert plain text to simple HTML, preserving line breaks."""
    import html as html_module
    escaped = html_module.escape(text)
    paragraphs = escaped.split("\n\n")
    parts = []
    for para in paragraphs:
        lines = para.replace("\n", "<br>\n")
        parts.append(f"<p>{lines}</p>")
    return "\n".join(parts)