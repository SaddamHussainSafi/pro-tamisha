"""Email module (Lark Mail): mailbox connections, access grants, folders, messages,
attachments, per-person read state, drafts, uploads, outgoing queue, sync runs and ERP links.
Also: employees.archived_at / archived_reason (user replacement: archived accounts are kept,
never deleted, and leave the normal user list), and permissions mail.use / mail.manage.

Separate from the Gmail Inbox tables (gmail_*), which this migration does not touch.

Rollback: `alembic downgrade 0029` drops only the new mail_* tables (imported copies of email;
nothing on Lark is affected), the two permissions and the two employee columns. Take a backup
first if mail has been imported (pg_dump -t 'mail_*').

Revision ID: 0030
Revises: 0029
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PERMS = {
    "mail.use": "Use the Email module for the mailboxes given to you (read and send)",
    "mail.manage": "Set up company mailboxes: Lark passwords, sync, access (Super Admin)",
}
GRANTS = {"OWNER_ADMIN": ["mail.use", "mail.manage"], "MANAGER": ["mail.use"]}
TABLES = [
    "mail_links", "mail_sync_runs", "mail_uploads", "mail_drafts", "mail_outgoing",
    "mail_user_state", "mail_attachment_blobs", "mail_attachments", "mail_messages",
    "mail_folders", "mail_access", "mail_accounts",
]  # fmt: skip

DDL = [
    """
CREATE TABLE mail_accounts (
    id UUID DEFAULT gen_random_uuid() NOT NULL, 
    company_id UUID NOT NULL, 
    address TEXT NOT NULL, 
    display_name TEXT DEFAULT '' NOT NULL, 
    label TEXT DEFAULT '' NOT NULL, 
    kind VARCHAR(16) DEFAULT 'PERSONAL' NOT NULL, 
    provider VARCHAR(16) DEFAULT 'lark' NOT NULL, 
    imap_host TEXT NOT NULL, 
    imap_port INTEGER DEFAULT '993' NOT NULL, 
    smtp_host TEXT NOT NULL, 
    smtp_port INTEGER DEFAULT '465' NOT NULL, 
    username TEXT NOT NULL, 
    password_enc BYTEA, 
    password_set_at TIMESTAMP WITH TIME ZONE, 
    password_set_by UUID, 
    status VARCHAR(16) DEFAULT 'NOT_CONFIGURED' NOT NULL, 
    sync_enabled BOOLEAN DEFAULT 'false' NOT NULL, 
    sync_interval_seconds INTEGER DEFAULT '120' NOT NULL, 
    imap_ok_at TIMESTAMP WITH TIME ZONE, 
    smtp_ok_at TIMESTAMP WITH TIME ZONE, 
    imap_error TEXT, 
    smtp_error TEXT, 
    capabilities JSONB DEFAULT '[]' NOT NULL, 
    last_sync_at TIMESTAMP WITH TIME ZONE, 
    last_sync_attempt_at TIMESTAMP WITH TIME ZONE, 
    next_sync_at TIMESTAMP WITH TIME ZONE, 
    failure_count INTEGER DEFAULT '0' NOT NULL, 
    last_error_code VARCHAR(48), 
    last_error TEXT, 
    last_error_at TIMESTAMP WITH TIME ZONE, 
    sync_lease_until TIMESTAMP WITH TIME ZONE, 
    backfill_done_at TIMESTAMP WITH TIME ZONE, 
    message_count INTEGER DEFAULT '0' NOT NULL, 
    unread_count INTEGER DEFAULT '0' NOT NULL, 
    signature_html TEXT DEFAULT '' NOT NULL, 
    created_by UUID, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    version INTEGER DEFAULT '1' NOT NULL, 
    PRIMARY KEY (id), 
    CONSTRAINT status_valid CHECK (status IN ('NOT_CONFIGURED','CONNECTED','ERROR','AUTH_FAILED','PAUSED')), 
    CONSTRAINT kind_valid CHECK (kind IN ('PERSONAL','SHARED')), 
    CONSTRAINT ports_valid CHECK (imap_port BETWEEN 1 AND 65535 AND smtp_port BETWEEN 1 AND 65535), 
    CONSTRAINT interval_valid CHECK (sync_interval_seconds BETWEEN 60 AND 86400), 
    CONSTRAINT configured_has_secret CHECK (status = 'NOT_CONFIGURED' OR password_enc IS NOT NULL), 
    FOREIGN KEY(company_id) REFERENCES companies (id), 
    FOREIGN KEY(password_set_by) REFERENCES employees (id), 
    FOREIGN KEY(created_by) REFERENCES employees (id)
)
""",
    """
CREATE UNIQUE INDEX uq_mail_account_address ON mail_accounts (company_id, lower(address))
""",
    """
CREATE TABLE mail_access (
    id UUID DEFAULT gen_random_uuid() NOT NULL, 
    company_id UUID NOT NULL, 
    account_id UUID NOT NULL, 
    employee_id UUID NOT NULL, 
    can_send BOOLEAN DEFAULT 'true' NOT NULL, 
    granted_by UUID, 
    granted_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    revoked_by UUID, 
    revoked_at TIMESTAMP WITH TIME ZONE, 
    PRIMARY KEY (id), 
    FOREIGN KEY(company_id) REFERENCES companies (id), 
    FOREIGN KEY(account_id) REFERENCES mail_accounts (id), 
    FOREIGN KEY(employee_id) REFERENCES employees (id), 
    FOREIGN KEY(granted_by) REFERENCES employees (id), 
    FOREIGN KEY(revoked_by) REFERENCES employees (id)
)
""",
    """
CREATE INDEX ix_mail_access_employee ON mail_access (employee_id)
""",
    """
CREATE UNIQUE INDEX uq_mail_access_live ON mail_access (account_id, employee_id) WHERE revoked_at IS NULL
""",
    """
CREATE TABLE mail_folders (
    id UUID DEFAULT gen_random_uuid() NOT NULL, 
    account_id UUID NOT NULL, 
    name TEXT NOT NULL, 
    raw_name TEXT NOT NULL, 
    display_name TEXT NOT NULL, 
    role VARCHAR(16) DEFAULT 'OTHER' NOT NULL, 
    attributes JSONB DEFAULT '[]' NOT NULL, 
    selectable BOOLEAN DEFAULT 'true' NOT NULL, 
    sync_enabled BOOLEAN DEFAULT 'true' NOT NULL, 
    uidvalidity BIGINT, 
    high_uid BIGINT DEFAULT '0' NOT NULL, 
    backfill_uid BIGINT, 
    backfill_done BOOLEAN DEFAULT 'false' NOT NULL, 
    server_count INTEGER DEFAULT '0' NOT NULL, 
    message_count INTEGER DEFAULT '0' NOT NULL, 
    unread_count INTEGER DEFAULT '0' NOT NULL, 
    last_synced_at TIMESTAMP WITH TIME ZONE, 
    gone_at TIMESTAMP WITH TIME ZONE, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id), 
    CONSTRAINT uq_mail_folder_name UNIQUE (account_id, name), 
    CONSTRAINT role_valid CHECK (role IN ('INBOX','SENT','DRAFTS','ARCHIVE','JUNK','TRASH','ALL','FLAGGED','OTHER')), 
    FOREIGN KEY(account_id) REFERENCES mail_accounts (id)
)
""",
    """
CREATE TABLE mail_messages (
    id UUID DEFAULT gen_random_uuid() NOT NULL, 
    company_id UUID NOT NULL, 
    account_id UUID NOT NULL, 
    folder_id UUID NOT NULL, 
    uidvalidity BIGINT NOT NULL, 
    uid BIGINT NOT NULL, 
    message_id_header TEXT DEFAULT '' NOT NULL, 
    in_reply_to TEXT DEFAULT '' NOT NULL, 
    "references" JSONB DEFAULT '[]' NOT NULL, 
    thread_key TEXT NOT NULL, 
    from_name TEXT DEFAULT '' NOT NULL, 
    from_email TEXT DEFAULT '' NOT NULL, 
    to_addrs JSONB DEFAULT '[]' NOT NULL, 
    cc_addrs JSONB DEFAULT '[]' NOT NULL, 
    bcc_addrs JSONB DEFAULT '[]' NOT NULL, 
    reply_to JSONB DEFAULT '[]' NOT NULL, 
    subject TEXT DEFAULT '' NOT NULL, 
    snippet TEXT DEFAULT '' NOT NULL, 
    body_text TEXT DEFAULT '' NOT NULL, 
    body_html TEXT DEFAULT '' NOT NULL, 
    body_truncated BOOLEAN DEFAULT 'false' NOT NULL, 
    has_remote_images BOOLEAN DEFAULT 'false' NOT NULL, 
    sent_at TIMESTAMP WITH TIME ZONE, 
    received_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    size INTEGER DEFAULT '0' NOT NULL, 
    flags JSONB DEFAULT '[]' NOT NULL, 
    seen BOOLEAN DEFAULT 'false' NOT NULL, 
    flagged BOOLEAN DEFAULT 'false' NOT NULL, 
    attachment_count INTEGER DEFAULT '0' NOT NULL, 
    attachment_names TEXT DEFAULT '' NOT NULL, 
    search_tsv TSVECTOR, 
    raw_sha256 VARCHAR(64) DEFAULT '' NOT NULL, 
    parse_warnings JSONB DEFAULT '[]' NOT NULL, 
    server_removed_at TIMESTAMP WITH TIME ZONE, 
    synced_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id), 
    CONSTRAINT uq_mail_message_uid UNIQUE (folder_id, uidvalidity, uid), 
    FOREIGN KEY(company_id) REFERENCES companies (id), 
    FOREIGN KEY(account_id) REFERENCES mail_accounts (id), 
    FOREIGN KEY(folder_id) REFERENCES mail_folders (id)
)
""",
    """
CREATE INDEX ix_mail_messages_account_date ON mail_messages (account_id, received_at)
""",
    """
CREATE INDEX ix_mail_messages_list ON mail_messages (folder_id, received_at, id)
""",
    """
CREATE INDEX ix_mail_messages_msgid ON mail_messages (account_id, message_id_header)
""",
    """
CREATE INDEX ix_mail_messages_search ON mail_messages USING gin (search_tsv)
""",
    """
CREATE INDEX ix_mail_messages_sha ON mail_messages (folder_id, raw_sha256)
""",
    """
CREATE INDEX ix_mail_messages_subject_trgm ON mail_messages USING gin (subject gin_trgm_ops)
""",
    """
CREATE INDEX ix_mail_messages_thread ON mail_messages (account_id, thread_key)
""",
    """
CREATE TABLE mail_attachments (
    id UUID DEFAULT gen_random_uuid() NOT NULL, 
    message_id UUID NOT NULL, 
    part_id VARCHAR(32) NOT NULL, 
    filename TEXT NOT NULL, 
    declared_mime VARCHAR(128) DEFAULT '' NOT NULL, 
    detected_mime VARCHAR(128) DEFAULT '' NOT NULL, 
    content_id TEXT DEFAULT '' NOT NULL, 
    inline BOOLEAN DEFAULT 'false' NOT NULL, 
    byte_size INTEGER DEFAULT '0' NOT NULL, 
    sha256 VARCHAR(64) DEFAULT '' NOT NULL, 
    stored BOOLEAN DEFAULT 'false' NOT NULL, 
    scan_status VARCHAR(16) DEFAULT 'NOT_SCANNED' NOT NULL, 
    scan_detail TEXT DEFAULT '' NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id), 
    CONSTRAINT uq_mail_attachment_part UNIQUE (message_id, part_id), 
    CONSTRAINT size_valid CHECK (byte_size >= 0), 
    FOREIGN KEY(message_id) REFERENCES mail_messages (id)
)
""",
    """
CREATE INDEX ix_mail_attachments_message_id ON mail_attachments (message_id)
""",
    """
CREATE INDEX ix_mail_attachments_sha ON mail_attachments (sha256)
""",
    """
CREATE TABLE mail_attachment_blobs (
    attachment_id UUID NOT NULL, 
    content BYTEA NOT NULL, 
    PRIMARY KEY (attachment_id), 
    FOREIGN KEY(attachment_id) REFERENCES mail_attachments (id)
)
""",
    """
CREATE TABLE mail_user_state (
    message_id UUID NOT NULL, 
    employee_id UUID NOT NULL, 
    read_at TIMESTAMP WITH TIME ZONE, 
    starred BOOLEAN DEFAULT 'false' NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (message_id, employee_id), 
    FOREIGN KEY(message_id) REFERENCES mail_messages (id), 
    FOREIGN KEY(employee_id) REFERENCES employees (id)
)
""",
    """
CREATE TABLE mail_outgoing (
    id UUID DEFAULT gen_random_uuid() NOT NULL, 
    company_id UUID NOT NULL, 
    account_id UUID NOT NULL, 
    employee_id UUID NOT NULL, 
    idempotency_key VARCHAR(128) NOT NULL, 
    mode VARCHAR(16) DEFAULT 'new' NOT NULL, 
    source_message_id UUID, 
    message_id_header TEXT NOT NULL, 
    from_addr TEXT NOT NULL, 
    to_addrs JSONB DEFAULT '[]' NOT NULL, 
    cc_addrs JSONB DEFAULT '[]' NOT NULL, 
    bcc_addrs JSONB DEFAULT '[]' NOT NULL, 
    subject TEXT DEFAULT '' NOT NULL, 
    raw_mime BYTEA NOT NULL, 
    status VARCHAR(16) DEFAULT 'QUEUED' NOT NULL, 
    attempts INTEGER DEFAULT '0' NOT NULL, 
    next_attempt_at TIMESTAMP WITH TIME ZONE, 
    last_error TEXT, 
    smtp_response TEXT, 
    sent_at TIMESTAMP WITH TIME ZONE, 
    seen_in_sent_at TIMESTAMP WITH TIME ZONE, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id), 
    CONSTRAINT status_valid CHECK (status IN ('QUEUED','SENDING','SENT','FAILED','UNCERTAIN')), 
    CONSTRAINT uq_mail_outgoing_idem UNIQUE (account_id, idempotency_key), 
    FOREIGN KEY(company_id) REFERENCES companies (id), 
    FOREIGN KEY(account_id) REFERENCES mail_accounts (id), 
    FOREIGN KEY(employee_id) REFERENCES employees (id), 
    FOREIGN KEY(source_message_id) REFERENCES mail_messages (id)
)
""",
    """
CREATE INDEX ix_mail_outgoing_status ON mail_outgoing (status, next_attempt_at)
""",
    """
CREATE TABLE mail_drafts (
    id UUID DEFAULT gen_random_uuid() NOT NULL, 
    company_id UUID NOT NULL, 
    account_id UUID NOT NULL, 
    employee_id UUID NOT NULL, 
    mode VARCHAR(16) DEFAULT 'new' NOT NULL, 
    source_message_id UUID, 
    to_addrs JSONB DEFAULT '[]' NOT NULL, 
    cc_addrs JSONB DEFAULT '[]' NOT NULL, 
    bcc_addrs JSONB DEFAULT '[]' NOT NULL, 
    subject TEXT DEFAULT '' NOT NULL, 
    body_html TEXT DEFAULT '' NOT NULL, 
    attachment_ids JSONB DEFAULT '[]' NOT NULL, 
    sent_outgoing_id UUID, 
    discarded_at TIMESTAMP WITH TIME ZONE, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(company_id) REFERENCES companies (id), 
    FOREIGN KEY(account_id) REFERENCES mail_accounts (id), 
    FOREIGN KEY(employee_id) REFERENCES employees (id), 
    FOREIGN KEY(source_message_id) REFERENCES mail_messages (id), 
    FOREIGN KEY(sent_outgoing_id) REFERENCES mail_outgoing (id)
)
""",
    """
CREATE INDEX ix_mail_drafts_owner ON mail_drafts (account_id, employee_id, updated_at)
""",
    """
CREATE TABLE mail_uploads (
    id UUID DEFAULT gen_random_uuid() NOT NULL, 
    company_id UUID NOT NULL, 
    employee_id UUID NOT NULL, 
    filename TEXT NOT NULL, 
    mime_type VARCHAR(128) NOT NULL, 
    byte_size INTEGER NOT NULL, 
    sha256 VARCHAR(64) NOT NULL, 
    content BYTEA NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(company_id) REFERENCES companies (id), 
    FOREIGN KEY(employee_id) REFERENCES employees (id)
)
""",
    """
CREATE TABLE mail_sync_runs (
    id UUID DEFAULT gen_random_uuid() NOT NULL, 
    account_id UUID NOT NULL, 
    trigger VARCHAR(16) NOT NULL, 
    started_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    finished_at TIMESTAMP WITH TIME ZONE, 
    status VARCHAR(16) DEFAULT 'RUNNING' NOT NULL, 
    new_messages INTEGER DEFAULT '0' NOT NULL, 
    backfilled INTEGER DEFAULT '0' NOT NULL, 
    folders INTEGER DEFAULT '0' NOT NULL, 
    error_code VARCHAR(48), 
    error TEXT, 
    PRIMARY KEY (id), 
    FOREIGN KEY(account_id) REFERENCES mail_accounts (id)
)
""",
    """
CREATE INDEX ix_mail_sync_runs_account ON mail_sync_runs (account_id, started_at)
""",
    """
CREATE TABLE mail_links (
    id UUID DEFAULT gen_random_uuid() NOT NULL, 
    company_id UUID NOT NULL, 
    message_id UUID NOT NULL, 
    entity_type VARCHAR(24) NOT NULL, 
    entity_id UUID NOT NULL, 
    note TEXT DEFAULT '' NOT NULL, 
    created_by UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    removed_by UUID, 
    removed_at TIMESTAMP WITH TIME ZONE, 
    PRIMARY KEY (id), 
    CONSTRAINT entity_type_valid CHECK (entity_type IN ('customer','vendor','sales_order','invoice','shipment','payment')), 
    FOREIGN KEY(company_id) REFERENCES companies (id), 
    FOREIGN KEY(message_id) REFERENCES mail_messages (id), 
    FOREIGN KEY(created_by) REFERENCES employees (id), 
    FOREIGN KEY(removed_by) REFERENCES employees (id)
)
""",
    """
CREATE INDEX ix_mail_links_entity ON mail_links (entity_type, entity_id)
""",
    """
CREATE UNIQUE INDEX uq_mail_link_live ON mail_links (message_id, entity_type, entity_id) WHERE removed_at IS NULL
""",
]


def upgrade() -> None:
    for stmt in DDL:
        op.execute(stmt)
    op.add_column("employees", sa.Column("archived_at", sa.DateTime(timezone=True)))
    op.add_column("employees", sa.Column("archived_reason", sa.Text()))
    bind = op.get_bind()
    for code, desc in PERMS.items():
        bind.execute(
            sa.text(
                "INSERT INTO permissions (code, description) VALUES (:c, :d) "
                "ON CONFLICT (code) DO UPDATE SET description = EXCLUDED.description"
            ),
            {"c": code, "d": desc},
        )
    for role, codes in GRANTS.items():
        for code in codes:
            bind.execute(
                sa.text(
                    "INSERT INTO role_permissions (role_id, permission_code) "
                    "SELECT id, :c FROM roles WHERE code = :r ON CONFLICT DO NOTHING"
                ),
                {"c": code, "r": role},
            )


def downgrade() -> None:
    bind = op.get_bind()
    codes = list(PERMS)
    bind.execute(
        sa.text("DELETE FROM role_permissions WHERE permission_code = ANY(:c)"), {"c": codes}
    )
    bind.execute(sa.text("DELETE FROM permissions WHERE code = ANY(:c)"), {"c": codes})
    op.drop_column("employees", "archived_reason")
    op.drop_column("employees", "archived_at")
    for t in TABLES:
        op.drop_table(t)
