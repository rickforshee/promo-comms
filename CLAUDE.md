# Promo Command Center — Claude Code Context

## Project
Internal communications platform for Vivid Impact Promotional Products.
Centralizes email from shared M365 mailbox (vividpromo@vividimpact.com),
links to Pace ERP records, and manages proof approval workflows.

## Stack
- Python 3.12, FastAPI + Jinja2 + HTMX, SQLAlchemy/Alembic, APScheduler
- PostgreSQL (promocomms DB), Redis, Microsoft Graph API, Pace SOAP API
- Deployed on Dev1 (Ubuntu VM, dev1.vividimpact.com:8000)

## Services — ALWAYS restart both after code changes
- promo-comms.service (uvicorn web server, port 8000)
- promo-scheduler.service (APScheduler background tasks)
- Command: sudo systemctl restart promo-comms.service promo-scheduler.service

## Key paths
- Repo: ~/promo-comms/
- Venv: ~/promo-comms/venv/
- Templates: app/web/templates/
- Static: app/web/static/style.css
- Attachments: controlled by ATTACHMENT_STORAGE_PATH in .env
- DB: postgresql://promocomms:***@localhost:5432/promocomms (see .env)

## Rules
- Always run syntax validation after editing Python files
- Always restart both services after code changes
- Always push to GitHub after committing: git push origin master
- Check exact file content before patching
- Commit to master branch throughout sessions

## Phase status
Phase 1 ✅ Complete — ingestion, Pace cache, job/PO linking, historical import
Phase 2 ✅ Complete — outbound reply, notes, status, assignment, proof workflow, dashboard
Phase 3 🔄 Current — Search & Reporting

## Phase 3 scope
1. Full-text search across email subjects, bodies, and internal notes
2. Filtered search by sender, date, job#, PO#, vendor, status
3. Communication volume reports by type, vendor, team member, date range
4. Stale item reports — open threads with no activity past configurable threshold
5. CSV export of search results and reports

## Current state (as of March 14, 2026)
- DRY_RUN=true in .env — proof notifications log but do not send
- Flip DRY_RUN=false when ready for live notification testing
- ALLOWED_EMAIL_DOMAINS still in config.py (legacy, superseded by DRY_RUN)
- GitHub remote: https://github.com/rickforshee/promo-comms.git

## Architecture notes
- Pace SOAP API does not expose customer/job linkage — use Pace read-only PostgreSQL
- Customer traversal: purchaseorderline → job.ccmasterid → job.armasterid → pace_customer_cache
- Pace DB uses ANY(%s) with list params instead of IN for column name conflicts
- Graph API uses Application permissions (not Delegated) on shared mailbox
- portal.py router has no prefix — proof portal routes are /proof/{token}/...
- Attachment paths are relative to ATTACHMENT_STORAGE_PATH, stored in DB as relative paths
