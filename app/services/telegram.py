"""Thin async client for the official Telegram Bot API.

No SDK — plain httpx against https://api.telegram.org, which keeps the
dependency surface small and the error handling explicit. All senders retry
transient failures (timeouts, 5xx, 429 with retry_after) with exponential
backoff; permanent failures (bot blocked, invalid chat) raise immediately so
callers can deactivate the link instead of retrying forever.
"""

import asyncio
import logging
import httpx

from app.core.config import settings

log = logging.getLogger("billbyte.telegram")

_API_ROOT = "https://api.telegram.org"
_TIMEOUT = httpx.Timeout(20.0, connect=10.0)
_MAX_ATTEMPTS = 3
_MAX_RETRY_AFTER = 60  # cap what we honor from a 429, seconds


class TelegramError(Exception):
    """A non-OK reply from the Bot API."""

    def __init__(self, description: str, code: int = 0, retry_after: int | None = None):
        super().__init__(f"[{code}] {description}")
        self.code = code
        self.description = description
        self.retry_after = retry_after

    @property
    def is_blocked(self) -> bool:
        # 403 = "Forbidden: bot was blocked by the user" (or kicked from chat)
        return self.code == 403

    @property
    def is_invalid_chat(self) -> bool:
        return self.code == 400 and "chat not found" in self.description.lower()

    @property
    def is_permanent(self) -> bool:
        return self.is_blocked or self.is_invalid_chat


def configured() -> bool:
    return bool(settings.TELEGRAM_BOT_TOKEN)


def _url(method: str) -> str:
    return f"{_API_ROOT}/bot{settings.TELEGRAM_BOT_TOKEN}/{method}"


async def _call(method: str, payload: dict | None = None, files: dict | None = None) -> dict:
    """One Bot API call. Returns the `result` object or raises TelegramError."""
    if not configured():
        raise TelegramError("TELEGRAM_BOT_TOKEN is not configured", 0)
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        if files:
            res = await client.post(_url(method), data=payload or {}, files=files)
        else:
            res = await client.post(_url(method), json=payload or {})
    try:
        body = res.json()
    except ValueError:
        raise TelegramError(f"non-JSON reply, HTTP {res.status_code}", res.status_code)
    if not body.get("ok"):
        raise TelegramError(
            body.get("description", "unknown error"),
            body.get("error_code", res.status_code),
            (body.get("parameters") or {}).get("retry_after"),
        )
    return body.get("result")


async def _call_with_retries(method: str, payload: dict | None = None, files: dict | None = None) -> dict:
    """Retry timeouts/5xx/429; fail fast on permanent errors (403, bad chat)."""
    delay = 1.0
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            return await _call(method, payload, files)
        except TelegramError as e:
            if e.is_permanent or attempt == _MAX_ATTEMPTS:
                raise
            if e.code == 429:
                wait = min(e.retry_after or delay, _MAX_RETRY_AFTER)
                log.warning("telegram rate-limited, waiting %ss (%s)", wait, method)
                await asyncio.sleep(wait)
            elif e.code >= 500 or e.code == 0:
                await asyncio.sleep(delay)
            else:
                raise  # other 4xx are caller bugs — retrying won't help
        except (httpx.TimeoutException, httpx.TransportError) as e:
            if attempt == _MAX_ATTEMPTS:
                raise TelegramError(f"network error: {e}", 0)
            await asyncio.sleep(delay)
        delay *= 2
    raise TelegramError("retries exhausted", 0)  # unreachable, keeps type-checkers happy


# ── Public senders ──────────────────────────────────────────────────────────────

async def send_message(chat_id: int, text: str, silent: bool = False) -> dict:
    """Send an HTML-formatted text message (bold/italic/code + emojis).

    HTML parse mode is used instead of Markdown so dynamic values (restaurant
    names, dish names) can be safely escaped — Markdown breaks on `_` and `*`.
    """
    return await _call_with_retries("sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_notification": silent,
    })


async def send_document(chat_id: int, filename: str, content: bytes,
                        mime: str = "application/octet-stream", caption: str | None = None) -> dict:
    payload = {"chat_id": str(chat_id)}
    if caption:
        payload["caption"] = caption
        payload["parse_mode"] = "HTML"
    return await _call_with_retries(
        "sendDocument", payload,
        files={"document": (filename, content, mime)},
    )


# ── Bot management ──────────────────────────────────────────────────────────────

_bot_username_cache: str | None = None


async def get_bot_username() -> str | None:
    """Bot @username for deep links — env override first, then getMe (cached)."""
    global _bot_username_cache
    if settings.TELEGRAM_BOT_USERNAME:
        return settings.TELEGRAM_BOT_USERNAME.lstrip("@")
    if _bot_username_cache:
        return _bot_username_cache
    try:
        me = await _call("getMe")
        _bot_username_cache = me.get("username")
        return _bot_username_cache
    except Exception as e:
        log.warning("getMe failed: %s", e)
        return None


async def set_webhook(url: str) -> dict:
    """Point Telegram at our webhook. Requires TELEGRAM_WEBHOOK_SECRET."""
    return await _call("setWebhook", {
        "url": url,
        "secret_token": settings.TELEGRAM_WEBHOOK_SECRET,
        "allowed_updates": ["message"],
        "drop_pending_updates": True,
    })


async def delete_webhook() -> dict:
    return await _call("deleteWebhook", {"drop_pending_updates": True})


# ── Dev-only long polling (no public URL needed) ────────────────────────────────

async def polling_loop(handle_update):
    """getUpdates loop for local development (TELEGRAM_USE_POLLING=true).

    `handle_update` is an async callable receiving one raw update dict. Errors
    in individual updates are logged and swallowed so the loop never dies.
    """
    offset = 0
    log.info("telegram polling loop started (dev mode)")
    while True:
        try:
            updates = await _call("getUpdates", {"offset": offset, "timeout": 50})
            for u in updates or []:
                offset = max(offset, u.get("update_id", 0) + 1)
                try:
                    await handle_update(u)
                except Exception:
                    log.exception("error handling telegram update")
        except asyncio.CancelledError:
            log.info("telegram polling loop stopped")
            raise
        except Exception as e:
            log.warning("polling error, retrying in 5s: %s", e)
            await asyncio.sleep(5)
