"""Nightly Telegram report job.

APScheduler (in-process) fires once a day at REPORT_SEND_HOUR:REPORT_SEND_MINUTE
in TIMEZONE. One report is generated per restaurant and delivered to every
linked, enabled user of that restaurant. Every attempt is written to
telegram_delivery_logs; a failure for one user never stops the run.

Note: this assumes a single backend instance (Railway default). If the app is
ever scaled to multiple replicas, move the job to a dedicated worker or add a
DB-level lock so reports aren't sent twice.
"""

import json
import logging
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.models.user import User, Restaurant
from app.models.telegram import TelegramDeliveryLog
from app.services import telegram as tg
from app.services.report_generator import generate_daily_report, ReportBundle

log = logging.getLogger("billbyte.reports")

_scheduler: AsyncIOScheduler | None = None


async def deliver_to_user(db, user: User, bundle: ReportBundle, manual: bool = False) -> str:
    """Send one bundle to one user, log the attempt, update user status.

    Returns the delivery status string. Never raises — permanent Telegram
    errors disable the user's subscription instead.
    """
    started = time.monotonic()
    status, error, response = "sent", None, None

    try:
        res = await tg.send_message(user.telegram_chat_id, bundle.text)
        response = json.dumps({"message_id": res.get("message_id")})
        for filename, content, mime in bundle.files:
            await tg.send_document(user.telegram_chat_id, filename, content, mime)
    except tg.TelegramError as e:
        error = e.description[:500]
        if e.is_blocked:
            status = "blocked"
            user.telegram_enabled = False
        elif e.is_invalid_chat:
            status = "invalid_chat"
            user.telegram_enabled = False
        else:
            status = "failed"
    except Exception as e:  # keep the batch alive no matter what
        status, error = "failed", str(e)[:500]

    duration_ms = int((time.monotonic() - started) * 1000)
    user.last_report_sent_at = datetime.now(timezone.utc)
    user.last_delivery_status = status if status == "sent" else f"{status}: {error or ''}"[:200]

    db.add(TelegramDeliveryLog(
        restaurant_id=user.restaurant_id,
        user_id=user.id,
        chat_id=user.telegram_chat_id,
        report_date=bundle.report_date,
        status=status,
        error=error,
        telegram_response=response,
        duration_ms=duration_ms,
        manual=manual,
    ))

    log.info("telegram report user=%s restaurant=%s status=%s in %dms%s",
             user.id, user.restaurant_id, status, duration_ms,
             f" error={error}" if error else "")
    return status


async def send_daily_reports() -> dict:
    """The 11 PM job. Also callable directly (admin tools, tests)."""
    run_started = time.monotonic()
    sent = failed = 0

    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(User)
            .join(Restaurant, Restaurant.id == User.restaurant_id)
            .where(
                User.telegram_enabled.is_(True),
                User.telegram_chat_id.is_not(None),
                User.is_active.is_(True),
                Restaurant.is_active.is_(True),
            )
            .order_by(User.restaurant_id)
        )).scalars().all()

        if not rows:
            log.info("telegram daily reports: no linked users, nothing to send")
            return {"sent": 0, "failed": 0}

        by_restaurant: dict[int, list[User]] = {}
        for u in rows:
            by_restaurant.setdefault(u.restaurant_id, []).append(u)

        for restaurant_id, users in by_restaurant.items():
            try:
                bundle = await generate_daily_report(db, restaurant_id)
            except Exception:
                log.exception("report generation failed for restaurant %s", restaurant_id)
                failed += len(users)
                continue
            for user in users:
                status = await deliver_to_user(db, user, bundle)
                sent += status == "sent"
                failed += status != "sent"
                await db.commit()  # per-user, so one failure can't roll back others

    log.info("telegram daily reports done: %d sent, %d failed in %.1fs",
             sent, failed, time.monotonic() - run_started)
    return {"sent": sent, "failed": failed}


def start_report_scheduler() -> AsyncIOScheduler | None:
    """Called from the FastAPI lifespan. No-op when the bot isn't configured."""
    global _scheduler
    if not tg.configured():
        log.info("TELEGRAM_BOT_TOKEN not set — report scheduler disabled")
        return None
    _scheduler = AsyncIOScheduler(timezone=ZoneInfo(settings.TIMEZONE))
    _scheduler.add_job(
        send_daily_reports,
        CronTrigger(hour=settings.REPORT_SEND_HOUR, minute=settings.REPORT_SEND_MINUTE),
        id="telegram_daily_reports",
        coalesce=True,             # several missed runs collapse into one
        misfire_grace_time=3600,   # still send if we wake up within an hour
        max_instances=1,
    )
    _scheduler.start()
    log.info("report scheduler started: daily at %02d:%02d %s",
             settings.REPORT_SEND_HOUR, settings.REPORT_SEND_MINUTE, settings.TIMEZONE)
    return _scheduler


def stop_report_scheduler() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
