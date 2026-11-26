import time
from urllib.parse import urlparse

from sqlalchemy import event, exc
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from app.core.config import settings

# The API (Railway, Singapore) and the database (Supabase, Mumbai) are ~60 ms
# apart, so every network round trip counts. Two settings keep them down:
#
# 1. Prepared-statement cache. Each query used to be prepared afresh (an extra
#    round trip). Supabase's pooler on port 5432 is *session* mode, where a
#    connection keeps its prepared statements, so caching them is safe. On a
#    *transaction*-mode pooler (port 6543 / pgbouncer) statements would land on
#    other backends, so the cache is switched off automatically there.
_db_port = urlparse(settings.DATABASE_URL.replace("+asyncpg", "")).port
_TRANSACTION_POOLER = _db_port == 6543

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    pool_size=3,
    max_overflow=5,
    pool_pre_ping=False,   # replaced by the idle-only ping below
    pool_recycle=300,
    connect_args={
        "prepared_statement_cache_size": 0 if _TRANSACTION_POOLER else 256,
        "statement_cache_size": 0,
    },
)

# 2. Ping only connections that sat idle. pool_pre_ping pinged on *every*
#    request (one more round trip each time). A connection used in the last
#    30 s is alive; an idle one is pinged first, and a dead one is replaced
#    transparently (DisconnectionError makes the pool retry with a new one).
_IDLE_PING_SECONDS = 30


@event.listens_for(engine.sync_engine, "checkin")
def _mark_idle(dbapi_connection, connection_record):
    connection_record.info["idle_since"] = time.monotonic()


@event.listens_for(engine.sync_engine, "checkout")
def _ping_if_idle(dbapi_connection, connection_record, connection_proxy):
    since = connection_record.info.get("idle_since")
    if since is None or time.monotonic() - since < _IDLE_PING_SECONDS:
        return
    try:
        engine.dialect.do_ping(dbapi_connection)
    except Exception:
        raise exc.DisconnectionError("stale pooled connection")

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def create_all_tables():
    """Auto-create tables on startup — use Alembic in production."""
    import app.models  # noqa: registers all models
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)