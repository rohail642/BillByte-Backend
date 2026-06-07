from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from sqlalchemy import text
from app.core.config import settings
from app.core.limiter import limiter
from app.db.session import create_all_tables, engine
from app.routers import auth, menu, orders, inventory, customers, staff, reports, recipes, webhooks, admin, payments
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
    async with engine.begin() as conn:
        for stmt in stmts:
            await conn.execute(text(stmt))


@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_all_tables()       # creates new tables on first run
    await _apply_schema_additions() # safely adds new columns to existing tables
    yield


app = FastAPI(
    title="BillByte API",
    version="1.0.0",
    description="Restaurant OS backend — FastAPI + PostgreSQL (Supabase)",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

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


@app.get("/", tags=["Health"])
async def root():
    return {"status": "ok", "app": "BillByte", "version": "1.0.0"}

@app.get("/health", tags=["Health"])
async def health():
    return {"status": "healthy"}