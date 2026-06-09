# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run dev server (auto-reloads)
uvicorn app.main:app --reload --port 8000

# Install dependencies
pip install -r requirements.txt

# Database migrations (production — dev uses auto create_all on startup)
alembic revision --autogenerate -m "describe change"
alembic upgrade head

# Seed the database with sample data
python seed.py
```

No test suite exists in this project.

Interactive API docs are at `http://localhost:8000/docs` after startup.

## Architecture

**BillByte** is a multi-tenant restaurant OS. Each `Restaurant` has its own isolated data. All authenticated endpoints derive `restaurant_id` from the JWT payload (set at login/register) — every DB query filters by this ID.

### Request flow

1. Client sends `Authorization: Bearer <token>`
2. `get_current_user` in `app/core/security.py` decodes JWT → loads `User` (with `restaurant_id`)
3. Router uses `user.restaurant_id` to scope all queries

### Multi-tenancy pattern

All resource models (`MenuItem`, `Order`, `InventoryItem`, etc.) have a `restaurant_id` FK. Routers always filter: `where(Model.restaurant_id == current_user.restaurant_id)`.

### Auth & RBAC

Roles hierarchy (highest to lowest): `owner → manager → cashier → waiter`

- `get_current_user` — any authenticated user
- `require_owner` — owner or manager only
- `require_role("owner", "cashier")` — exact role match
- `require_min_role("manager")` — minimum level check

### Database session

`get_db()` in `app/db/session.py` yields an `AsyncSession` that auto-commits on success and rolls back on exception. Tables are auto-created via `create_all_tables()` in the lifespan hook (dev only — use Alembic in production).

### Inventory deduction via Recipes

`Recipe` links a `MenuItem` to `InventoryItem` ingredients with quantities. When a KOT is sent (`PATCH /api/orders/{id}/status` → `kot_sent`), `deduct_inventory_for_order()` in `app/routers/recipes.py` is called to deduct stock. The same function runs automatically for Zomato/Swiggy webhook orders.

### Webhook integrations

`/api/webhooks/zomato/{restaurant_id}` and `/api/webhooks/swiggy/{restaurant_id}` accept platform orders. Secrets (`zomato_secret`, `swiggy_secret`) are stored per-restaurant in the `Restaurant` model. Signature verification is skipped if the secret is empty (dev mode). Webhook orders auto-set status to `kot_sent` and trigger inventory deduction.

### Order lifecycle

`pending → kot_sent → preparing → ready → served → paid` (or `cancelled`)

Payment is collected via `PATCH /api/orders/{id}/pay` which calculates GST from the restaurant's `gst_rate`, applies discount, and optionally awards loyalty points.

## Environment variables

| Variable | Description |
|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://...` (Supabase asyncpg URI) |
| `SECRET_KEY` | JWT signing secret |
| `ALLOWED_ORIGINS` | Comma-separated CORS origins |
| `ZOMATO_WEBHOOK_SECRET` | Global fallback (per-restaurant secret preferred) |
| `SWIGGY_WEBHOOK_SECRET` | Global fallback (per-restaurant secret preferred) |
| `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` | Payment gateway |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | JWT lifetime, default 720 (12h). Keep short — do not set multi-week values. |
| `DEBUG` | `true` in local dev only. Relaxes the `SECRET_KEY` placeholder check and enables the dev-only webhook test endpoint. |
| `EXPOSE_DOCS` | `true` serves `/docs`, `/redoc`, `/openapi.json`. Keep **false/unset in production** so the API surface isn't published anonymously. |

## Security model (post-pentest hardening, 2026-06)

- **Input sanitization**: free-text fields use `SafeStr`/`SafeOptStr` from `app/core/sanitize.py`, which strip HTML at the schema boundary (defense-in-depth vs stored XSS). Apply these to any new user-supplied string field.
- **Password policy**: `app/core/validators.py::validate_password` (≥8 chars, upper/lower/digit, blocklist). Enforced on register, change-password, and team-member create/update.
- **Token revocation**: `User.token_version` is embedded in the JWT and checked in `get_current_user`. Bump it (`+= 1`) whenever you invalidate a user's sessions (password change, forced logout, deactivation).
- **`SECRET_KEY`** must be a strong random value in production; the app refuses to start with the placeholder unless `DEBUG=true`.
- **Proxy headers**: the start command passes `--proxy-headers --forwarded-allow-ips='*'` so per-IP rate limiting (slowapi) and audit-log IPs see the real client behind Railway's edge.
- **Security headers + CORS**: added in `app/main.py`. CSP is strict (`default-src 'none'`) unless docs are exposed.

## Key design decisions

- SQLAlchemy `expire_on_commit=False` — objects remain accessible after commit without re-query
- Connection pool tuned for Supabase (`pool_size=3`, `prepared_statement_cache_size=0` required for pgbouncer compatibility)
- `OrderItem.name` stores the item name at order time (denormalized) — so menu edits don't corrupt historical orders
- Swiggy sends prices in paise; the webhook parser divides by 100
- Token expiry defaults to 1440 minutes (24 hours), configurable via `ACCESS_TOKEN_EXPIRE_MINUTES`
