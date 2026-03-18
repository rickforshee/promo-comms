import logging
import requests
from msal import ConfidentialClientApplication
from app import config

log = logging.getLogger(__name__)


class GraphClient:
    """
    Thin wrapper around the Microsoft Graph API.
    Handles authentication and provides methods for mailbox operations
    against the shared promo mailbox.
    """

    def __init__(self):
        self._app = ConfidentialClientApplication(
            client_id=config.AZURE_CLIENT_ID,
            client_credential=config.AZURE_CLIENT_SECRET,
            authority=f"https://login.microsoftonline.com/{config.AZURE_TENANT_ID}",
        )
        self._token: str | None = None

    # ─── Authentication ───────────────────────────────────────────────────────

    def _get_token(self) -> str:
        """Acquire or refresh the access token."""
        result = self._app.acquire_token_for_client(scopes=config.GRAPH_SCOPE)
        if "access_token" not in result:
            raise RuntimeError(
                f"Failed to acquire Graph API token: "
                f"{result.get('error')}: {result.get('error_description')}"
            )
        return result["access_token"]

    def _headers(self) -> dict:
        if not self._token:
            self._token = self._get_token()
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

    def _get(self, url: str, params: dict = None) -> dict:
        """Make an authenticated GET request, refreshing token on 401."""
        response = requests.get(url, headers=self._headers(), params=params)
        if response.status_code == 401:
            self._token = self._get_token()
            response = requests.get(url, headers=self._headers(), params=params)
        response.raise_for_status()
        return response.json()
    
    def _post(self, url: str, json: dict) -> dict | None:
        """POST to a Graph API endpoint with auth and error handling."""
        token = self._get_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        resp = requests.post(url, headers=headers, json=json, timeout=30)
        if resp.status_code == 202:
            # 202 Accepted — reply sent, no body
            return None
        if resp.status_code not in (200, 201):
            raise RuntimeError(
                f"Graph POST failed [{resp.status_code}]: {resp.text[:500]}"
            )
        if resp.content:
            return resp.json()
        return None


    # ─── Mailbox Operations ───────────────────────────────────────────────────

    def list_messages(self, folder="inbox", page_size=50, skip=0, since=None):
        """List messages from a mailbox folder with optional date filter."""
        mailbox = config.SHARED_MAILBOX
        url = f"{config.GRAPH_BASE_URL}/users/{mailbox}/mailFolders/{folder}/messages"
        params = {
            "$top": page_size,
            "$select": "id,subject,from,toRecipients,ccRecipients,receivedDateTime,"
                    "conversationId,hasAttachments,body,internetMessageHeaders",
        }
        if since:
            params["$filter"] = f"receivedDateTime ge {since}"
            params["$orderby"] = "receivedDateTime asc"
        else:
            params["$orderby"] = "receivedDateTime desc"
        if skip:
            params["$skip"] = skip
        return self._get(url, params=params)

    # ... rest of method unchanged
        return self._get(url, params=params)

    def get_message(self, message_id: str) -> dict:
        """Fetch a single message by its M365 message ID."""
        mailbox = config.SHARED_MAILBOX
        url = f"{config.GRAPH_BASE_URL}/users/{mailbox}/messages/{message_id}"
        return self._get(url)

    def list_attachments(self, message_id: str) -> list[dict]:
        """Return the list of attachments for a given message."""
        mailbox = config.SHARED_MAILBOX
        url = (
            f"{config.GRAPH_BASE_URL}"
            f"/users/{mailbox}/messages/{message_id}/attachments"
        )
        result = self._get(url)
        return result.get("value", [])

    def get_attachment(self, message_id: str, attachment_id: str) -> dict:
        """Fetch a single attachment including its contentBytes (base64)."""
        mailbox = config.SHARED_MAILBOX
        url = (
            f"{config.GRAPH_BASE_URL}"
            f"/users/{mailbox}/messages/{message_id}"
            f"/attachments/{attachment_id}"
        )
        return self._get(url)

    def archive_message(self, message_id: str) -> None:
        """Move a message to the Archive folder in the shared mailbox."""
        mailbox = config.SHARED_MAILBOX
        url = f"{config.GRAPH_BASE_URL}/users/{mailbox}/messages/{message_id}/move"
        try:
            self._post(url, json={"destinationId": "archive"})
            log.info("Archived message %s", message_id)
        except Exception:
            log.exception("Failed to archive message %s — continuing", message_id)

    def archive_thread_messages(self, thread_id: int, db) -> int:
        """Archive all inbound M365 messages on a thread. Returns count archived."""
        from app.models import Email, EmailDirection
        messages = (
            db.query(Email)
            .filter(
                Email.thread_id == thread_id,
                Email.direction == EmailDirection.inbound,
                Email.message_id.isnot(None),
            )
            .all()
        )
        count = 0
        for msg in messages:
            # Skip synthetic outbound-* IDs we generate ourselves
            if msg.message_id.startswith("outbound-"):
                continue
            self.archive_message(msg.message_id)
            count += 1
        log.info("Archived %d messages for thread %s", count, thread_id)
        return count

    def set_message_flag(self, message_id: str, flagged: bool, due_date: str | None = None) -> None:
        """Set or clear a follow-up flag on a message in the shared mailbox."""
        mailbox = config.SHARED_MAILBOX
        url = f"{config.GRAPH_BASE_URL}/users/{mailbox}/messages/{message_id}"
        if flagged:
            flag = {"flagStatus": "flagged"}
            if due_date:
                flag["dueDateTime"] = {"dateTime": f"{due_date}T00:00:00", "timeZone": "Eastern Standard Time"}
                flag["startDateTime"] = {"dateTime": f"{due_date}T00:00:00", "timeZone": "Eastern Standard Time"}
        else:
            flag = {"flagStatus": "notFlagged"}
        token = self._get_token()
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        resp = requests.patch(url, headers=headers, json={"flag": flag}, timeout=30)
        if resp.status_code not in (200, 204):
            raise RuntimeError(f"Graph PATCH flag failed [{resp.status_code}]: {resp.text[:300]}")
        log.info("Set flag flagged=%s on message %s", flagged, message_id)

    def reply_to_message(
        self,
        message_id: str,
        comment: str,
        to_recipients: list[dict] | None = None,
        cc_recipients: list[dict] | None = None,
    ) -> None:
            """
            Reply to a message from the shared mailbox.

            Uses Graph API POST /messages/{id}/reply which sends immediately.

            Args:
                message_id:     M365 message ID to reply to.
                comment:        HTML body of the reply.
                to_recipients:  List of {"address": ..., "name": ...} dicts.
                                If omitted, Graph replies to the original sender.
                cc_recipients:  Optional CC list in same format.
            """
            mailbox = config.SHARED_MAILBOX
            url = (
                f"{config.GRAPH_BASE_URL}"
                f"/users/{mailbox}/messages/{message_id}/reply"
            )

            payload: dict = {"comment": comment}

            # Only override recipients if explicitly provided
            message_overrides: dict = {}
            if to_recipients:
                message_overrides["toRecipients"] = [
                    {"emailAddress": r} for r in to_recipients
                ]
            if cc_recipients:
                message_overrides["ccRecipients"] = [
                    {"emailAddress": r} for r in cc_recipients
                ]
            if message_overrides:
                payload["message"] = message_overrides

            self._post(url, json=payload)

    def send_new_message(
        self,
        to_recipients: list[dict],
        subject: str,
        body_html: str,
        cc_recipients: list[dict] | None = None,
    ) -> None:
        """
        Send a new email from the shared mailbox (not a reply).

        Args:
            to_recipients:  List of {"address": ..., "name": ...} dicts.
            subject:        Email subject line.
            body_html:      HTML body content.
            cc_recipients:  Optional CC list in same format.
        """
        mailbox = config.SHARED_MAILBOX
        url = f"{config.GRAPH_BASE_URL}/users/{mailbox}/sendMail"

        payload = {
            "message": {
                "subject": subject,
                "body": {
                    "contentType": "HTML",
                    "content": body_html,
                },
                "toRecipients": [
                    {"emailAddress": r} for r in to_recipients
                ],
            },
            "saveToSentItems": True,
        }
        if cc_recipients:
            payload["message"]["ccRecipients"] = [
                {"emailAddress": r} for r in cc_recipients
            ]

        self._post(url, json=payload)

