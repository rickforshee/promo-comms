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
  sudo systemctl restart promo-comms.service promo-scheduler.service

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
Phase 2 ✅ Complete — outbound reply, notes, status, assignment, proof workflow, dashboard, proof notifications (DRY_RUN=true in .env — flip to false for production)
Phase 3 ✅ Complete — full-text search, filtered search, reports, CSV export, shipment tracking detection (UPS + FedEx)
Phase 4 🔄 In Progress — admin/profile done, mailbox hygiene done

## Phase 4 remaining
- M365 SSO
- Email templates
- Reporting enhancements (proof cycle time)
- UI refinements

## Production cutover checklist
- Set DRY_RUN=false in .env
- Restart both services
- Verify notify_client_proof_sent fires to real client addresses
- Verify notify_vivid_proof_decided fires back to shared mailbox
- Verify mailbox archiving on Resolved/Closed

## Service definitions (DO NOT CHANGE)
promo-comms.service → uvicorn web server port 8000
  ExecStart: venv/bin/uvicorn app.web.main:app --host 0.0.0.0 --port 8000
promo-scheduler.service → APScheduler --no-historical
  ExecStart: venv/bin/python -m app.scheduler --no-historical

## Current state
- DRY_RUN=true in .env — proof notifications log but do not send
- ALLOWED_EMAIL_DOMAINS still in config.py (legacy, superseded by DRY_RUN)
- GitHub remote: https://github.com/rickforshee/promo-comms.git

## Architecture notes
- Pace SOAP API does not expose customer/job linkage — use Pace read-only PostgreSQL
- Customer traversal: purchaseorderline → job.ccmasterid → job.armasterid → pace_customer_cache
- Pace DB uses ANY(%s) with list params instead of IN for column name conflicts
- Graph API uses Application permissions (not Delegated) on shared mailbox
- portal.py router has no prefix — proof portal routes are /proof/{token}/...
- Attachment paths are relative to ATTACHMENT_STORAGE_PATH, stored in DB as relative paths
