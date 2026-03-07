from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from sqlalchemy.orm import Session

from app.models import User, UserRole
from app.web.auth import get_current_user, get_db, hash_password

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role.value != "admin":
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user


# ─── User list ────────────────────────────────────────────────────────────────

@router.get("/users", response_class=HTMLResponse)
async def user_list(
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    users = db.query(User).order_by(User.email).all()
    return templates.TemplateResponse("admin/users.html", {
        "request":      request,
        "current_user": admin,
        "users":        users,
        "success":      request.query_params.get("success"),
        "error":        request.query_params.get("error"),
    })


# ─── Add user ─────────────────────────────────────────────────────────────────

@router.get("/users/new", response_class=HTMLResponse)
async def add_user_form(
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    return templates.TemplateResponse("admin/user_form.html", {
        "request":      request,
        "current_user": admin,
        "edit_user":    None,
        "roles":        [r.value for r in UserRole],
        "error":        None,
    })


@router.post("/users/new", response_class=HTMLResponse)
async def add_user(
    request: Request,
    email: str = Form(...),
    display_name: str = Form(""),
    role: str = Form("user"),
    password: str = Form(...),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    email = email.strip().lower()
    error = None

    if not email:
        error = "Email is required."
    elif db.query(User).filter(User.email == email).first():
        error = f"A user with email {email} already exists."
    elif len(password) < 8:
        error = "Password must be at least 8 characters."
    elif role not in [r.value for r in UserRole]:
        error = "Invalid role."

    if error:
        return templates.TemplateResponse("admin/user_form.html", {
            "request":      request,
            "current_user": admin,
            "edit_user":    None,
            "roles":        [r.value for r in UserRole],
            "error":        error,
        })

    user = User(
        email         = email,
        display_name  = display_name.strip() or None,
        role          = UserRole(role),
        active        = True,
        password_hash = hash_password(password),
    )
    db.add(user)
    db.commit()
    return RedirectResponse("/admin/users?success=User+added.", status_code=303)


# ─── Edit user ────────────────────────────────────────────────────────────────

@router.get("/users/{user_id}/edit", response_class=HTMLResponse)
async def edit_user_form(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    edit_user = db.query(User).filter(User.id == user_id).first()
    if not edit_user:
        return RedirectResponse("/admin/users?error=User+not+found.", status_code=303)
    return templates.TemplateResponse("admin/user_form.html", {
        "request":      request,
        "current_user": admin,
        "edit_user":    edit_user,
        "roles":        [r.value for r in UserRole],
        "error":        None,
    })


@router.post("/users/{user_id}/edit", response_class=HTMLResponse)
async def edit_user(
    user_id: int,
    request: Request,
    email: str = Form(...),
    display_name: str = Form(""),
    role: str = Form("user"),
    active: str = Form("on"),
    new_password: str = Form(""),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return RedirectResponse("/admin/users?error=User+not+found.", status_code=303)

    email = email.strip().lower()
    error = None

    existing = db.query(User).filter(User.email == email, User.id != user_id).first()
    if existing:
        error = f"Email {email} is already in use."
    elif role not in [r.value for r in UserRole]:
        error = "Invalid role."
    elif new_password and len(new_password) < 8:
        error = "Password must be at least 8 characters."

    if error:
        return templates.TemplateResponse("admin/user_form.html", {
            "request":      request,
            "current_user": admin,
            "edit_user":    user,
            "roles":        [r.value for r in UserRole],
            "error":        error,
        })

    user.email        = email
    user.display_name = display_name.strip() or None
    user.role         = UserRole(role)
    user.active       = (active == "on")

    if new_password:
        user.password_hash = hash_password(new_password)

    db.commit()
    return RedirectResponse("/admin/users?success=User+updated.", status_code=303)
