from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from sqlalchemy import create_engine, text

from app import config
from app.web.auth import get_current_user
from app.models import User

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")

# PO line status codes
_LINE_STATUS = {
    'O': 'Open',
    'R': 'Received',
    'C': 'Closed',
    'V': 'Void',
    'X': 'Cancelled',
}


@router.get("/po/{po_number}/lines", response_class=HTMLResponse)
async def get_po_lines(
    request: Request,
    po_number: str,
    current_user: User = Depends(get_current_user),
):
    try:
        engine = create_engine(config.PACE_DB_URL)
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT
                    podescription,
                    vendorpartnumber,
                    poqtyordered,
                    poqtyreceived,
                    pounitprice,
                    polinestatus,
                    daterequired
                FROM purchaseorderline
                WHERE pomasterid = :po_number
                ORDER BY poautokey
            """), {"po_number": po_number}).fetchall()
    except Exception:
        rows = []

    lines = []
    for row in rows:
        qty_ordered  = float(row.poqtyordered)  if row.poqtyordered  is not None else None
        qty_received = float(row.poqtyreceived) if row.poqtyreceived is not None else None
        unit_price   = float(row.pounitprice)   if row.pounitprice   is not None else None
        ext_price    = (qty_ordered * unit_price) if qty_ordered and unit_price else None

        lines.append({
            "description":    row.podescription or "—",
            "vendor_part":    row.vendorpartnumber or "",
            "qty_ordered":    qty_ordered,
            "qty_received":   qty_received,
            "unit_price":     unit_price,
            "ext_price":      ext_price,
            "status":         _LINE_STATUS.get(row.polinestatus or "", row.polinestatus or "—"),
            "date_required":  row.daterequired,
        })

    return templates.TemplateResponse("threads/po_lines.html", {
        "request":   request,
        "po_number": po_number,
        "lines":     lines,
    })
