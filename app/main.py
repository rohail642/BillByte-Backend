import asyncio
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager, suppress
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from sqlalchemy import text
from app.core.config import settings
from app.core.limiter import limiter
from app.db.session import create_all_tables, engine
from app.routers import auth, menu, orders, inventory, customers, staff, reports, recipes, webhooks, admin, payments, cancellations, telegram
from app.services import telegram as telegram_service
from app.services.report_scheduler import start_report_scheduler, stop_report_scheduler
from app.models import admin_models  # noqa: F401 — ensures tables are created
from app.models import payment as payment_model  # noqa: F401 — ensures payment_transactions table is created


async def _apply_schema_additions():
    """Idempotent column additions that create_all() won't add to existing tables."""
    stmts = [
        "ALTER TABLE restaurants ADD COLUMN IF NOT EXISTS round_off BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE restaurants ADD COLUMN IF NOT EXISTS loyalty_enabled BOOLEAN NOT NULL DEFAULT TRUE",
        "ALTER TABLE restaurants ADD COLUMN IF NOT EXISTS pinelabs_enabled BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE restaurants ADD COLUMN IF NOT EXISTS pinelabs_merchant_id VARCHAR(200)",
        "ALTER TABLE restaurants ADD COLUMN IF NOT EXISTS pinelabs_terminal_id VARCHAR(200)",
        "ALTER TABLE restaurants ADD COLUMN IF NOT EXISTS pinelabs_security_token VARCHAR(500)",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS gst_rate FLOAT NOT NULL DEFAULT 5.0",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS token_version INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE restaurants ADD COLUMN IF NOT EXISTS printer_config JSONB",
        "ALTER TABLE restaurants ADD COLUMN IF NOT EXISTS manager_pin VARCHAR(128)",
        "ALTER TABLE cancellation_events ADD COLUMN IF NOT EXISTS quantity INTEGER NOT NULL DEFAULT 0",
        # Telegram daily reports (see app/routers/telegram.py)
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS telegram_chat_id BIGINT",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS telegram_username VARCHAR(64)",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS telegram_linked_at TIMESTAMPTZ",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS telegram_enabled BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_report_sent_at TIMESTAMPTZ",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_delivery_status VARCHAR(200)",
        "CREATE INDEX IF NOT EXISTS ix_users_telegram_chat_id ON users (telegram_chat_id)",
        # Per-menu-item stock counter (NULL = not tracked)
        "ALTER TABLE menu_items ADD COLUMN IF NOT EXISTS stock_qty INTEGER",
        # Backfill gst_rate for existing orders by deriving it from stored amounts
        """
        UPDATE orders
        SET gst_rate = ROUND(
            (gst_amount / NULLIF(subtotal - discount_amount, 0) * 100)::NUMERIC,
            1
        )
        WHERE gst_amount > 0 AND subtotal > discount_amount AND ABS(gst_rate - 5.0) < 0.01
        """,
    ]
    # One transaction PER statement: batching them holds AccessExclusiveLocks on
    # several tables at once, which deadlocks against live traffic (the deployed
    # app shares this database) that reads those tables in a different order.
    # Brief deadlocks are retried instead of failing the whole boot.
    from sqlalchemy.exc import DBAPIError
    for stmt in stmts:
        for attempt in range(3):
            try:
                async with engine.begin() as conn:
                    await conn.execute(text(stmt))
                break
            except DBAPIError as e:
                if "deadlock" in str(e).lower() and attempt < 2:
                    await asyncio.sleep(1 + attempt)
                    continue
                raise


@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_all_tables()       # creates new tables on first run
    await _apply_schema_additions() # safely adds new columns to existing tables

    # Telegram daily reports: cron scheduler + optional dev polling loop.
    # Both are no-ops when TELEGRAM_BOT_TOKEN is unset.
    start_report_scheduler()
    polling_task = None
    if settings.TELEGRAM_USE_POLLING and telegram_service.configured():
        polling_task = asyncio.create_task(
            telegram_service.polling_loop(telegram.handle_polled_update)
        )

    yield

    if polling_task:
        polling_task.cancel()
        with suppress(asyncio.CancelledError):
            await polling_task
    stop_report_scheduler()


# Interactive docs + OpenAPI schema are disabled unless EXPOSE_DOCS is set
# (A05: don't publish the full attack surface to unauthenticated users in prod).
_docs_enabled = settings.EXPOSE_DOCS or settings.DEBUG

app = FastAPI(
    title="BillByte API",
    version="1.0.0",
    description="Restaurant OS backend — FastAPI + PostgreSQL (Supabase)",
    docs_url="/docs" if _docs_enabled else None,
    redoc_url="/redoc" if _docs_enabled else None,
    openapi_url="/openapi.json" if _docs_enabled else None,
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# ── SECURITY HEADERS ────────────────────────────────────────────────────────────
@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains; preload"
    # This is a JSON API; lock down framing/scripts. Swagger UI (when docs are
    # enabled in dev) needs 'unsafe-inline' + CDN, so relax CSP only then.
    if _docs_enabled:
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data: https://fastapi.tiangolo.com; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; frame-ancestors 'none'"
        )
    else:
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
    return response


# ── CORS ──────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── ROUTERS ───────────────────────────────────────────────────────────────────
app.include_router(auth.router,       prefix="/api")
app.include_router(menu.router,       prefix="/api")
app.include_router(orders.router,     prefix="/api")
app.include_router(inventory.router,  prefix="/api")
app.include_router(customers.router,  prefix="/api")
app.include_router(staff.router,      prefix="/api")
app.include_router(reports.router,    prefix="/api")
app.include_router(recipes.router,    prefix="/api")
app.include_router(webhooks.router,   prefix="/api")
app.include_router(admin.router,      prefix="/api")
app.include_router(payments.router,   prefix="/api")
app.include_router(cancellations.router, prefix="/api")
app.include_router(telegram.router,   prefix="/api")


@app.get("/", tags=["Health"])
async def root():
    return {"status": "ok", "app": "BillByte", "version": "1.0.0"}

@app.get("/health", tags=["Health"])
async def health():
    return {"status": "healthy"}