"""
Pace Cache Population Service

Reads Job, PurchaseOrder, Vendor, and Customer data from Pace via SOAP API
and upserts into local PostgreSQL cache tables for fast access.

The cache ensures the platform remains functional if Pace is temporarily
unavailable, and avoids hitting Pace on every page load.

Job cache note:
  Jobs are identified as promo by querying JobPart filtered on
  @jobProductType = 'smPromo'. Job-level fields are pulled via traversal
  XPaths (e.g. job/@description). The cache stores one row per job number;
  if a job has multiple smPromo parts, the last part's values win on upsert.

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


def _safe_date(value: Any) -> date | None:
    """Parse date/datetime from Pace, return None on failure."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        ).date()
    except (ValueError, AttributeError):
        return None


def _safe_decimal(value: Any) -> Decimal | None:
    """Parse decimal value, return None on failure."""
    if not value:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    """Parse integer value, return None on failure."""
    if not value:
        return None
    try:
        return int(float(str(value)))
    except (ValueError, TypeError):
        return None


def _safe_bool(value: Any) -> bool | None:
    if not value:
        return None
    return str(value).lower() in ("true", "1", "yes")


class PaceCacheService:
    """
    Populates and refreshes the four Pace cache tables.
    All database operations use upsert (INSERT ... ON CONFLICT DO UPDATE)
    so partial runs are safe and idempotent.
    """

    # Records per page for pagination
    PAGE_SIZE = 50

    def __init__(self, db: Session):
        self.db     = db
        self.client = PaceClient.from_env()

    # ─── Public Interface ─────────────────────────────────────────────────────

    def refresh_all(self) -> dict:
        """Refresh all four cache tables. Returns summary of records upserted."""
        if not self.client.test_connection():
            raise RuntimeError("Cannot reach Pace — aborting cache refresh.")

        summary = {}
        summary["jobs"]      = self.refresh_jobs()
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

    def refresh_jobs(self) -> int:
        """
        Refresh pace_job_cache by querying JobPart filtered on smPromo product type.
        Excludes archived jobs (job/@adminStatus != 'X').
        Returns count of records upserted.
        """
        logger.info("Refreshing job cache (via JobPart, smPromo filter)...")

        fields = {
            "jobNumber":         "job/@job",
            "jobPartNum":        "@jobPart",
            "jobProductType":    "@jobProductType",
            "qtyOrdered":        "@qtyOrdered",
            "quotedPrice":       "@quotedPrice",
            "description":       "job/@description",
            "adminStatus":       "job/@adminStatus",
            "customerId":        "job/customer/@id",
            "dateSetup":         "job/@dateSetup",
            "promiseDate":       "job/@promiseDate",
            "scheduledShipDate": "job/@scheduledShipDate",
            "salesPersonId":     "job/salesPerson/@id",
            "csrId":             "job/csr/@id",
            "poNum":             "job/@poNum",
            "contactFirstName":  "job/@contactFirstName",
            "contactLastName":   "job/@contactLastName",
            "jobValue":          "job/@jobValue",
            "totalParts":        "job/@totalParts",
        }

        return self._paginate_and_upsert(
            object_type = "JobPart",
            fields      = fields,
            builder_fn  = lambda model: (
                model
                .filter("@jobProductType", "smPromo")
                .filter("job/@adminStatus", "!=", "X")
            ),
            mapper      = self._map_job,
            model       = PaceJobCache,
            pk_field    = "job_number",
        )

    def refresh_purchase_orders(self) -> int:
        """
        Refresh pace_po_cache via direct Pace DB query.
        Filters to promo era (April 2025+) and excludes cancelled POs.
        """
        from sqlalchemy import create_engine, text
        from app import config

        logger.info("Refreshing PO cache (via direct Pace DB)...")

        engine = create_engine(config.PACE_DB_URL)
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT
                    pomasterid, poautoinc, apmasterid, armasterid,
                    poorderstatus, poordertotal, podateentered,
                    podateconfirmed, podatelastreceipt, buyer,
                    poconfirmedby, ponotes, contactfirstname, contactlastname,
                    lastmodified
                FROM purchaseorder
                WHERE podateentered >= '2025-04-01'
                AND poorderstatus != 'X'
                AND pomasterid IS NOT NULL
                AND pomasterid != ''
            """)).fetchall()

        count = 0
        for row in rows:
            mapped = {
                "po_number":          str(row.pomasterid).strip(),
                "pace_internal_id":   row.poautoinc,
                "vendor_id":          row.apmasterid,
                "customer_id":        row.armasterid,
                "order_status":       row.poorderstatus,
                "order_total":        _safe_decimal(row.poordertotal),
                "date_entered":       row.podateentered,
                "date_confirmed":     row.podateconfirmed,
                "date_last_receipt":  row.podatelastreceipt,
                "buyer":              row.buyer,
                "confirmed_by":       row.poconfirmedby,
                "notes":              row.ponotes,
                "contact_first_name": row.contactfirstname,
                "contact_last_name":  row.contactlastname,
                "last_modified":      row.lastmodified,
            }
            if not mapped["po_number"]:
                continue
            stmt = pg_insert(PacePOCache).values(**mapped)
            stmt = stmt.on_conflict_do_update(
                index_elements=["po_number"],
                set_={k: stmt.excluded[k] for k in mapped if k != "po_number"},
            )
            self.db.execute(stmt)
            count += 1

        self.db.commit()
        logger.info(f"PO cache refresh complete — {count} records")
        return count

    def refresh_customers(self) -> int:
        """Refresh pace_customer_cache. Non-inactive customers only."""
        logger.info("Refreshing customer cache...")

        fields = {
            "customerId":     "@id",
            "custName":       "@custName",
            "address1":       "@address1",
            "city":           "@city",
            "phoneNumber":    "@phoneNumber",
            "customerStatus": "@customerStatus",
            "firstName":      "@contactFirstName",
            "lastName":       "@contactLastName",
            "accountBalance": "@accountBalance",
        }

        return self._paginate_and_upsert(
            object_type = "Customer",
            fields      = fields,
            builder_fn  = lambda model: model.filter("@customerStatus", "!=", "I"),
            mapper      = self._map_customer,
            model       = PaceCustomerCache,
            pk_field    = "customer_id",
        )
    
    def refresh_vendor_names(self) -> int:
        """
        Backfill company_name in pace_vendor_cache via direct Pace DB query.
        Uses apname from the vendor table, keyed on apmasterid.
        """
        from sqlalchemy import create_engine, text
        from app import config

        logger.info("Refreshing vendor company names from Pace DB...")
        engine = create_engine(config.PACE_DB_URL)
        with engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT apmasterid, apname FROM vendor WHERE apname IS NOT NULL AND apname != ''"
            )).fetchall()

        count = 0
        for row in rows:
            vendor = self.db.query(PaceVendorCache).filter(
                PaceVendorCache.vendor_id == str(row.apmasterid).strip()
            ).first()
            if vendor and row.apname:
                vendor.company_name = row.apname.strip()
                count += 1

        self.db.commit()
        logger.info(f"Vendor company names updated: {count}")
        return count

    # ─── Pagination + Upsert ─────────────────────────────────────────────────

    def _paginate_and_upsert(
        self,
        object_type: str,
        fields: dict,
        builder_fn,
        mapper,
        model,
        pk_field: str,
    ) -> int:
        """
        Page through all records from Pace using the fluent API and upsert
        each batch into the DB. Continues until a page returns fewer records
        than PAGE_SIZE. Returns total records upserted.
        """
        offset         = 0
        total_upserted = 0
        pace_model     = self.client.model(object_type)

        while True:
            collection = (
                builder_fn(pace_model)
                .load(fields)
                .offset(offset)
                .limit(self.PAGE_SIZE)
                .find()
            )

            objects = collection.to_list()
            if not objects:
                break

            rows = []
            for obj in objects:
                try:
                    row = mapper(obj)
                    if row and row.get(pk_field):
                        rows.append(row)
                except Exception as e:
                    logger.warning(
                        f"Failed to map {object_type} record: {e} — {obj}"
                    )

            if rows:
                self._upsert_batch(model, rows, pk_field)
                total_upserted += len(rows)

            logger.debug(
                f"{object_type}: offset={offset}, "
                f"fetched={len(objects)}, upserted={len(rows)}"
            )

            # Stop when we get a partial page — no more records
            if len(objects) < self.PAGE_SIZE:
                break

            offset += self.PAGE_SIZE

        logger.info(
            f"{object_type} cache refresh complete — {total_upserted} records"
        )
        return total_upserted

    def _upsert_batch(self, model, rows: list[dict], pk_field: str):
        # Deduplicate by PK — Pace returns one row per JobPart; last part wins
        seen = {}
        for row in rows:
            seen[row[pk_field]] = row
        rows = list(seen.values())
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
            "job_product_type":    obj.get("jobProductType"),
            "quoted_price":        _safe_decimal(obj.get("quotedPrice")),
            "qty_ordered":         _safe_decimal(obj.get("qtyOrdered")),
        }

    @staticmethod
    def _map_po(obj: dict[str, Any]) -> dict:
        return {
            "po_number":          obj.get("poNumber"),
            "pace_internal_id":   _safe_int(obj.get("primaryKey")),
            "vendor_id":          obj.get("vendor"),
            "customer_id":        obj.get("customer"),
            "order_status":       obj.get("orderStatus"),
            "order_total":        _safe_decimal(obj.get("orderTotal")),
            "date_entered":       _safe_date(obj.get("dateEntered")),
            "date_confirmed":     _safe_date(obj.get("dateConfirmed")),
            "date_last_receipt":  _safe_date(obj.get("dateLastReceipt")),
            "buyer":              obj.get("buyer"),
            "confirmed_by":       obj.get("confirmedBy"),
            "notes":              obj.get("notes"),
            "contact_first_name": obj.get("firstName"),
            "contact_last_name":  obj.get("lastName"),
    }

    @staticmethod
    def _map_vendor(obj: dict[str, Any]) -> dict:
        return {
            "vendor_id":          obj.get("vendorId"),
            "contact_first_name": obj.get("firstName"),
            "contact_last_name":  obj.get("lastName"),
            "contact_title":      obj.get("title"),
            "email_address":      obj.get("emailAddress"),
            "fax_number":         obj.get("fax"),
            "address1":           obj.get("address1"),
            "city":               obj.get("city"),
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
            "phone_number":       obj.get("phone"),
            "customer_status":    obj.get("customerStatus"),
            "contact_first_name": obj.get("firstName"),
            "contact_last_name":  obj.get("lastName"),
            "account_balance":    _safe_decimal(obj.get("accountBalance")),
        }