"""
Promo Communications Platform — FastAPI Web Application
"""
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pathlib import Path

from app.web.routes import auth, threads

BASE_DIR = Path(__file__).parent

app = FastAPI(title="Promo Comms", docs_url=None, redoc_url=None)

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

templates = Jinja2Templates(directory=BASE_DIR / "templates")

app.include_router(auth.router)
app.include_router(threads.router)

from app.web.routes.threads import load_status_maps

@app.on_event("startup")
async def startup_event():
    load_status_maps()


@app.get("/")
async def root():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/threads")
