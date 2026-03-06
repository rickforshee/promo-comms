"""
SQLAlchemy models for the promo-comms platform.

All 15 tables:
  Core:         emails, threads, attachments, users
  Workflow:     proofs, proof_history, notes, audit_log
  Linking:      thread_job_links, thread_po_links
  Pace cache:   pace_job_cache, pace_po_cache, pace_vendor_cache, pace_customer_cache
"""

import enum
from sqlalchemy import (
    Boolean, Column, Date, DateTime, Enum, ForeignKey,
    Integer, JSON, Numeric, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


# ─── Enums ────────────────────────────────────────────────────────────────────

class ProofStatus(enum.Enum):
    received            = "received"
    sent_for_approval   = "sent_for_approval"
    approved            = "approved"
    rejected            = "rejected"
    revision_requested  = "revision_requested"


# ─── Users ────────────────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id            = Column(Integer, primary_key=True)
    email         = Column(String(255), nullable=False, unique=True)
    display_name  = Column(String(255))
    is_active     = Column(Boolean, default=True)
    created_at    = Column(DateTime, server_default=func.now())

    notes         = relationship("Note", back_populates="author")
    audit_entries = relationship("AuditLog", back_populates="user")


# ─── Threads ──────────────────────────────────────────────────────────────────

class Thread(Base):
    __tablename__ = "threads"

    id                    = Column(Integer, primary_key=True)
    subject               = Column(String(998))
    conversation_id       = Column(String(255), unique=True)
    created_at            = Column(DateTime, server_default=func.now())
    updated_at            = Column(DateTime, server_default=func.now(), onupdate=func.now())

    emails                = relationship("Email", back_populates="thread")
    job_links             = relationship("ThreadJobLink", back_populates="thread")
    po_links              = relationship("ThreadPOLink", back_populates="thread")
    proofs                = relationship("Proof", back_populates="thread")
    notes                 = relationship("Note", back_populates="thread")


# ─── Emails ───────────────────────────────────────────────────────────────────

class Email(Base):
    __tablename__ = "emails"

    id                  = Column(Integer, primary_key=True)
    message_id          = Column(String(255), nullable=False, unique=True)
    thread_id           = Column(Integer, ForeignKey("threads.id"), nullable=True)
    subject             = Column(String(998))
    sender_name         = Column(String(255))
    sender_email        = Column(String(255))
    recipient_emails    = Column(JSON)
    body_text           = Column(Text)
    body_html           = Column(Text)
    received_at         = Column(DateTime)
    ingested_at         = Column(DateTime, server_default=func.now())
    import_source       = Column(String(50), default="realtime")  # 'realtime' or 'historical'
    has_attachments     = Column(Boolean, default=False)
    in_reply_to         = Column(String(255))
    references_header   = Column(Text)

    thread              = relationship("Thread", back_populates="emails")
    attachments         = relationship("Attachment", back_populates="email")


# ─── Attachments ──────────────────────────────────────────────────────────────

class Attachment(Base):
    __tablename__ = "attachments"

    id              = Column(Integer, primary_key=True)
    email_id        = Column(Integer, ForeignKey("emails.id"), nullable=False)
    filename        = Column(String(255))
    content_type    = Column(String(255))
    file_size       = Column(Integer)
    storage_path    = Column(String(1024))
    created_at      = Column(DateTime, server_default=func.now())

    email           = relationship("Email", back_populates="attachments")


# ─── Thread <-> Job / PO Links ────────────────────────────────────────────────

class ThreadJobLink(Base):
    __tablename__ = "thread_job_links"
    __table_args__ = (UniqueConstraint("thread_id", "job_number"),)

    id              = Column(Integer, primary_key=True)
    thread_id       = Column(Integer, ForeignKey("threads.id"), nullable=False)
    job_number      = Column(String(12), nullable=False)
    confidence      = Column(String(20), default="auto")  # 'auto' or 'manual'
    created_at      = Column(DateTime, server_default=func.now())

    thread          = relationship("Thread", back_populates="job_links")


class ThreadPOLink(Base):
    __tablename__ = "thread_po_links"
    __table_args__ = (UniqueConstraint("thread_id", "po_number"),)

    id              = Column(Integer, primary_key=True)
    thread_id       = Column(Integer, ForeignKey("threads.id"), nullable=False)
    po_number       = Column(String(8), nullable=False)
    confidence      = Column(String(20), default="auto")  # 'auto' or 'manual'
    created_at      = Column(DateTime, server_default=func.now())

    thread          = relationship("Thread", back_populates="po_links")


# ─── Proofs ───────────────────────────────────────────────────────────────────

class Proof(Base):
    __tablename__ = "proofs"

    id              = Column(Integer, primary_key=True)
    thread_id       = Column(Integer, ForeignKey("threads.id"), nullable=False)
    attachment_id   = Column(Integer, ForeignKey("attachments.id"), nullable=True)
    status          = Column(Enum(ProofStatus), nullable=False, default=ProofStatus.received)
    created_at      = Column(DateTime, server_default=func.now())
    updated_at      = Column(DateTime, server_default=func.now(), onupdate=func.now())

    thread          = relationship("Thread", back_populates="proofs")
    attachment      = relationship("Attachment")
    history         = relationship("ProofHistory", back_populates="proof")


class ProofHistory(Base):
    __tablename__ = "proof_history"

    id          = Column(Integer, primary_key=True)
    proof_id    = Column(Integer, ForeignKey("proofs.id"), nullable=False)
    status      = Column(Enum(ProofStatus), nullable=False)
    changed_by  = Column(Integer, ForeignKey("users.id"), nullable=False)
    changed_at  = Column(DateTime, server_default=func.now())
    notes       = Column(Text)

    proof       = relationship("Proof", back_populates="history")


# ─── Internal Notes ───────────────────────────────────────────────────────────

class Note(Base):
    __tablename__ = "notes"

    id          = Column(Integer, primary_key=True)
    thread_id   = Column(Integer, ForeignKey("threads.id"), nullable=False)
    author_id   = Column(Integer, ForeignKey("users.id"), nullable=False)
    content     = Column(Text, nullable=False)
    created_at  = Column(DateTime, server_default=func.now())

    thread      = relationship("Thread", back_populates="notes")
    author      = relationship("User", back_populates="notes")


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

    user        = relationship("User", back_populates="audit_entries")


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
    salesperson_id      = Column(Integer)
    csr_id              = Column(Integer)
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

    po_number           = Column(String(8), primary_key=True)
    pace_internal_id    = Column(Integer)
    vendor_id           = Column(String(8))
    customer_id         = Column(String(8))
    order_status        = Column(String(1))
    order_total         = Column(Numeric(12, 4))
    date_entered        = Column(Date)
    date_confirmed      = Column(Date)
    date_last_receipt   = Column(Date)
    buyer               = Column(String(24))
    confirmed_by        = Column(String(24))
    notes               = Column(Text)
    contact_first_name  = Column(String(100))
    contact_last_name   = Column(String(100))
    last_modified       = Column(DateTime)
    cached_at           = Column(DateTime, server_default=func.now())


class PaceVendorCache(Base):
    __tablename__ = "pace_vendor_cache"

    vendor_id           = Column(String(8), primary_key=True)
    contact_first_name  = Column(String(100))
    contact_last_name   = Column(String(100))
    contact_title       = Column(String(200))
    email_address       = Column(String(255))
    fax_number          = Column(String(60))
    address1            = Column(String(100))
    city                = Column(String(100))
    state               = Column(String(6))
    active              = Column(Boolean)
    customer_number     = Column(String(32))
    default_currency    = Column(String(3))
    cached_at           = Column(DateTime, server_default=func.now())


class PaceCustomerCache(Base):
    __tablename__ = "pace_customer_cache"

    customer_id         = Column(String(8), primary_key=True)
    cust_name           = Column(String(255))
    address1            = Column(String(100))
    city                = Column(String(100))
    state               = Column(String(6))
    email_address       = Column(String(255))
    phone_number        = Column(String(60))
    customer_status     = Column(String(1))
    contact_first_name  = Column(String(100))
    contact_last_name   = Column(String(100))
    account_balance     = Column(Numeric(12, 2))
    cached_at           = Column(DateTime, server_default=func.now())