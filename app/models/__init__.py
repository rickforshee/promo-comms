from sqlalchemy import (
    Column, Integer, String, Text, Boolean, DateTime, Date,
    Numeric, ForeignKey, Enum, JSON, func, UniqueConstraint
)
from sqlalchemy.orm import relationship
from app.database import Base
import enum
import uuid


# ─── Enums ────────────────────────────────────────────────────────────────────

class UserRole(str, enum.Enum):
    admin   = "admin"
    manager = "manager"
    user    = "user"

class ThreadStatus(str, enum.Enum):
    open    = "open"
    pending = "pending"
    resolved = "resolved"
    closed  = "closed"

class EmailDirection(str, enum.Enum):
    inbound  = "inbound"
    outbound = "outbound"

class ImportSource(str, enum.Enum):
    realtime   = "realtime"
    historical = "historical"

class LinkSource(str, enum.Enum):
    auto   = "auto"
    manual = "manual"

class AttachmentType(str, enum.Enum):
    proof   = "proof"
    tracking = "tracking"
    general = "general"

class ProofStatus(str, enum.Enum):
    received           = "received"
    sent_for_approval  = "sent_for_approval"
    approved           = "approved"
    rejected           = "rejected"
    revision_requested = "revision_requested"

class ThreadStatus(enum.Enum):
    open     = "open"
    pending  = "pending"
    resolved = "resolved"
    closed   = "closed"


# ─── Users ────────────────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id           = Column(Integer, primary_key=True)
    email        = Column(String(255), unique=True, nullable=False)
    display_name = Column(String(255))
    role         = Column(Enum(UserRole), nullable=False, default=UserRole.user)
    active       = Column(Boolean, nullable=False, default=True)
    created_at   = Column(DateTime, server_default=func.now())
    last_login   = Column(DateTime)
    password_hash = Column(String(255), nullable=True)

    assigned_threads = relationship("Thread", back_populates="assignee")
    notes            = relationship("Note", back_populates="author")
    audit_entries    = relationship("AuditLog", back_populates="user")


# ─── Threads ──────────────────────────────────────────────────────────────────

class Thread(Base):
    __tablename__ = "threads"

    id              = Column(Integer, primary_key=True)
    subject         = Column(String(500))
    conversation_id = Column(String(255), index=True)  # M365 conversation ID
    status          = Column(Enum(ThreadStatus), nullable=False,
                             default=ThreadStatus.open)
    assigned_to     = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at      = Column(DateTime, server_default=func.now())
    updated_at      = Column(DateTime, server_default=func.now(),
                             onupdate=func.now())
    flagged       = Column(Boolean, nullable=False, default=False, server_default='false')
    flag_due_date = Column(Date, nullable=True)
    flag_note     = Column(String(500), nullable=True)

    assignee    = relationship("User", back_populates="assigned_threads")
    emails      = relationship("Email", back_populates="thread")
    notes       = relationship("Note", back_populates="thread")
    proofs      = relationship("Proof", back_populates="thread")
    job_links   = relationship("ThreadJobLink", back_populates="thread")
    po_links    = relationship("ThreadPOLink", back_populates="thread")


# ─── Emails ───────────────────────────────────────────────────────────────────

class Email(Base):
    __tablename__ = "emails"

    id              = Column(Integer, primary_key=True)
    message_id      = Column(String(255), unique=True, nullable=False)  # M365 ID
    thread_id       = Column(Integer, ForeignKey("threads.id"), nullable=True)
    direction       = Column(Enum(EmailDirection), nullable=False)
    sender_email    = Column(String(255))
    sender_name     = Column(String(255))
    recipient_emails = Column(JSON)   # list of addresses
    subject         = Column(String(500))
    body_text       = Column(Text)
    body_html       = Column(Text)
    received_at     = Column(DateTime)
    import_source   = Column(Enum(ImportSource), nullable=False,
                             default=ImportSource.realtime)
    raw_headers     = Column(JSON)
    created_at      = Column(DateTime, server_default=func.now())

    thread      = relationship("Thread", back_populates="emails")
    attachments = relationship("Attachment", back_populates="email")


# ─── Thread ↔ Job Links ───────────────────────────────────────────────────────

class ThreadJobLink(Base):
    __tablename__ = "thread_job_links"

    id          = Column(Integer, primary_key=True)
    thread_id   = Column(Integer, ForeignKey("threads.id"), nullable=False)
    job_number  = Column(String(12), nullable=False)   # FK to pace_job_cache
    link_source = Column(Enum(LinkSource), nullable=False)
    linked_by   = Column(Integer, ForeignKey("users.id"), nullable=True)
    linked_at   = Column(DateTime, server_default=func.now())

    thread = relationship("Thread", back_populates="job_links")


# ─── Thread ↔ PO Links ────────────────────────────────────────────────────────

class ThreadPOLink(Base):
    __tablename__ = "thread_po_links"

    id          = Column(Integer, primary_key=True)
    thread_id   = Column(Integer, ForeignKey("threads.id"), nullable=False)
    po_number   = Column(String(8), nullable=False)    # FK to pace_po_cache
    link_source = Column(Enum(LinkSource), nullable=False)
    linked_by   = Column(Integer, ForeignKey("users.id"), nullable=True)
    linked_at   = Column(DateTime, server_default=func.now())

    thread = relationship("Thread", back_populates="po_links")


# ─── Attachments ──────────────────────────────────────────────────────────────

class Attachment(Base):
    __tablename__ = "attachments"

    id              = Column(Integer, primary_key=True)
    email_id        = Column(Integer, ForeignKey("emails.id"), nullable=False)
    filename        = Column(String(500))
    content_type    = Column(String(100))
    file_size       = Column(Integer)
    storage_path    = Column(String(1000))
    attachment_type = Column(Enum(AttachmentType), nullable=False,
                             default=AttachmentType.general)
    created_at      = Column(DateTime, server_default=func.now())

    email = relationship("Email", back_populates="attachments")
    proof = relationship("Proof", back_populates="attachment", uselist=False)


# ─── Proofs ───────────────────────────────────────────────────────────────────

class Proof(Base):
    __tablename__ = "proofs"

    id            = Column(Integer, primary_key=True)
    attachment_id = Column(Integer, ForeignKey("attachments.id"), nullable=False)
    thread_id     = Column(Integer, ForeignKey("threads.id"), nullable=False)
    status        = Column(Enum(ProofStatus), nullable=False,
                           default=ProofStatus.received)
    portal_token  = Column(String(64), unique=True, nullable=True,
                           default=lambda: uuid.uuid4().hex)
    created_at    = Column(DateTime, server_default=func.now())
    updated_at    = Column(DateTime, server_default=func.now(),
                           onupdate=func.now())

    attachment = relationship("Attachment", back_populates="proof")
    thread     = relationship("Thread", back_populates="proofs")
    history    = relationship("ProofHistory", back_populates="proof",
                              order_by="ProofHistory.changed_at")


class ProofHistory(Base):
    __tablename__ = "proof_history"

    id         = Column(Integer, primary_key=True)
    proof_id   = Column(Integer, ForeignKey("proofs.id"), nullable=False)
    status     = Column(Enum(ProofStatus), nullable=False)
    changed_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    changed_at = Column(DateTime, server_default=func.now())
    notes      = Column(Text)

    proof = relationship("Proof", back_populates="history")


# ─── Internal Notes ───────────────────────────────────────────────────────────

class Note(Base):
    __tablename__ = "notes"

    id         = Column(Integer, primary_key=True)
    thread_id  = Column(Integer, ForeignKey("threads.id"), nullable=False)
    author_id  = Column(Integer, ForeignKey("users.id"), nullable=False)
    content    = Column(Text, nullable=False)
    created_at = Column(DateTime, server_default=func.now())

    thread = relationship("Thread", back_populates="notes")
    author = relationship("User", back_populates="notes")


# ─── Audit Log ────────────────────────────────────────────────────────────────

class AuditLog(Base):
    __tablename__ = "audit_log"

    id          = Column(Integer, primary_key=True)
    user_id     = Column(Integer, ForeignKey("users.id"), nullable=True)
    action      = Column(String(100), nullable=False)
    entity_type = Column(String(100), nullable=False)
    entity_id   = Column(Integer)
    old_value   = Column(JSON)
    new_value   = Column(JSON)
    created_at  = Column(DateTime, server_default=func.now())

    user = relationship("User", back_populates="audit_entries")


# ─── Pace Cache Tables ────────────────────────────────────────────────────────

class PaceJobCache(Base):
    __tablename__ = "pace_job_cache"

    job_number          = Column(String(12), primary_key=True)
    description         = Column(String(50))
    customer_id         = Column(String(8))
    admin_status        = Column(String(1))
    date_setup          = Column(Date)
    promise_date        = Column(Date)
    scheduled_ship_date = Column(Date)
    salesperson_id      = Column(String(20))
    csr_id              = Column(String(20))
    po_num              = Column(String(50))
    contact_first_name  = Column(String(100))
    contact_last_name   = Column(String(100))
    job_value           = Column(Numeric(12, 2))
    total_parts         = Column(Integer)
    job_product_type    = Column(String(20))
    quoted_price        = Column(Numeric(12, 2))
    qty_ordered         = Column(Numeric(12, 2))
    last_modified       = Column(DateTime)
    cached_at           = Column(DateTime, server_default=func.now())


class PacePOCache(Base):
    __tablename__ = "pace_po_cache"

    po_number          = Column(String(8), primary_key=True)
    pace_internal_id   = Column(Integer)
    vendor_id          = Column(String(8))
    customer_id        = Column(String(8))
    job_number         = Column(String(12))
    order_status       = Column(String(1))
    order_total        = Column(Numeric(12, 4))
    date_entered       = Column(Date)
    date_confirmed     = Column(Date)
    date_last_receipt  = Column(Date)
    buyer              = Column(String(24))
    confirmed_by       = Column(String(24))
    notes              = Column(Text)
    contact_first_name = Column(String(100))
    contact_last_name  = Column(String(100))
    last_modified      = Column(DateTime)
    cached_at          = Column(DateTime, server_default=func.now())


class PaceVendorCache(Base):
    __tablename__ = "pace_vendor_cache"

    vendor_id        = Column(String(8), primary_key=True)
    contact_first_name = Column(String(100))
    contact_last_name  = Column(String(100))
    contact_title    = Column(String(200))
    company_name = Column(String(60), nullable=True)
    email_address    = Column(String(255))
    fax_number       = Column(String(60))
    address1         = Column(String(100))
    city             = Column(String(100))
    state            = Column(String(6))
    active           = Column(Boolean)
    customer_number  = Column(String(32))
    default_currency = Column(String(3))
    cached_at        = Column(DateTime, server_default=func.now())


class PaceCustomerCache(Base):
    __tablename__ = "pace_customer_cache"

    customer_id        = Column(String(8), primary_key=True)
    cust_name          = Column(String(255))
    address1           = Column(String(100))
    city               = Column(String(100))
    state              = Column(String(6))
    email_address      = Column(String(255))
    phone_number       = Column(String(60))
    customer_status    = Column(String(1))
    contact_first_name = Column(String(100))
    contact_last_name  = Column(String(100))
    account_balance    = Column(Numeric(12, 2))
    cached_at          = Column(DateTime, server_default=func.now())

class PaceShipmentCache(Base):
    __tablename__ = "pace_shipment_cache"

    shipment_id        = Column(String(12),  primary_key=True)
    job_number         = Column(String(12),  nullable=False, index=True)
    shipped            = Column(Boolean())
    ship_date          = Column(Date)
    promise_date       = Column(Date)
    tracking_number    = Column(String(100))
    weight             = Column(Numeric(8, 2))
    ship_name          = Column(String(255))
    address1           = Column(String(255))
    city               = Column(String(100))
    state_id           = Column(String(20))
    zip                = Column(String(20))
    contact_first_name = Column(String(100))
    charges            = Column(String(100))
    account_number     = Column(String(50))
    cached_at          = Column(DateTime, server_default=func.now())


class ThreadTrackingLink(Base):
    __tablename__ = "thread_tracking_links"
    id              = Column(Integer, primary_key=True)
    thread_id       = Column(Integer, ForeignKey("threads.id", ondelete="CASCADE"), nullable=False)
    email_id        = Column(Integer, ForeignKey("emails.id", ondelete="SET NULL"), nullable=True)
    carrier         = Column(String(10), nullable=False)
    tracking_number = Column(String(50), nullable=False)
    link_source     = Column(Enum(LinkSource), nullable=False)
    created_at      = Column(DateTime, server_default=func.now())
    __table_args__  = (UniqueConstraint("thread_id", "tracking_number", name="uq_thread_tracking"),)