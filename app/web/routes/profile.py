from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from sqlalchemy.orm import Session

from app.models import User
from app.web.auth import get_current_user, get_db, hash_password, verify_password

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


@router.get("/profile", response_class=HTMLResponse)
async def profile_page(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return templates.TemplateResponse("profile.html", {
        "request": request,
        "current_user": current_user,
        "success": request.query_params.get("success"),
        "error": None,
    })


@router.post("/profile", response_class=HTMLResponse)
async def update_profile(
    request: Request,
    display_name: str = Form(""),
    current_password: str = Form(""),
    new_password: str = Form(""),
    confirm_password: str = Form(""),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    error = None
    user = db.query(User).filter(User.id == current_user.id).first()

    # Update display name
    if display_name.strip():
        user.display_name = display_name.strip()

    # Change password if requested
    if new_password:
        if not current_password:
            error = "Current password is required to set a new password."
        elif not verify_password(current_password, user.password_hash or ""):
            error = "Current password is incorrect."
        elif new_password != confirm_password:
            error = "New passwords do not match."
        elif len(new_password) < 8:
            error = "New password must be at least 8 characters."
        else:
            user.password_hash = hash_password(new_password)

    if error:
        db.rollback()
        return templates.TemplateResponse("profile.html", {
            "request": request,
            "current_user": current_user,
            "success": None,
            "error": error,
        })

    db.commit()
    return RedirectResponse("/profile?success=1", status_code=303)
