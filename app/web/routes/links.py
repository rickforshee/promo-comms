from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app import config
from app.models import (
    Thread, ThreadJobLink, ThreadPOLink,
    PaceJobCache, PacePOCache, PaceVendorCache, PaceCustomerCache,
    LinkSource, User,
)
from app.web.auth import get_current_user, get_db

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")

PACE_BASE = "https://vicepace.vividimpact.com/epace/company:public/object"

_job_status_map: dict[str, str] = {}
_po_status_map:  dict[str, str] = {}


def set_status_maps(job_map: dict, po_map: dict):
    global _job_status_map, _po_status_map
    _job_status_map = job_map
    _po_status_map  = po_map


# ─── Validation endpoints (live feedback) ────────────────────────────────────

@router.get("/validate/job/{job_number}")
async def validate_job(job_number: str, db: Session = Depends(get_db),
                       current_user: User = Depends(get_current_user)):
    job = db.query(PaceJobCache).filter(PaceJobCache.job_number == job_number.strip()).first()
    if job:
        return JSONResponse({"valid": True, "description": job.description or "", "job_number": job.job_number})
    return JSONResponse({"valid": False}, status_code=404)


@router.get("/validate/po/{po_number}")
async def validate_po(po_number: str, db: Session = Depends(get_db),
                      current_user: User = Depends(get_current_user)):
    po = db.query(PacePOCache).filter(PacePOCache.po_number == po_number.strip()).first()
    if po:
        vendor = db.query(PaceVendorCache).filter(PaceVendorCache.vendor_id == po.vendor_id).first() if po.vendor_id else None
        vendor_name = (vendor.company_name if vendor else None) or po.vendor_id or ""
        return JSONResponse({"valid": True, "vendor_name": vendor_name, "po_number": po.po_number})
    return JSONResponse({"valid": False}, status_code=404)


# ─── Add link ─────────────────────────────────────────────────────────────────

@router.post("/threads/{thread_id}/links/job", response_class=HTMLResponse)
async def add_job_link(
    request: Request,
    thread_id: int,
    job_number: str = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    job_number = job_number.strip()
    thread = db.query(Thread).filter(Thread.id == thread_id).first()
    if not thread:
        return HTMLResponse("Not found", status_code=404)

    job = db.query(PaceJobCache).filter(PaceJobCache.job_number == job_number).first()
    if not job:
        return _render_links_panel(thread_id, db, request, error=f"Job #{job_number} not found in Pace.")

    exists = db.query(ThreadJobLink).filter(
        ThreadJobLink.thread_id == thread_id,
        ThreadJobLink.job_number == job_number,
    ).first()
    if not exists:
        db.add(ThreadJobLink(
            thread_id   = thread_id,
            job_number  = job_number,
            link_source = LinkSource.manual,
            linked_by   = current_user.id,
        ))
        db.commit()

    return _render_links_panel(thread_id, db, request)


@router.post("/threads/{thread_id}/links/po", response_class=HTMLResponse)
async def add_po_link(
    request: Request,
    thread_id: int,
    po_number: str = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    po_number = po_number.strip()
    thread = db.query(Thread).filter(Thread.id == thread_id).first()
    if not thread:
        return HTMLResponse("Not found", status_code=404)

    po = db.query(PacePOCache).filter(PacePOCache.po_number == po_number).first()
    if not po:
        return _render_links_panel(thread_id, db, request, error=f"PO #{po_number} not found in Pace.")

    exists = db.query(ThreadPOLink).filter(
        ThreadPOLink.thread_id == thread_id,
        ThreadPOLink.po_number == po_number,
    ).first()
    if not exists:
        db.add(ThreadPOLink(
            thread_id   = thread_id,
            po_number   = po_number,
            link_source = LinkSource.manual,
            linked_by   = current_user.id,
        ))
        db.commit()

    return _render_links_panel(thread_id, db, request)


# ─── Remove link ──────────────────────────────────────────────────────────────

@router.delete("/threads/{thread_id}/links/job/{job_number}", response_class=HTMLResponse)
async def remove_job_link(
    request: Request,
    thread_id: int,
    job_number: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    link = db.query(ThreadJobLink).filter(
        ThreadJobLink.thread_id == thread_id,
        ThreadJobLink.job_number == job_number,
    ).first()
    if link:
        db.delete(link)
        db.commit()
    return _render_links_panel(thread_id, db, request)


@router.delete("/threads/{thread_id}/links/po/{po_number}", response_class=HTMLResponse)
async def remove_po_link(
    request: Request,
    thread_id: int,
    po_number: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    link = db.query(ThreadPOLink).filter(
        ThreadPOLink.thread_id == thread_id,
        ThreadPOLink.po_number == po_number,
    ).first()
    if link:
        db.delete(link)
        db.commit()
    return _render_links_panel(thread_id, db, request)


# ─── Shared panel renderer ────────────────────────────────────────────────────

def _render_links_panel(thread_id: int, db: Session, request: Request, error: str = None):
    jobs = []
    for link in db.query(ThreadJobLink).filter(ThreadJobLink.thread_id == thread_id).all():
        job = db.query(PaceJobCache).filter(PaceJobCache.job_number == link.job_number).first()
        if not job:
            continue
        customer = db.query(PaceCustomerCache).filter(
            PaceCustomerCache.customer_id == job.customer_id
        ).first() if job.customer_id else None
        jobs.append({
            "job":           job,
            "link":          link,
            "status_label":  _job_status_map.get(str(job.admin_status), job.admin_status or "—"),
            "customer_name": customer.cust_name if customer else (job.customer_id or "—"),
            "pace_url":      f"{PACE_BASE}/Job/detail/{job.job_number}",
            "is_manual":     link.link_source == LinkSource.manual,
        })

    pos = []
    for link in db.query(ThreadPOLink).filter(ThreadPOLink.thread_id == thread_id).all():
        po = db.query(PacePOCache).filter(PacePOCache.po_number == link.po_number).first()
        if not po:
            continue
        vendor = db.query(PaceVendorCache).filter(
            PaceVendorCache.vendor_id == po.vendor_id
        ).first() if po.vendor_id else None
        if vendor:
            vendor_name = vendor.company_name or (
                ((vendor.contact_first_name or "") + " " + (vendor.contact_last_name or "")).strip()
            ) or po.vendor_id or "—"
        else:
            vendor_name = po.vendor_id or "—"
        pos.append({
            "po":           po,
            "link":         link,
            "status_label": _po_status_map.get(str(po.order_status), po.order_status or "—"),
            "vendor_name":  vendor_name,
            "pace_url":     f"{PACE_BASE}/PurchaseOrder/detail/{po.pace_internal_id}"
                            if po.pace_internal_id else None,
            "is_manual":    link.link_source == LinkSource.manual,
        })

    return templates.TemplateResponse("threads/links_panel.html", {
        "request":    request,
        "thread_id":  thread_id,
        "jobs":       jobs,
        "pos":        pos,
        "error":      error,
    })
