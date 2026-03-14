from dotenv import load_dotenv
import os

load_dotenv()

# ─── Database ─────────────────────────────────────────────────────────────────

DATABASE_URL = os.getenv("DATABASE_URL")

# ─── Microsoft Graph API ──────────────────────────────────────────────────────

AZURE_CLIENT_ID     = os.getenv("AZURE_CLIENT_ID")
AZURE_TENANT_ID     = os.getenv("AZURE_TENANT_ID")
AZURE_CLIENT_SECRET = os.getenv("AZURE_CLIENT_SECRET")
SHARED_MAILBOX         = os.getenv("SHARED_MAILBOX")
BASE_URL               = os.getenv("BASE_URL", "http://dev1.vividimpact.com:8000")
ALLOWED_EMAIL_DOMAINS  = set(os.getenv("ALLOWED_EMAIL_DOMAINS", "vividimpact.com").split(","))
DRY_RUN                = os.getenv("DRY_RUN", "true").lower() == "true"

GRAPH_SCOPE         = ["https://graph.microsoft.com/.default"]
GRAPH_BASE_URL      = "https://graph.microsoft.com/v1.0"

# ─── Application ──────────────────────────────────────────────────────────────

APP_ENV    = os.getenv("APP_ENV", "development")
SECRET_KEY = os.getenv("SECRET_KEY")

# ─── Ingestion ────────────────────────────────────────────────────────────────

# How many emails to fetch per Graph API page
INGESTION_PAGE_SIZE = 50

# Local path for storing email attachments
ATTACHMENT_STORAGE_PATH = os.getenv(
    "ATTACHMENT_STORAGE_PATH",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "attachments")
)

PACE_DB_URL = (
    f"postgresql+psycopg2://{os.getenv('PACE_DB_USER')}:{os.getenv('PACE_DB_PASSWORD')}"
    f"@{os.getenv('PACE_DB_HOST')}:{os.getenv('PACE_DB_PORT', 5432)}/{os.getenv('PACE_DB_NAME')}"
)

SECRET_KEY = os.getenv("SECRET_KEY", "04740ffdbeeae2a2dd39b48de87c29f261c72304b47f1025d5a0c408083277c1")
