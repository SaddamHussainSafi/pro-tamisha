from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from cryptography.fernet import Fernet
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_MFA_KEY = "dev-only-insecure-key-change-me-000000000000="
DEV_SCAN_KEY = "dev-only-scan-capability-key-change-me"
DEV_ORDER_CONNECT_KEY = "dev-only-order-connect-key-change-me"
DEV_GMAIL_KEY = "dev-only-gmail-token-key-change-me"
DEV_LARK_MAIL_KEY = "dev-only-lark-mail-key-change-me"
GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: Literal["development", "test", "staging", "production"] = "development"
    app_name: str = "Pro Trading"
    database_url: str = "postgresql+psycopg://tami@127.0.0.1:5432/tami"
    redis_url: str = "redis://127.0.0.1:6379/0"
    frontend_origin: str = "http://localhost:5173"
    cookie_secure: bool = False
    session_cookie_name: str = "tami_session"
    session_ttl_minutes: int = Field(default=480, ge=5, le=1440)
    session_idle_minutes: int = Field(default=60, ge=5, le=1440)
    login_max_failures: int = 5
    login_lockout_minutes: int = 15
    # Failed sign-ins allowed per client IP per window (password spraying across accounts).
    login_ip_max_failures: int = Field(default=30, ge=1)
    login_ip_window_minutes: int = Field(default=15, ge=1, le=1440)
    # Wrong MFA codes allowed per employee per window before verification is refused.
    mfa_max_failures: int = Field(default=5, ge=1)
    mfa_window_minutes: int = Field(default=15, ge=1, le=1440)
    # Invitation and password-reset links (single use; only their SHA-256 is stored).
    invite_ttl_hours: int = Field(default=72, ge=1, le=336)
    password_reset_ttl_minutes: int = Field(default=60, ge=10, le=1440)
    # Reset emails per account per hour (stops mail bombing a known address).
    password_reset_max_per_hour: int = Field(default=3, ge=1, le=20)
    # Fernet key (urlsafe base64, 32 bytes) used to encrypt TOTP secrets at rest.
    mfa_encryption_key: str = DEV_MFA_KEY
    mfa_issuer: str = "Pro Trading"
    # Module 05 invoicing. Tax rules are unverified (OPEN_DECISIONS D-03): production-like
    # environments refuse to issue until an accountant signs off and this is set true.
    invoice_tax_verified: bool = False
    # Invoice numbers are `{prefix}{year}-{n:06d}`, e.g. TT2026-000001 (D-11/D-12).
    invoice_prefix: str = Field(default="TT", pattern=r"^[A-Z]{1,8}$")
    # Module 06 scanner bridge. The capability key signs short-lived, single-job scan grants.
    scan_capability_key: str = DEV_SCAN_KEY
    scan_capability_ttl_seconds: int = Field(default=300, ge=30, le=900)
    scan_timeout_seconds: int = Field(default=180, ge=30, le=1800)
    scan_pairing_ttl_minutes: int = Field(default=10, ge=1, le=60)
    # Cap for every non-upload request body (JSON). Uploads use SCAN_MAX_BYTES.
    max_request_bytes: int = Field(default=1024 * 1024, ge=16 * 1024, le=50 * 1024 * 1024)
    scan_max_bytes: int = Field(default=20 * 1024 * 1024, ge=1024, le=100 * 1024 * 1024)
    # Module 07 OCR. "fake" reads text drawn into synthetic PDFs (dev/test only); "document_ai"
    # calls Google Document AI server-side (unverified until the pilot); "disabled" = manual only.
    # "gemini" sends each page to Google's Gemini API (handwriting) and checks the answer here.
    ocr_provider: Literal["fake", "tesseract", "document_ai", "gemini", "disabled"] = "fake"
    ocr_tesseract_cmd: str = "tesseract"
    ocr_confidence_threshold: str = Field(default="0.900", pattern=r"^0\.\d{3}$")
    ocr_docai_endpoint: str = (
        ""  # https://{loc}-documentai.googleapis.com/v1/projects/.../processors/...:process
    )
    ocr_docai_credentials_file: str = ""  # service-account JSON; never sent to the browser
    ocr_timeout_seconds: int = Field(default=60, ge=5, le=600)
    # A reading still queued with no reader working on it and no progress for this long is
    # stopped as OCR_TIMEOUT (the worker is down or gave up); the reviewer can read it again.
    ocr_stall_seconds: int = Field(default=180, ge=30, le=3600)
    # Gemini OCR (OCR_PROVIDER=gemini). The key is read from the server's environment only: it
    # is never logged, returned by the API or sent to the browser. GEMINI_MODEL must be a model
    # code exactly as Google lists it. Free plan limits measured in AI Studio on 2026-11-11 for
    # gemini-3.8-flash: 5 requests/minute, 250,000 tokens/minute, 20 requests/day; the caps below
    # keep the reader inside them. When the cap is used up, Google is unreachable or the answer
    # is not valid, the page is read by OCR_FALLBACK_PROVIDER instead and the reading says so.
    # Nothing here ever switches to another Google product or to a paid plan.
    gemini_api_key: SecretStr = SecretStr("")
    gemini_model: str = Field(default="gemini-3.8-flash", pattern=r"^[a-z0-9][a-z0-9.\-]{2,63}$")
    # Google serves the same models through two APIs. "generate_content" is the long-standing
    # one; "interactions" is the newer one (it answered 503 "high demand" on the free plan on
    # 2026-11-11 while generate_content answered normally, so it is not the default).
    gemini_api: Literal["generate_content", "interactions"] = "generate_content"
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    gemini_api_revision: str = "2026-05-20"  # Interactions API only
    gemini_requests_per_minute: int = Field(default=5, ge=1, le=600)
    gemini_daily_request_cap: int = Field(default=20, ge=0, le=100_000)
    gemini_timeout_seconds: int = Field(default=45, ge=10, le=600)
    # Stage A (kind of document, customer, order reference) as its own low-resolution request
    # for the whole document. Off: stage B answers those from the full page in the same request
    # (one request less per document; on real sheets the extra request cost 2-30 s and disagreed
    # with stage B mostly through its own misreads).
    gemini_separate_stage_a: bool = False
    gemini_max_retries: int = Field(default=2, ge=0, le=5)
    # After this many failed calls in a row nothing is sent for the cool-down (circuit breaker).
    # Unclear rows are read a second time (one more request per page) only while more than
    # this many requests of the day are left, so new pages always come first.
    gemini_recheck_reserve: int = Field(default=5, ge=0, le=100_000)
    gemini_breaker_failures: int = Field(default=3, ge=1, le=20)
    gemini_breaker_seconds: int = Field(default=300, ge=10, le=86_400)
    # On the free plan Google may use what is sent to improve its products and its staff may
    # read it. Nothing is sent until the owner has agreed and set this to true.
    gemini_data_consent: bool = False
    ocr_fallback_provider: Literal["tesseract", "disabled"] = "tesseract"
    # A page should be read in about this long. A slower page keeps going (progress is shown);
    # the time is recorded with the reading and a slow document gets a SLOW_READING warning.
    ocr_target_seconds: int = Field(default=25, ge=5, le=600)
    # Module 09 document filing. "local" copies filed documents into drive_local_root (dev/test
    # stand-in for Google Drive); "google" uses the Drive v3 API with a service account
    # (unverified until D-15 Workspace decision); "disabled" keeps documents in the database only.
    drive_provider: Literal["local", "google", "disabled"] = "local"
    drive_local_root: str = "var/drive"
    drive_root_folder_id: str = ""  # Google Drive folder that holds the company archive
    drive_credentials_file: str = ""  # service-account JSON; never sent to the browser
    drive_timeout_seconds: int = Field(default=30, ge=5, le=300)
    drive_fault: Literal["", "timeout", "denied"] = ""  # failure injection (dev/test only)
    # Module 10 payments. Manual ledger only: no bank login or bank API secret exists here.
    # Instructions and reminder days are placeholders until OPEN_DECISIONS D-16 is answered.
    payment_instructions: str = (
        "Pay by Interac e-Transfer to payments@example.test or EFT to the account on the "
        "invoice, quoting the invoice number. (Placeholder pending D-16.)"
    )
    payment_reminder_offsets: str = Field(default="-3,1,14", pattern=r"^-?\d{1,3}(,-?\d{1,3})*$")
    # Development only: POST /auth/dev-login signs the browser in as this employee with no
    # password and no MFA. Empty = off. The app refuses to start with it set in staging/production
    # or with a non-localhost FRONTEND_ORIGIN, and the route only answers loopback requests.
    dev_auto_login_email: str = ""
    # Order Connect (public website order forms). PUBLIC_APP_ORIGIN serves /embed/{id} and the
    # public form API (empty = FRONTEND_ORIGIN). ORDER_CONNECT_KEY signs form-load tokens and
    # keys the stored IP hashes; it never reaches the browser.
    public_app_origin: str = ""
    order_connect_key: str = DEV_ORDER_CONNECT_KEY
    order_connect_max_body_bytes: int = Field(default=64 * 1024, ge=4 * 1024, le=512 * 1024)
    # Gmail Inbox (docs/modules/15-gmail-inbox.md). One dedicated mailbox, read-only
    # (scope gmail.readonly: nothing here can send, delete or change mail). Everything is
    # server-side: the client secret and the tokens never reach the browser. Empty
    # GMAIL_CLIENT_ID = the feature is off and the page says "not set up".
    gmail_client_id: str = ""
    gmail_client_secret: SecretStr = SecretStr("")
    # Where Google sends the person back: {FRONTEND_ORIGIN}/gmail/oauth/callback when empty.
    # Must be listed exactly under the OAuth client's "Authorized redirect URIs".
    gmail_redirect_uri: str = ""
    # Fernet key (urlsafe base64, 32 bytes) that encrypts the stored OAuth tokens.
    gmail_token_key: str = DEV_GMAIL_KEY
    # Optional: only this mailbox may be connected (stops a personal account by mistake).
    gmail_allowed_address: str = ""
    # Pub/Sub topic Gmail publishes to (projects/<project>/topics/<topic>). Empty = no push;
    # the worker still checks the mailbox every GMAIL_POLL_SECONDS.
    gmail_pubsub_topic: str = ""
    # POST /api/v1/gmail/push is refused unless the caller proves it is that subscription:
    # the OIDC token Google signs (audience + service account), and/or a shared ?token=.
    gmail_push_audience: str = ""
    gmail_push_service_account: str = ""
    gmail_push_token: SecretStr = SecretStr("")
    gmail_poll_seconds: int = Field(default=60, ge=15, le=3600)
    # How far back the first import goes (0 = the whole mailbox), and messages per step.
    gmail_backfill_days: int = Field(default=0, ge=0, le=3650)
    gmail_backfill_batch: int = Field(default=50, ge=1, le=200)
    gmail_api_base: str = "https://gmail.googleapis.com/gmail/v1"
    gmail_oauth_auth_url: str = "https://accounts.google.com/o/oauth2/v2/auth"
    gmail_oauth_token_url: str = "https://oauth2.googleapis.com/token"  # noqa: S105
    gmail_oauth_revoke_url: str = "https://oauth2.googleapis.com/revoke"
    gmail_oauth_certs_url: str = "https://www.googleapis.com/oauth2/v3/certs"
    gmail_timeout_seconds: int = Field(default=30, ge=5, le=120)
    gmail_max_retries: int = Field(default=3, ge=0, le=6)
    # Stored body text / sanitized HTML per email (longer bodies are cut and marked).
    gmail_max_body_chars: int = Field(default=200_000, ge=1_000, le=2_000_000)
    # Email module (Lark Mail, docs/modules/16-email-lark.md). Company mailboxes are added by a
    # Super Admin in Settings > Integrations > Email Services; the dedicated Lark app password is
    # typed there and stored encrypted with LARK_MAIL_KEY (Fernet). Separate from Gmail Inbox.
    lark_mail_key: str = DEV_LARK_MAIL_KEY
    lark_imap_host: str = "imap.larksuite.com"
    lark_imap_port: int = 993
    lark_smtp_host: str = "smtp.larksuite.com"
    lark_smtp_port: int = 465
    # Only addresses on this domain may be added (stops a personal address by mistake).
    lark_mail_domain: str = "tamishatrading.com"
    # Plain (non-TLS) IMAP/SMTP: only for the local test servers in tests/; refused otherwise.
    lark_mail_allow_plaintext: bool = False
    lark_mail_timeout_seconds: int = Field(default=30, ge=5, le=120)
    # Messages fetched per step; history is imported newest first, this many per folder per run.
    lark_mail_fetch_batch: int = Field(default=25, ge=1, le=200)
    lark_mail_backfill_per_run: int = Field(default=200, ge=1, le=5000)
    lark_mail_max_message_bytes: int = Field(default=50 * 1024 * 1024, ge=1024)
    lark_mail_max_attachment_bytes: int = Field(default=25 * 1024 * 1024, ge=1024)
    lark_mail_max_body_chars: int = Field(default=400_000, ge=1_000, le=4_000_000)
    lark_mail_flag_window: int = Field(default=1000, ge=0, le=20_000)
    # Optional ClamAV daemon for attachment scanning (empty = attachments show "Not scanned").
    clamd_host: str = ""
    clamd_port: int = 3310

    @property
    def public_origin(self) -> str:
        return (self.public_app_origin or self.frontend_origin).rstrip("/")

    @property
    def gmail_configured(self) -> bool:
        return bool(self.gmail_client_id and self.gmail_client_secret.get_secret_value())

    @property
    def gmail_redirect(self) -> str:
        return self.gmail_redirect_uri or f"{self.frontend_origin.rstrip('/')}/gmail/oauth/callback"

    @property
    def gmail_push_configured(self) -> bool:
        """Push can be verified: an OIDC identity to expect, or a shared token."""
        return bool(
            (self.gmail_push_audience and self.gmail_push_service_account)
            or self.gmail_push_token.get_secret_value()
        )

    @property
    def is_production_like(self) -> bool:
        return self.app_env in ("staging", "production")

    @property
    def frontend_is_local(self) -> bool:
        return urlsplit(self.frontend_origin).hostname in LOCAL_HOSTS

    @model_validator(mode="after")
    def _no_dev_login_in_production(self) -> "Settings":
        if self.app_env == "development" and not self.frontend_is_local:
            # A deployed server that forgot APP_ENV would otherwise skip every production check.
            raise ValueError(
                "APP_ENV is development but FRONTEND_ORIGIN is not localhost: set APP_ENV to "
                "staging or production on a deployed server"
            )
        if self.dev_auto_login_email and (self.is_production_like or not self.frontend_is_local):
            raise ValueError(
                "DEV_AUTO_LOGIN_EMAIL only works in development with a localhost FRONTEND_ORIGIN"
            )
        return self

    @model_validator(mode="after")
    def _production_secrets(self) -> "Settings":
        """Staging/production refuse to start on the public dev placeholders or plain-HTTP
        cookies, so a forgotten env var fails loudly instead of running insecurely."""
        if not self.is_production_like:
            return self
        problems = []
        if self.mfa_encryption_key == DEV_MFA_KEY:
            problems.append("MFA_ENCRYPTION_KEY is the dev placeholder")
        else:
            try:
                Fernet(self.mfa_encryption_key)
            except ValueError:
                problems.append("MFA_ENCRYPTION_KEY is not a valid Fernet key")
        if self.scan_capability_key == DEV_SCAN_KEY or len(self.scan_capability_key) < 32:
            problems.append("SCAN_CAPABILITY_KEY is the dev placeholder or shorter than 32 chars")
        if not self.cookie_secure:
            problems.append("COOKIE_SECURE must be true")
        if not self.frontend_origin.startswith("https://"):
            problems.append("FRONTEND_ORIGIN must be https")
        if self.order_connect_key == DEV_ORDER_CONNECT_KEY or len(self.order_connect_key) < 32:
            problems.append("ORDER_CONNECT_KEY is the dev placeholder or shorter than 32 chars")
        if not self.public_origin.startswith("https://"):
            problems.append("PUBLIC_APP_ORIGIN must be https")
        if self.ocr_provider == "fake":
            problems.append("OCR_PROVIDER=fake is for dev/test only")
        if self.ocr_provider == "gemini" and not self.gemini_api_key.get_secret_value():
            problems.append("OCR_PROVIDER=gemini needs GEMINI_API_KEY")
        if not self.gemini_base_url.startswith("https://"):
            problems.append("GEMINI_BASE_URL must be https")
        if self.drive_fault:
            problems.append("DRIVE_FAULT is for dev/test only")
        if self.gmail_configured:
            if self.gmail_token_key == DEV_GMAIL_KEY:
                problems.append("GMAIL_TOKEN_KEY is the dev placeholder")
            else:
                try:
                    Fernet(self.gmail_token_key)
                except ValueError:
                    problems.append("GMAIL_TOKEN_KEY is not a valid Fernet key")
            if not self.gmail_redirect.startswith("https://"):
                problems.append("GMAIL_REDIRECT_URI must be https")
            if self.gmail_pubsub_topic and not self.gmail_push_configured:
                problems.append(
                    "GMAIL_PUBSUB_TOPIC needs GMAIL_PUSH_AUDIENCE + GMAIL_PUSH_SERVICE_ACCOUNT "
                    "(or GMAIL_PUSH_TOKEN) so pushes can be verified"
                )
        if self.lark_mail_key != DEV_LARK_MAIL_KEY:
            try:
                Fernet(self.lark_mail_key)
            except ValueError:
                problems.append("LARK_MAIL_KEY is not a valid Fernet key")
        if self.lark_mail_allow_plaintext:
            problems.append("LARK_MAIL_ALLOW_PLAINTEXT is for tests only")
        for name in ("gmail_api_base", "gmail_oauth_token_url", "gmail_oauth_certs_url"):
            if not str(getattr(self, name)).startswith("https://"):
                problems.append(f"{name.upper()} must be https")
        if problems:
            raise ValueError("Unsafe production settings: " + "; ".join(problems))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
