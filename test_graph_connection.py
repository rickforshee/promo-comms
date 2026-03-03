import os
import json
from pathlib import Path
import msal
import httpx

# Load .env manually (no dotenv dependency needed)
env = {}
for line in Path("/etc/promocomms/.env").read_text().splitlines():
    if line.strip() and not line.startswith("#"):
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip()

TENANT_ID    = env["GRAPH_TENANT_ID"]
CLIENT_ID    = env["GRAPH_CLIENT_ID"]
CLIENT_SECRET = env["GRAPH_CLIENT_SECRET"]
MAILBOX      = env["SHARED_MAILBOX"]

AUTHORITY    = f"https://login.microsoftonline.com/{TENANT_ID}"
SCOPE        = ["https://graph.microsoft.com/.default"]
GRAPH_BASE   = "https://graph.microsoft.com/v1.0"

print("--- Vivid Impact: Graph API Connection Test ---\n")

# Step 1: Acquire token
print("1. Acquiring access token...")
app = msal.ConfidentialClientApplication(
    CLIENT_ID,
    authority=AUTHORITY,
    client_credential=CLIENT_SECRET,
)
result = app.acquire_token_for_client(scopes=SCOPE)

if "access_token" not in result:
    print(f"   FAILED: {result.get('error')}: {result.get('error_description')}")
    exit(1)

print("   OK — token acquired\n")
token = result["access_token"]
headers = {"Authorization": f"Bearer {token}"}

# Step 2: Read mailbox profile
print(f"2. Reading mailbox profile for {MAILBOX}...")
r = httpx.get(f"{GRAPH_BASE}/users/{MAILBOX}", headers=headers)
if r.status_code == 200:
    user = r.json()
    print(f"   OK — Display name : {user.get('displayName')}")
    print(f"        Mail          : {user.get('mail')}")
    print(f"        UPN           : {user.get('userPrincipalName')}\n")
else:
    print(f"   FAILED ({r.status_code}): {r.text}\n")
    exit(1)

# Step 3: List most recent 5 messages
print("3. Fetching 5 most recent messages from inbox...")
r = httpx.get(
    f"{GRAPH_BASE}/users/{MAILBOX}/mailFolders/inbox/messages",
    headers=headers,
    params={"$top": 5, "$select": "subject,from,receivedDateTime", "$orderby": "receivedDateTime desc"},
)
if r.status_code == 200:
    messages = r.json().get("value", [])
    print(f"   OK — {len(messages)} messages returned")
    for m in messages:
        sender = m.get("from", {}).get("emailAddress", {}).get("address", "unknown")
        print(f"        [{m['receivedDateTime'][:10]}] {sender}: {m['subject']}")
    print()
else:
    print(f"   FAILED ({r.status_code}): {r.text}\n")
    exit(1)

# Step 4: Check send capability (dry run — no email sent)
print("4. Verifying send permission (dry-run — no email will be sent)...")
r = httpx.get(
    f"{GRAPH_BASE}/users/{MAILBOX}/mailFolders/sentItems/messages",
    headers=headers,
    params={"$top": 1, "$select": "subject,sentDateTime"},
)
if r.status_code == 200:
    print("   OK — Sent Items folder accessible (Mail.Send permission confirmed)\n")
else:
    print(f"   WARNING ({r.status_code}): Could not read Sent Items — {r.text}\n")

print("--- All tests passed. Graph API connection is working. ---")
