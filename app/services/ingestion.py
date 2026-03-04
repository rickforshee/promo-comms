import base64
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from app import config
from app.models import (
    Email, Thread, Attachment, ThreadJobLink, ThreadPOLink,
    EmailDirection, ImportSource, LinkSource, AttachmentType,
)
from app.services.graph_client import GraphClient
from app.services.pattern_matcher import extract_all

logger = logging.getLogger(__name__)


class IngestionService:
    """
    Orchestrates inbound email ingestion from the shared M365 mailbox.

    Responsibilities:
    - Fetch messages from Graph API (with pagination)
    - Deduplicate against existing records using M365 message ID
    - Parse message metadata and body
    - Detect job/PO numbers via pattern matching
    - Persist emails, threads, attachments, and job/PO links
    - Download and store attachment files
    """

    def __init__(self, db: Session):
        self.db     = db
        self.client = GraphClient()
        self._ensure_attachment_dir()

    def _ensure_attachment_dir(self):
        Path(config.ATTACHMENT_STORAGE_PATH).mkdir(parents=True, exist_ok=True)

    # ─── Public Interface ─────────────────────────────────────────────────────

    def run(
        self,
        folder: str = "inbox",
        import_source: ImportSource = ImportSource.realtime,
        max_pages: int = None,
    ) -> dict:
        """
        Fetch and ingest all new messages from the specified folder.

        Args:
            folder:        Graph API folder name ('inbox', 'sentitems', etc.)
            import_source: Tag records as realtime or historical
            max_pages:     Cap page fetches (useful for historical import batching)

        Returns:
            Summary dict with counts of ingested, skipped, and failed messages.
        """
        summary = {"ingested": 0, "skipped": 0, "failed": 0}
        skip_token = None
        pages_fetched = 0

        while True:
            try:
                response = self.client.list_messages(
                    folder=folder,
                    skip_token=skip_token,
                )
            except Exception as e:
                logger.error(f"Graph API fetch failed: {e}")
                break

            messages = response.get("value", [])
            logger.info(f"Fetched page {pages_fetched + 1}: {len(messages)} messages")

            for msg in messages:
                try:
                    result = self._ingest_message(msg, import_source)
                    if result == "ingested":
                        summary["ingested"] += 1
                    else:
                        summary["skipped"] += 1
                except Exception as e:
                    logger.error(f"Failed to ingest message {msg.get('id')}: {e}")
                    summary["failed"] += 1
                    self.db.rollback()

            pages_fetched += 1
            next_link = response.get("@odata.nextLink")

            if not next_link:
                break
            if max_pages and pages_fetched >= max_pages:
                logger.info(f"Reached max_pages={max_pages}, stopping.")
                break

            # Extract skipToken from nextLink for the next request
            skip_token = self._parse_skip_token(next_link)

        logger.info(
            f"Ingestion complete — "
            f"ingested: {summary['ingested']}, "
            f"skipped: {summary['skipped']}, "
            f"failed: {summary['failed']}"
        )
        return summary

    # ─── Per-Message Processing ───────────────────────────────────────────────

    def _ingest_message(
        self,
        msg: dict,
        import_source: ImportSource,
    ) -> str:
        """
        Process a single message dict from Graph API.
        Returns 'ingested' or 'skipped'.
        """
        message_id = msg["id"]

        # Deduplicate — skip if already in database
        existing = (
            self.db.query(Email)
            .filter(Email.message_id == message_id)
            .first()
        )
        if existing:
            return "skipped"

        # Resolve or create thread
        thread = self._get_or_create_thread(msg)

        # Parse and persist the email record
        email = self._create_email(msg, thread, import_source)
        self.db.add(email)
        self.db.flush()  # get email.id before attachments

        # Process attachments if present
        if msg.get("hasAttachments"):
            self._process_attachments(email, message_id)

        # Run pattern matching and create job/PO links
        matches = extract_all(
            subject=msg.get("subject", ""),
            body=self._get_body_text(msg),
        )
        self._create_job_links(thread, matches["job_numbers"])
        self._create_po_links(thread, matches["po_numbers"])

        self.db.commit()

        if matches["job_numbers"] or matches["po_numbers"]:
            logger.info(
                f"Message {message_id[:16]}… — "
                f"jobs: {matches['job_numbers']}, "
                f"POs: {matches['po_numbers']}"
            )

        return "ingested"

    # ─── Thread Resolution ────────────────────────────────────────────────────

    def _get_or_create_thread(self, msg: dict) -> Thread:
        """
        Return an existing Thread matching the M365 conversation ID,
        or create a new one.
        """
        conversation_id = msg.get("conversationId")

        if conversation_id:
            thread = (
                self.db.query(Thread)
                .filter(Thread.conversation_id == conversation_id)
                .first()
            )
            if thread:
                return thread

        thread = Thread(
            subject=msg.get("subject", "(no subject)")[:500],
            conversation_id=conversation_id,
        )
        self.db.add(thread)
        self.db.flush()
        return thread

    # ─── Email Record Construction ────────────────────────────────────────────

    def _create_email(
        self,
        msg: dict,
        thread: Thread,
        import_source: ImportSource,
    ) -> Email:
        sender    = msg.get("from", {}).get("emailAddress", {})
        to_list   = msg.get("toRecipients", [])
        cc_list   = msg.get("ccRecipients", [])
        all_recip = [
            r["emailAddress"]["address"]
            for r in to_list + cc_list
            if r.get("emailAddress", {}).get("address")
        ]

        received_raw = msg.get("receivedDateTime")
        received_at  = (
            datetime.fromisoformat(received_raw.replace("Z", "+00:00"))
            if received_raw else None
        )

        return Email(
            message_id       = msg["id"],
            thread_id        = thread.id,
            direction        = EmailDirection.inbound,
            sender_email     = sender.get("address"),
            sender_name      = sender.get("name"),
            recipient_emails = all_recip,
            subject          = (msg.get("subject") or "")[:500],
            body_text        = self._get_body_text(msg),
            body_html        = self._get_body_html(msg),
            received_at      = received_at,
            import_source    = import_source,
            raw_headers      = self._parse_headers(
                msg.get("internetMessageHeaders", [])
            ),
        )

    # ─── Attachment Handling ──────────────────────────────────────────────────

    def _process_attachments(self, email: Email, message_id: str):
        """Fetch, save, and record all attachments for a message."""
        try:
            attachments = self.client.list_attachments(message_id)
        except Exception as e:
            logger.warning(f"Could not fetch attachments for {message_id}: {e}")
            return

        for att in attachments:
            try:
                self._save_attachment(email, message_id, att)
            except Exception as e:
                logger.warning(
                    f"Failed to save attachment {att.get('id')} "
                    f"for message {message_id}: {e}"
                )

    def _save_attachment(self, email: Email, message_id: str, att_meta: dict):
        """Download attachment content, write to disk, record in database."""
        att_id       = att_meta["id"]
        filename     = att_meta.get("name", "unknown")
        content_type = att_meta.get("contentType", "application/octet-stream")
        file_size    = att_meta.get("size", 0)

        # Fetch full attachment with content
        full_att   = self.client.get_attachment(message_id, att_id)
        content_b64 = full_att.get("contentBytes")

        storage_path = None
        if content_b64:
            content_bytes = base64.b64decode(content_b64)
            storage_path  = self._write_attachment(
                email_id=email.id,
                filename=filename,
                content=content_bytes,
            )
            file_size = len(content_bytes)

        attachment_type = self._classify_attachment(filename, content_type)

        record = Attachment(
            email_id        = email.id,
            filename        = filename[:500],
            content_type    = content_type[:100],
            file_size       = file_size,
            storage_path    = storage_path,
            attachment_type = attachment_type,
        )
        self.db.add(record)

    def _write_attachment(
        self,
        email_id: int,
        filename: str,
        content: bytes,
    ) -> str:
        """Write attachment bytes to disk and return the relative storage path."""
        # Store under attachments/{email_id}/{filename}
        dir_path = Path(config.ATTACHMENT_STORAGE_PATH) / str(email_id)
        dir_path.mkdir(parents=True, exist_ok=True)

        # Sanitize filename
        safe_name = "".join(
            c if c.isalnum() or c in "._- " else "_" for c in filename
        ).strip()
        if not safe_name:
            safe_name = "attachment"

        file_path = dir_path / safe_name
        # Avoid overwriting if the same filename appears multiple times
        counter = 1
        while file_path.exists():
            stem   = Path(safe_name).stem
            suffix = Path(safe_name).suffix
            file_path = dir_path / f"{stem}_{counter}{suffix}"
            counter += 1

        file_path.write_bytes(content)

        # Return path relative to ATTACHMENT_STORAGE_PATH
        return str(file_path.relative_to(config.ATTACHMENT_STORAGE_PATH))

    @staticmethod
    def _classify_attachment(filename: str, content_type: str) -> AttachmentType:
        """Heuristic classification of attachment type."""
        name_lower = filename.lower()
        if any(name_lower.endswith(ext) for ext in (".pdf", ".jpg", ".jpeg", ".png")):
            if any(
                kw in name_lower
                for kw in ("proof", "art", "artwork", "mock", "design", "approve")
            ):
                return AttachmentType.proof
        return AttachmentType.general

    # ─── Job / PO Link Creation ───────────────────────────────────────────────

    def _create_job_links(self, thread: Thread, job_numbers: list[str]):
        """Create ThreadJobLink records for any new job numbers on this thread."""
        existing = {
            link.job_number
            for link in self.db.query(ThreadJobLink)
            .filter(ThreadJobLink.thread_id == thread.id)
            .all()
        }
        for job_num in job_numbers:
            if job_num not in existing:
                self.db.add(ThreadJobLink(
                    thread_id   = thread.id,
                    job_number  = job_num,
                    link_source = LinkSource.auto,
                ))

    def _create_po_links(self, thread: Thread, po_numbers: list[str]):
        """Create ThreadPOLink records for any new PO numbers on this thread."""
        existing = {
            link.po_number
            for link in self.db.query(ThreadPOLink)
            .filter(ThreadPOLink.thread_id == thread.id)
            .all()
        }
        for po_num in po_numbers:
            if po_num not in existing:
                self.db.add(ThreadPOLink(
                    thread_id   = thread.id,
                    po_number   = po_num,
                    link_source = LinkSource.auto,
                ))

    # ─── Helpers ─────────────────────────────────────────────────────────────

    @staticmethod
    def _get_body_text(msg: dict) -> str | None:
        body = msg.get("body", {})
        if body.get("contentType") == "text":
            return body.get("content")
        # If HTML, return None here — we store raw HTML in body_html
        return None

    @staticmethod
    def _get_body_html(msg: dict) -> str | None:
        body = msg.get("body", {})
        if body.get("contentType") == "html":
            return body.get("content")
        return None

    @staticmethod
    def _parse_headers(headers: list) -> dict:
        """Convert Graph API header list to a simple key→value dict."""
        return {h["name"]: h["value"] for h in headers if "name" in h}

    @staticmethod
    def _parse_skip_token(next_link: str) -> str | None:
        """Extract $skipToken value from a Graph API nextLink URL."""
        if not next_link:
            return None
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(next_link).query)
        tokens = qs.get("$skipToken") or qs.get("%24skipToken")
        return tokens[0] if tokens else None
