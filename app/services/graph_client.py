import requests
from msal import ConfidentialClientApplication
from app import config


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
