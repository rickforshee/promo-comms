from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pathlib import Path

from app.web.routes import auth, threads, assignment, notes, links, po_lines, profile, admin, status, reply

BASE_DIR = Path(__file__).parent

app = FastAPI(title="Promo Comms", docs_url=None, redoc_url=None)

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

templates = Jinja2Templates(directory=BASE_DIR / "templates")

app.include_router(auth.router)
app.include_router(threads.router)
app.include_router(assignment.router)
app.include_router(notes.router)
app.include_router(links.router)
app.include_router(po_lines.router)
app.include_router(profile.router)
app.include_router(admin.router)
app.include_router(status.router)
app.include_router(reply.router)


@app.on_event("startup")
async def startup_event():
    from app.web.routes.threads import load_status_maps
    load_status_maps()
    status.set_templates(templates)
    reply.set_templates(templates)


@app.get("/")
async def root():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/threads")