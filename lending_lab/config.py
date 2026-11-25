from functools import lru_cache
from urllib.parse import urlparse

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_mode: str = "production"
    database_url: str = "postgresql://lending_runtime:lending-runtime-demo@localhost:5432/lending_lab"
    migration_database_url: str = "postgresql://lending_migrator:lending-migrator-demo@localhost:5432/lending_lab"
    lab_database_url: str = "postgresql://lending_lab_operator:lending-lab-demo@localhost:5432/lending_lab"
    session_signing_secret: str = "local-only-change-before-use"
    approval_hmac_secret: str = "local-only-approval-hmac-key-change-before-use"
    approval_hmac_key_version: str = "demo-v1"
    privacy_hmac_secret: str = "local-only-privacy-hmac-key-change-before-use"
    privacy_hmac_key_version: str = "privacy-demo-v1"
    ai_mode: str = "deterministic"
    gemini_api_key: str | None = None
    gemini_model: str | None = None
    gemini_timeout_seconds: int = 5
    assistant_max_question_chars: int = 1000
    session_ttl_seconds: int = 1800
    demo_borrower_password: str = "borrower-demo"
    demo_operator_password: str = "operator-demo"
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    def validate_startup(self) -> None:
        """Refuse sample secrets outside an explicitly local-only demo runtime."""
        sample_secret = self.session_signing_secret == "local-only-change-before-use"
        sample_approval_key = self.approval_hmac_secret == "local-only-approval-hmac-key-change-before-use"
        sample_privacy_key = self.privacy_hmac_secret == "local-only-privacy-hmac-key-change-before-use"
        if self.app_mode == "local_demo":
            urls = (self.database_url, self.migration_database_url, self.lab_database_url)
            hosts = {urlparse(url).hostname for url in urls}
            if not hosts.issubset({"localhost", "127.0.0.1", "::1", "db"}):
                raise ValueError("local_demo database hosts must be loopback or the Compose db service")
            if self.ai_mode not in {"deterministic", "mock", "gemini"}:
                raise ValueError("AI_MODE must be deterministic, mock, or gemini")
            if not 1 <= self.gemini_timeout_seconds <= 10:
                raise ValueError("GEMINI_TIMEOUT_SECONDS must be between 1 and 10")
            if not 1 <= self.assistant_max_question_chars <= 1000:
                raise ValueError("ASSISTANT_MAX_QUESTION_CHARS must be between 1 and 1000")
            return
        if self.app_mode != "production":
            raise ValueError("APP_MODE must be production or explicit local_demo")
        if self.ai_mode not in {"deterministic", "mock", "gemini"}:
            raise ValueError("AI_MODE must be deterministic, mock, or gemini")
        if not 1 <= self.gemini_timeout_seconds <= 10:
            raise ValueError("GEMINI_TIMEOUT_SECONDS must be between 1 and 10")
        if not 1 <= self.assistant_max_question_chars <= 1000:
            raise ValueError("ASSISTANT_MAX_QUESTION_CHARS must be between 1 and 1000")
        # Fixed demo passwords cannot authenticate in production (6B/6C);
        # requiring unused demo passwords to be configured would be misleading.
        if sample_secret:
            raise ValueError("sample demo secrets are refused outside APP_MODE=local_demo")
        if sample_approval_key:
            raise ValueError("sample approval HMAC key is refused outside APP_MODE=local_demo")
        if sample_privacy_key:
            raise ValueError("sample privacy HMAC key is refused outside APP_MODE=local_demo")
        if len(self.session_signing_secret.encode()) < 32:
            raise ValueError("SESSION_SIGNING_SECRET must contain at least 32 bytes")
        if len(self.approval_hmac_secret.encode()) < 32 or not self.approval_hmac_key_version:
            raise ValueError("approval HMAC key and version must be configured")
        if len(self.privacy_hmac_secret.encode()) < 32 or not self.privacy_hmac_key_version:
            raise ValueError("privacy HMAC key and version must be configured")


@lru_cache
def get_settings() -> Settings:
    return Settings()
