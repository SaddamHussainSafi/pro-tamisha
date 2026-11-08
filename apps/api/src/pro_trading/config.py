from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    # Fernet key (urlsafe base64, 32 bytes) used to encrypt TOTP secrets at rest.
    mfa_encryption_key: str = "dev-only-insecure-key-change-me-000000000000="
    mfa_issuer: str = "Pro Trading"
    # Module 05 invoicing. Tax rules are unverified (OPEN_DECISIONS D-03): production-like
    # environments refuse to issue until an accountant signs off and this is set true.
    invoice_tax_verified: bool = False
    invoice_prefix: str = Field(default="INV", pattern=r"^[A-Z]{1,8}$")
    # Module 06 scanner bridge. The capability key signs short-lived, single-job scan grants.
    scan_capability_key: str = "dev-only-scan-capability-key-change-me"
    scan_capability_ttl_seconds: int = Field(default=300, ge=30, le=900)
    scan_timeout_seconds: int = Field(default=180, ge=30, le=1800)
    scan_pairing_ttl_minutes: int = Field(default=10, ge=1, le=60)
    scan_max_bytes: int = Field(default=20 * 1024 * 1024, ge=1024, le=100 * 1024 * 1024)
    # Module 07 OCR. "fake" reads text drawn into synthetic PDFs (dev/test only); "document_ai"
    # calls Google Document AI server-side (unverified until the pilot); "disabled" = manual only.
    ocr_provider: Literal["fake", "tesseract", "document_ai", "disabled"] = "fake"
    ocr_tesseract_cmd: str = "tesseract"
    ocr_confidence_threshold: str = Field(default="0.900", pattern=r"^0\.\d{3}$")
    ocr_docai_endpoint: str = (
        ""  # https://{loc}-documentai.googleapis.com/v1/projects/.../processors/...:process
    )
    ocr_docai_credentials_file: str = ""  # service-account JSON; never sent to the browser
    ocr_timeout_seconds: int = Field(default=60, ge=5, le=600)
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
    # password and no MFA. Empty = off. The app refuses to start with it set in staging/production.
    dev_auto_login_email: str = ""

    @property
    def is_production_like(self) -> bool:
        return self.app_env in ("staging", "production")

    @model_validator(mode="after")
    def _no_dev_login_in_production(self) -> "Settings":
        if self.dev_auto_login_email and self.is_production_like:
            raise ValueError("DEV_AUTO_LOGIN_EMAIL must be empty in staging/production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
