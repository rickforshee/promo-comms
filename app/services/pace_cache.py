"""
Pace Cache Population Service

Reads Job, PurchaseOrder, Vendor, and Customer data from Pace via SOAP API
and upserts into local PostgreSQL cache tables for fast access.

The cache ensures the platform remains functional if Pace is temporarily
unavailable, and avoids hitting Pace on every page load.

Refresh strategy:
  - Full refresh: replaces all cache data (used on first run)
  - Incremental refresh: fetches only recently modified records (scheduled)
"""

import logging
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.models import (
    PaceJobCache, PacePOCache, PaceVendorCache, PaceCustomerCache,
)
from app.services.pace_client import PaceClient

logger = logging.getLogger(__name__)


def _safe_date(value: str | None) -> date | None:
    """Parse ISO date string from Pace, return None on failure."""
    if not value:
        return None
    try:
        # Pace returns dates like "2024-03-15T00:00:00Z"
        return datetime.fromisoformat(
            value.replace("Z", "+00:00")
        ).date()
    except (ValueError, AttributeError):
        return None


def _safe_decimal(value: str | None) -> Decimal | None:
    """Parse decimal string, return None on failure."""
    if not value:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _safe_int(value: str | None) -> int | None:
    """Parse integer string, return None on failure."""
    if not value:
        return None
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return None


def _safe_bool(value: str | None) -> bool | None:
    if not value:
        return None
    return value.lower() in ("true", "1", "yes")


class PaceCacheService:
    """
    Populates and refreshes the four Pace cache tables.
    All database operations use upsert (INSERT ... ON CONFLICT DO UPDATE)
    so partial runs are safe and idempotent.
    """

    # Batch size for loadValueObjects pagination
    PAGE_SIZE = 50

    def __init__(self, db: Session):
        self.db     = db
        self.client = PaceClient()

    # ─── Public Interface ─────────────────────────────────────────────────────

    def refresh_all(self) -> dict:
        """Refresh all four cache tables. Returns summary of records upserted."""
        if not self.client.test_connection():
            raise RuntimeError("Cannot reach Pace — aborting cache refresh.")

        summary = {}
        summary["jobs"]      = self.refresh_jobs()
        summary["pos"]       = self.refresh_purchase_orders()
        summary["vendors"]   = self.refresh_vendors()
        summary["customers"] = self.refresh_customers()

        logger.info(
            f"Cache refresh complete — "
            f"jobs: {summary['jobs']}, "
            f"POs: {summary['pos']}, "
            f"vendors: {summary['vendors']}, "
            f"customers: {summary['customers']}"
        )
        return summary

    def refresh_jobs(self, xpath_filter: str = "@adminStatus != 'X'") -> int:
        """
        Refresh pace_job_cache.
        Default filter excludes archived/deleted jobs.
        Returns count of records upserted.
        """
        logger.info("Refreshing job cache...")
        fields = [
            {"name": "jobNumber",         "xpath": "@job"},
            {"name": "description",       "xpath": "@description"},
            {"name": "customerId",        "xpath": "customer/@id"},
            {"name": "adminStatus",       "xpath": "@adminStatus"},
            {"name": "dateSetup",         "xpath": "@dateSetup"},
            {"name": "promiseDate",       "xpath": "@promiseDate"},
            {"name": "scheduledShipDate", "xpath": "@scheduledShipDate"},
            {"name": "salespersonId",     "xpath": "salesperson/@id"},
            {"name": "csrId",             "xpath": "csr/@id"},
            {"name": "poNum",             "xpath": "@poNum"},
            {"name": "contactFirstName",  "xpath": "@contactFirstName"},
            {"name": "contactLastName",   "xpath": "@contactLastName"},
            {"name": "jobValue",          "xpath": "@jobValue"},
            {"name": "totalParts",        "xpath": "@totalParts"},
            {"name": "lastModified",      "xpath": "@lastModified"},
        ]
        return self._paginate_and_upsert(
            object_type  = "Job",
            fields       = fields,
            xpath_filter = xpath_filter,
            mapper       = self._map_job,
            model        = PaceJobCache,
            pk_field     = "job_number",
        )

    def refresh_purchase_orders(
        self,
        xpath_filter: str = "@orderStatus != 'X'",
    ) -> int:
        """
        Refresh pace_po_cache.
        Default filter excludes cancelled/deleted POs.
        """
        logger.info("Refreshing PO cache...")
        fields = [
            {"name": "poNumber",          "xpath": "@poNumber"},
            {"name": "vendorId",          "xpath": "vendor/@id"},
            {"name": "customerId",        "xpath": "customer/@id"},
            {"name": "orderStatus",       "xpath": "@orderStatus"},
            {"name": "orderTotal",        "xpath": "@orderTotal"},
            {"name": "dateEntered",       "xpath": "@dateEntered"},
            {"name": "dateConfirmed",     "xpath": "@dateConfirmed"},
            {"name": "dateLastReceipt",   "xpath": "@dateLastReceipt"},
            {"name": "buyer",             "xpath": "buyer/@id"},
            {"name": "confirmedBy",       "xpath": "@confirmedBy"},
            {"name": "notes",             "xpath": "@notes"},
            {"name": "contactFirstName",  "xpath": "@contactFirstName"},
            {"name": "contactLastName",   "xpath": "@contactLastName"},
            {"name": "lastModified",      "xpath": "@lastModified"},
        ]
        return self._paginate_and_upsert(
            object_type  = "PurchaseOrder",
            fields       = fields,
            xpath_filter = xpath_filter,
            mapper       = self._map_po,
            model        = PacePOCache,
            pk_field     = "po_number",
        )

    def refresh_vendors(self, xpath_filter: str = "@active = 'true'") -> int:
        """Refresh pace_vendor_cache. Default filter: active vendors only."""
        logger.info("Refreshing vendor cache...")
        fields = [
            {"name": "vendorId",         "xpath": "@id"},
            {"name": "firstName",        "xpath": "@contactFirstName"},
            {"name": "lastName",         "xpath": "@contactLastName"},
            {"name": "title",            "xpath": "@contactTitle"},
            {"name": "email",            "xpath": "@email"},
            {"name": "fax",              "xpath": "@faxNumber"},
            {"name": "address1",         "xpath": "@address1"},
            {"name": "city",             "xpath": "@city"},
            {"name": "state",            "xpath": "state/@id"},
            {"name": "active",           "xpath": "@active"},
            {"name": "customerNumber",   "xpath": "@customerNumber"},
            {"name": "defaultCurrency",  "xpath": "@defaultCurrency"},
        ]
        return self._paginate_and_upsert(
            object_type  = "Vendor",
            fields       = fields,
            xpath_filter = xpath_filter,
            mapper       = self._map_vendor,
            model        = PaceVendorCache,
            pk_field     = "vendor_id",
        )

    def refresh_customers(
        self,
        xpath_filter: str = "@customerStatus != 'I'",
    ) -> int:
        """Refresh pace_customer_cache. Default filter: non-inactive customers."""
        logger.info("Refreshing customer cache...")
        fields = [
            {"name": "customerId",      "xpath": "@id"},
            {"name": "custName",        "xpath": "@custName"},
            {"name": "address1",        "xpath": "@address1"},
            {"name": "city",            "xpath": "@city"},
            {"name": "state",           "xpath": "state/@id"},
            {"name": "email",           "xpath": "@email"},
            {"name": "phone",           "xpath": "@phoneNumber"},
            {"name": "customerStatus",  "xpath": "@customerStatus"},
            {"name": "firstName",       "xpath": "@contactFirstName"},
            {"name": "lastName",        "xpath": "@contactLastName"},
            {"name": "accountBalance",  "xpath": "@accountBalance"},
        ]
        return self._paginate_and_upsert(
            object_type  = "Customer",
            fields       = fields,
            xpath_filter = xpath_filter,
            mapper       = self._map_customer,
            model        = PaceCustomerCache,
            pk_field     = "customer_id",
        )

    # ─── Pagination + Upsert ─────────────────────────────────────────────────

    def _paginate_and_upsert(
        self,
        object_type: str,
        fields: list[dict],
        xpath_filter: str,
        mapper,
        model,
        pk_field: str,
    ) -> int:
        """
        Page through all records from Pace and upsert each batch into the DB.
        Returns total records upserted.
        """
        offset       = 0
        total_upserted = 0

        while True:
            result = self.client.load_value_objects(
                object_type  = object_type,
                fields       = fields,
                xpath_filter = xpath_filter,
                offset       = offset,
                limit        = self.PAGE_SIZE,
            )

            objects = result["objects"]
            if not objects:
                break

            rows = []
            for obj in objects:
                try:
                    row = mapper(obj)
                    if row and row.get(pk_field):
                        rows.append(row)
                except Exception as e:
                    logger.warning(f"Failed to map {object_type} record: {e} — {obj}")

            if rows:
                self._upsert_batch(model, rows, pk_field)
                total_upserted += len(rows)

            logger.debug(
                f"{object_type}: offset={offset}, "
                f"fetched={len(objects)}, upserted={len(rows)}"
            )

            offset += self.PAGE_SIZE
            if offset >= result["total_records"]:
                break

        logger.info(f"{object_type} cache refresh complete — {total_upserted} records")
        return total_upserted

    def _upsert_batch(self, model, rows: list[dict], pk_field: str):
        """
        Upsert a batch of row dicts into the given model's table.
        Uses PostgreSQL INSERT ... ON CONFLICT DO UPDATE.
        """
        if not rows:
            return
        stmt = pg_insert(model.__table__).values(rows)
        update_cols = {
            col.name: stmt.excluded[col.name]
            for col in model.__table__.columns
            if col.name != pk_field
        }
        stmt = stmt.on_conflict_do_update(
            index_elements=[pk_field],
            set_=update_cols,
        )
        self.db.execute(stmt)
        self.db.commit()

    # ─── Field Mappers ────────────────────────────────────────────────────────

    @staticmethod
    def _map_job(obj: dict[str, Any]) -> dict:
        return {
            "job_number":          obj.get("jobNumber"),
            "description":         obj.get("description"),
            "customer_id":         obj.get("customerId"),
            "admin_status":        obj.get("adminStatus"),
            "date_setup":          _safe_date(obj.get("dateSetup")),
            "promise_date":        _safe_date(obj.get("promiseDate")),
            "scheduled_ship_date": _safe_date(obj.get("scheduledShipDate")),
            "salesperson_id":      _safe_int(obj.get("salespersonId")),
            "csr_id":              _safe_int(obj.get("csrId")),
            "po_num":              obj.get("poNum"),
            "contact_first_name":  obj.get("contactFirstName"),
            "contact_last_name":   obj.get("contactLastName"),
            "job_value":           _safe_decimal(obj.get("jobValue")),
            "total_parts":         _safe_int(obj.get("totalParts")),
            "last_modified":       _safe_date(obj.get("lastModified")),
        }

    @staticmethod
    def _map_po(obj: dict[str, Any]) -> dict:
        return {
            "po_number":           obj.get("poNumber"),
            "vendor_id":           obj.get("vendorId"),
            "customer_id":         obj.get("customerId"),
            "order_status":        obj.get("orderStatus"),
            "order_total":         _safe_decimal(obj.get("orderTotal")),
            "date_entered":        _safe_date(obj.get("dateEntered")),
            "date_confirmed":      _safe_date(obj.get("dateConfirmed")),
            "date_last_receipt":   _safe_date(obj.get("dateLastReceipt")),
            "buyer":               obj.get("buyer"),
            "confirmed_by":        obj.get("confirmedBy"),
            "notes":               obj.get("notes"),
            "contact_first_name":  obj.get("contactFirstName"),
            "contact_last_name":   obj.get("contactLastName"),
            "last_modified":       _safe_date(obj.get("lastModified")),
        }

    @staticmethod
    def _map_vendor(obj: dict[str, Any]) -> dict:
        return {
            "vendor_id":         obj.get("vendorId"),
            "contact_first_name": obj.get("firstName"),
            "contact_last_name":  obj.get("lastName"),
            "contact_title":      obj.get("title"),
            "email_address":      obj.get("email"),
            "fax_number":         obj.get("fax"),
            "address1":           obj.get("address1"),
            "city":               obj.get("city"),
            "state":              obj.get("state"),
            "active":             _safe_bool(obj.get("active")),
            "customer_number":    obj.get("customerNumber"),
            "default_currency":   obj.get("defaultCurrency"),
        }

    @staticmethod
    def _map_customer(obj: dict[str, Any]) -> dict:
        return {
            "customer_id":        obj.get("customerId"),
            "cust_name":          obj.get("custName"),
            "address1":           obj.get("address1"),
            "city":               obj.get("city"),
            "state":              obj.get("state"),
            "email_address":      obj.get("email"),
            "phone_number":       obj.get("phone"),
            "customer_status":    obj.get("customerStatus"),
            "contact_first_name": obj.get("firstName"),
            "contact_last_name":  obj.get("lastName"),
            "account_balance":    _safe_decimal(obj.get("accountBalance")),
        }
