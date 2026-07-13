from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class LinkTokenOut(BaseModel):
    token: str
    deep_link: Optional[str] = None   # null when the bot username can't be resolved
    expires_at: datetime
    bot_username: Optional[str] = None


class TelegramStatusOut(BaseModel):
    configured: bool                  # is TELEGRAM_BOT_TOKEN set on the server
    linked: bool
    enabled: bool
    telegram_username: Optional[str] = None
    linked_at: Optional[datetime] = None
    last_report_sent_at: Optional[datetime] = None
    last_delivery_status: Optional[str] = None
    bot_username: Optional[str] = None
    send_hour: int
    send_minute: int
    timezone: str


class TelegramToggleIn(BaseModel):
    enabled: bool


class SendNowOut(BaseModel):
    status: str
    report_date: str
