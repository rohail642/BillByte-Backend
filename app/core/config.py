from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    ZOMATO_WEBHOOK_SECRET: str = ""
    SWIGGY_WEBHOOK_SECRET: str = ""
    APP_NAME: str = "BillByte"
    DEBUG: bool = False

    DATABASE_URL: str = "postgresql+asyncpg://user:pass@localhost/billbyte"
    SECRET_KEY: str = "change-me-in-production"
    ALGORITHM: str = "HS256"
    # Default reduced from 24h to 12h (one work shift). Tokens are now revocable
    # via token_version, so this primarily bounds the stolen-token window.
    # NOTE: a production env var ACCESS_TOKEN_EXPIRE_MINUTES will override this —
    # ensure it is not set to a multi-week value.
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 720

    ALLOWED_ORIGINS: str = "http://localhost:5500,http://127.0.0.1:5500"

    # When False (the default / production), interactive API docs and the
    # OpenAPI schema are not served. Set EXPOSE_DOCS=true only in dev.
    EXPOSE_DOCS: bool = False

    RAZORPAY_KEY_ID: str = ""
    RAZORPAY_KEY_SECRET: str = ""

    # ── Telegram daily reports ────────────────────────────────────────────────
    # Bot token from @BotFather. Empty = feature disabled (scheduler doesn't
    # start, webhook returns 503, Settings card shows "not configured").
    TELEGRAM_BOT_TOKEN: str = ""
    # Bot username WITHOUT the @ (e.g. "BillByteReportsBot") — used to build
    # t.me deep links. If empty, it is fetched once from getMe and cached.
    TELEGRAM_BOT_USERNAME: str = ""
    # Random string passed to setWebhook and verified on every webhook call
    # via the X-Telegram-Bot-Api-Secret-Token header. Required for webhook mode.
    TELEGRAM_WEBHOOK_SECRET: str = ""
    # Dev fallback: long-poll getUpdates instead of a public webhook. Never
    # enable in production alongside a webhook (Telegram allows only one).
    TELEGRAM_USE_POLLING: bool = False
    # When the nightly report goes out, in TIMEZONE local time.
    REPORT_SEND_HOUR: int = 23
    REPORT_SEND_MINUTE: int = 0
    TIMEZONE: str = "Asia/Kolkata"
    # Comma list of extra attachments per report: "pdf,csv", "csv", or "" (text only)
    REPORT_ATTACHMENTS: str = "pdf,csv"

    @property
    def origins_list(self) -> List[str]:
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",")]

    class Config:
        env_file = ".env"


def _assert_production_safety(s: "Settings") -> None:
    """Fail fast in production if the JWT signing secret was never changed."""
    if not s.DEBUG and s.SECRET_KEY in ("", "change-me-in-production"):
        raise RuntimeError(
            "SECRET_KEY must be set to a strong random value in production "
            "(set DEBUG=true to bypass this check in local development)."
        )


settings = Settings()
_assert_production_safety(settings)