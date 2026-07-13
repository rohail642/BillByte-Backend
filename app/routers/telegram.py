"""Telegram account linking + daily-report endpoints.

Linking flow (replay-safe):
1. Owner/manager clicks "Connect Telegram" → POST /telegram/link-token issues a
   one-time token (32 chars, 15-minute expiry). Only its SHA-256 hash is stored,
   and issuing a new token invalidates the previous unused ones.
2. The user opens t.me/<bot>?start=<token> and hits Start — Telegram delivers
   "/start <token>" to our webhook (or the dev polling loop).
3. The handler burns the token, stores the chat id on the user, and confirms.

The webhook trusts nothing: it requires the X-Telegram-Bot-Api-Secret-Token
header to match TELEGRAM_WEBHOOK_SECRET and treats the payload as untyped JSON.
"""

import hashlib
import hmac
import logging
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Header
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.limiter import limiter
from app.core.security import get_current_user, require_owner
from app.db.session import get_db, AsyncSessionLocal
from app.models.user import User, Restaurant
from app.models.telegram import TelegramLinkToken
from app.schemas.telegram import LinkTokenOut, TelegramStatusOut, TelegramToggleIn, SendNowOut
from app.services import telegram as tg
from app.services.report_generator import generate_daily_report
from app.services.report_scheduler import deliver_to_user

log = logging.getLogger("billbyte.telegram")

router = APIRouter(prefix="/telegram", tags=["Telegram"])

_TOKEN_TTL_MINUTES = 15


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _utcnow() -> datetime:
    # Always timezone-aware: naive datetimes get reinterpreted in the host's
    # local timezone on the way to timestamptz columns (5h30m off on IST hosts).
    return datetime.now(timezone.utc)


def _require_configured():
    if not tg.configured():
        raise HTTPException(503, "Telegram bot is not configured on the server")


# ── Account linking ─────────────────────────────────────────────────────────────

@router.post("/link-token", response_model=LinkTokenOut)
@limiter.limit("10/minute")
async def create_link_token(
    request: Request,
    db: AsyncSession = Depends(get_db),
    u: User = Depends(require_owner),
):
    """Issue a one-time deep-link token for the calling user."""
    _require_configured()

    # A fresh token supersedes any unused ones — nothing older can be replayed.
    await db.execute(
        update(TelegramLinkToken)
        .where(TelegramLinkToken.user_id == u.id, TelegramLinkToken.used_at.is_(None))
        .values(used_at=_utcnow())
    )

    token = secrets.token_urlsafe(24)  # 32 chars, fits Telegram's 64-char start payload
    expires_at = _utcnow() + timedelta(minutes=_TOKEN_TTL_MINUTES)
    db.add(TelegramLinkToken(user_id=u.id, token_hash=_hash(token), expires_at=expires_at))

    bot_username = await tg.get_bot_username()
    return LinkTokenOut(
        token=token,
        deep_link=f"https://t.me/{bot_username}?start={token}" if bot_username else None,
        expires_at=expires_at,
        bot_username=bot_username,
    )


@router.get("/status", response_model=TelegramStatusOut)
async def telegram_status(u: User = Depends(require_owner)):
    bot_username = await tg.get_bot_username() if tg.configured() else None
    return TelegramStatusOut(
        configured=tg.configured(),
        linked=u.telegram_chat_id is not None,
        enabled=bool(u.telegram_enabled),
        telegram_username=u.telegram_username,
        linked_at=u.telegram_linked_at,
        last_report_sent_at=u.last_report_sent_at,
        last_delivery_status=u.last_delivery_status,
        bot_username=bot_username,
        send_hour=settings.REPORT_SEND_HOUR,
        send_minute=settings.REPORT_SEND_MINUTE,
        timezone=settings.TIMEZONE,
    )


@router.patch("/toggle", response_model=TelegramStatusOut)
async def toggle_reports(
    body: TelegramToggleIn,
    db: AsyncSession = Depends(get_db),
    u: User = Depends(require_owner),
):
    if body.enabled and u.telegram_chat_id is None:
        raise HTTPException(400, "Connect a Telegram account first")
    u.telegram_enabled = body.enabled
    await db.flush()
    return await telegram_status(u)


@router.delete("/link", response_model=TelegramStatusOut)
async def unlink_telegram(
    db: AsyncSession = Depends(get_db),
    u: User = Depends(require_owner),
):
    u.telegram_chat_id = None
    u.telegram_username = None
    u.telegram_linked_at = None
    u.telegram_enabled = False
    await db.flush()
    return await telegram_status(u)


@router.post("/send-now", response_model=SendNowOut)
@limiter.limit("5/minute")
async def send_report_now(
    request: Request,
    db: AsyncSession = Depends(get_db),
    u: User = Depends(require_owner),
):
    """Send today's report to the calling user immediately (testing / on demand)."""
    _require_configured()
    if u.telegram_chat_id is None:
        raise HTTPException(400, "Connect a Telegram account first")
    bundle = await generate_daily_report(db, u.restaurant_id)
    status = await deliver_to_user(db, u, bundle, manual=True)
    if status != "sent":
        raise HTTPException(502, f"Delivery failed ({u.last_delivery_status})")
    return SendNowOut(status=status, report_date=bundle.report_date)


# ── Bot update handling (webhook + dev polling share this) ──────────────────────

_HELP_TEXT = (
    "👋 This is the <b>BillByte reports bot</b>.\n\n"
    "It sends your restaurant's daily sales report every night.\n\n"
    "To connect your account: open BillByte → Settings → Integrations → "
    "<b>Connect Telegram</b>, then tap the link it gives you.\n\n"
    "Commands:\n"
    "/stop — pause daily reports\n"
    "/help — this message"
)


async def _reply(chat_id: int, text: str):
    try:
        await tg.send_message(chat_id, text)
    except Exception as e:  # replies are best-effort; never break the webhook
        log.warning("telegram reply failed for chat %s: %s", chat_id, e)


async def _link_account(db: AsyncSession, chat: dict, from_user: dict, raw_token: str):
    chat_id = chat["id"]
    r = await db.execute(
        select(TelegramLinkToken).where(TelegramLinkToken.token_hash == _hash(raw_token))
    )
    link = r.scalar_one_or_none()

    if not link or link.used_at is not None or link.expires_at < _utcnow():
        await _reply(chat_id,
                     "⚠️ This link is invalid or has expired.\n"
                     "Generate a fresh one from BillByte → Settings → Integrations.")
        return

    user = await db.get(User, link.user_id)
    if not user or not user.is_active:
        await _reply(chat_id, "⚠️ This BillByte account is not active.")
        return

    link.used_at = _utcnow()  # burn the token before anything else
    user.telegram_chat_id = chat_id
    user.telegram_username = (from_user.get("username") or "")[:64] or None
    user.telegram_linked_at = _utcnow()
    user.telegram_enabled = True
    user.last_delivery_status = None

    rest = await db.get(Restaurant, user.restaurant_id)
    import html as _html
    await _reply(chat_id,
                 f"✅ <b>Connected!</b>\n\n"
                 f"This chat will now receive the daily report for "
                 f"<b>{_html.escape(rest.name if rest else 'your restaurant')}</b> "
                 f"every night at {settings.REPORT_SEND_HOUR:02d}:{settings.REPORT_SEND_MINUTE:02d} "
                 f"({settings.TIMEZONE}).\n\n"
                 f"Send /stop anytime to pause.")
    log.info("telegram linked: user=%s chat=%s", user.id, chat_id)


async def _process_update(update_data: dict, db: AsyncSession):
    """Handle one Telegram update. Only private text messages are meaningful."""
    msg = update_data.get("message")
    if not isinstance(msg, dict):
        return
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    text = msg.get("text")
    if not isinstance(chat_id, int) or not isinstance(text, str):
        return
    text = text.strip()
    from_user = msg.get("from") or {}

    if text.startswith("/start"):
        parts = text.split(maxsplit=1)
        payload = parts[1].strip() if len(parts) > 1 else ""
        # Telegram start payloads are [A-Za-z0-9_-]{1,64}; anything else is noise
        if payload and len(payload) <= 64 and all(c.isalnum() or c in "_-" for c in payload):
            await _link_account(db, chat, from_user, payload)
        else:
            await _reply(chat_id, _HELP_TEXT)
    elif text.startswith("/stop"):
        res = await db.execute(
            update(User)
            .where(User.telegram_chat_id == chat_id, User.telegram_enabled.is_(True))
            .values(telegram_enabled=False)
        )
        if res.rowcount:
            await _reply(chat_id, "⏸ Daily reports paused. Re-enable them from BillByte → Settings.")
        else:
            await _reply(chat_id, "This chat has no active report subscription.")
    else:
        await _reply(chat_id, _HELP_TEXT)


@router.post("/webhook", include_in_schema=False)
async def telegram_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_secret: str = Header(None, alias="X-Telegram-Bot-Api-Secret-Token"),
):
    _require_configured()
    # Reject anything that doesn't carry the secret we registered via setWebhook.
    if not settings.TELEGRAM_WEBHOOK_SECRET or not hmac.compare_digest(
        x_secret or "", settings.TELEGRAM_WEBHOOK_SECRET
    ):
        raise HTTPException(403, "Bad webhook secret")

    try:
        update_data = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON")

    try:
        await _process_update(update_data, db)
    except Exception:
        # Log but ACK — otherwise Telegram redelivers the same broken update forever.
        log.exception("error processing telegram update")
    return {"ok": True}


async def handle_polled_update(update_data: dict):
    """Session-owning wrapper used by the dev polling loop (see app.main)."""
    async with AsyncSessionLocal() as db:
        try:
            await _process_update(update_data, db)
            await db.commit()
        except Exception:
            await db.rollback()
            raise
