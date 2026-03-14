# Promo Command Center

## Stack
FastAPI + Jinja2 + HTMX + SQLAlchemy + PostgreSQL + Redis

## Services (both must be restarted after code changes)
- promo-comms.service (uvicorn web server, port 8000)
- promo-scheduler.service (APScheduler)

## Restart command
sudo systemctl restart promo-comms.service promo-scheduler.service

## Key paths
- App: ~/promo-comms/
- Venv: ~/promo-comms/venv/
- Templates: app/web/templates/
- Static: app/web/static/style.css
- DB: postgresql://promocomms:***@localhost:5432/promocomms (see .env)

## Rules
- Always run syntax validation after editing Python files
- Always restart both services after changes
- Commit to master branch throughout sessions
- Check exact file content before patching
