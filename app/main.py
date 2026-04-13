from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from app.core.config import settings
from app.db.session import create_all_tables
from app.routers import auth, menu, orders, inventory, customers, staff, reports, recipes


@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_all_tables()   # auto-creates tables on first run
    yield


app = FastAPI(
    title="BillByte API",
    version="1.0.0",
    description="Restaurant OS backend — FastAPI + PostgreSQL (Supabase)",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

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


@app.get("/", tags=["Health"])
async def root():
    return {"status": "ok", "app": "BillByte", "version": "1.0.0"}

@app.get("/health", tags=["Health"])
async def health():
    return {"status": "healthy"}