# Telegram Daily Reports

Every night at **23:00 IST** (configurable), BillByte sends each linked owner/manager
their restaurant's daily sales report on Telegram — a formatted text summary plus
PDF and CSV attachments.

## How it works

```
app/services/telegram.py          Bot API client (httpx, retries/backoff, error classes)
app/services/report_generator.py  Data queries + text/CSV/PDF renderers → ReportBundle
app/services/report_scheduler.py  APScheduler cron job (23:00 IST) + per-user delivery
app/routers/telegram.py           Linking endpoints + Telegram webhook + bot commands
app/models/telegram.py            telegram_link_tokens, telegram_delivery_logs
app/models/user.py                telegram_* columns on users
alembic/versions/c1d2e3f4a5b6_*   Production migration (dev applies automatically on boot)
Frontend: src/api/telegram.js + the "Telegram Daily Reports" card in Settings → Integrations
```

- One report is generated **per restaurant**, then delivered to every linked, enabled
  user of that restaurant. A failure for one user never stops the others.
- Every attempt is recorded in `telegram_delivery_logs` (status, error, Telegram
  response, duration, manual-vs-scheduled) and mirrored to `users.last_report_sent_at`
  / `users.last_delivery_status`.
- If a user **blocks the bot** (403) or the chat vanishes (400 chat not found), their
  subscription is automatically disabled instead of retrying forever. Transient
  failures (timeouts, 5xx, 429) retry up to 3× with exponential backoff, honouring
  Telegram's `retry_after` on rate limits.
- Numbers come from the same queries as the Reports page (paid orders only,
  restaurant-local calendar day), so Telegram always matches the dashboard.

## 1. Create the bot (BotFather)

1. In Telegram, open **@BotFather** → send `/newbot`.
2. Pick a display name (e.g. `BillByte Reports`) and a username ending in `bot`
   (e.g. `BillByteReportsBot`).
3. BotFather replies with the **bot token** (`1234567890:AA...`). Keep it secret.
4. Optional polish: `/setdescription`, `/setuserpic`.

## 2. Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | yes | `""` | From BotFather. Empty = whole feature off (scheduler doesn't start, webhook 503s, Settings card says "not configured"). |
| `TELEGRAM_WEBHOOK_SECRET` | yes (webhook mode) | `""` | Random string (e.g. `openssl rand -hex 24`). Registered with setWebhook and verified on every incoming update. |
| `TELEGRAM_BOT_USERNAME` | no | `""` | Bot username without `@`, used for t.me deep links. Auto-fetched via `getMe` if empty. |
| `TELEGRAM_USE_POLLING` | no | `false` | Dev only: long-poll `getUpdates` instead of a webhook. Never combine with an active webhook. |
| `REPORT_SEND_HOUR` / `REPORT_SEND_MINUTE` | no | `23` / `0` | Local send time. |
| `TIMEZONE` | no | `Asia/Kolkata` | Scheduler timezone AND the "day" boundary for report numbers. |
| `REPORT_ATTACHMENTS` | no | `pdf,csv` | Which files to attach (`pdf,csv`, `csv`, or empty for text-only). |

## 3. Database migration

- **Dev**: nothing to do — columns/tables are created automatically on startup
  (`create_all_tables()` + `_apply_schema_additions()` in `app/main.py`).
- **Production (Alembic)**: `alembic upgrade head` (revision `c1d2e3f4a5b6` adds the
  `users.telegram_*` columns and the two new tables). The startup ALTERs are
  idempotent, so either path is safe.

## 4. Point Telegram at the backend (webhook mode — production)

After deploying with the env vars set:

```bash
curl -s "https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/setWebhook" \
  -d "url=https://api.billbyte.co.in/api/telegram/webhook" \
  -d "secret_token=<TELEGRAM_WEBHOOK_SECRET>" \
  -d 'allowed_updates=["message"]'
```

Check it with `.../getWebhookInfo`. To remove: `.../deleteWebhook`.

**Local development instead**: set `TELEGRAM_USE_POLLING=true` (and no webhook) —
the app long-polls Telegram itself, so no public URL is needed.

## 5. Linking a user (what the owner does)

1. BillByte → **Settings → Integrations → Telegram Daily Reports → Connect Telegram**.
2. The app opens `https://t.me/<bot>?start=<one-time-token>` — tap **Start**.
3. The bot confirms; the Settings card flips to "Connected" within a few seconds.

Bot commands: `/stop` pauses reports (re-enable from Settings), `/help` shows usage.

Security properties of the link flow: tokens are single-use, expire after 15 minutes,
are stored only as SHA-256 hashes, and issuing a new token invalidates all previous
unused ones — so a leaked or reused deep link cannot re-bind an account (replay-safe).
The webhook rejects any request whose `X-Telegram-Bot-Api-Secret-Token` header doesn't
match, and treats update payloads as untrusted JSON.

## 6. Testing

1. **Renderers (no Telegram needed)** — already covered by the import smoke test;
   `report_generator.build_csv/build_pdf/format_report_text` are pure functions.
2. **End to end (dev)**:
   ```bash
   # .env: TELEGRAM_BOT_TOKEN=..., TELEGRAM_USE_POLLING=true
   uvicorn app.main:app --reload --port 8000
   ```
   Link your account from Settings, then press **Send test report** in the same card
   (calls `POST /api/telegram/send-now`) — the report for *today so far* arrives
   immediately. Delivery rows: `select * from telegram_delivery_logs order by id desc;`
3. **Force the nightly job** without waiting for 23:00:
   ```bash
   venv/bin/python -c "import asyncio; from app.services.report_scheduler import send_daily_reports; print(asyncio.run(send_daily_reports()))"
   ```

## 7. Example message

```
📊 Daily Report — Anas Kitchen
🗓 Fri, 11 Jul 2026

💰 Revenue: ₹12,54,322
🧾 Orders: 28  ·  Avg bill: ₹44,797
🏷 GST collected: ₹62,716  ·  Discounts: ₹0
🙋 New customers: 7

By payment
  • CASH: 20 orders — ₹9,00,000
  • UPI: 8 orders — ₹3,54,321

Top dishes
  1. Paneer Tikka ×14 — ₹3,500

— BillByte
+ BillByte-2026-07-11.pdf, BillByte-2026-07-11.csv attached
```

## 8. Deployment (Railway)

1. Set the env vars from §2 on the Railway service.
2. Deploy — startup applies the schema changes and starts the scheduler
   (`report scheduler started: daily at 23:00 Asia/Kolkata` in logs).
3. Run the `setWebhook` curl from §4 once.
4. Have the owner link from Settings and press **Send test report**.

⚠️ The scheduler is in-process and assumes **one** backend instance (Railway's
default). If you ever scale to replicas, move the job to a worker or add a DB lock,
or reports will send twice.

## 9. Future improvements

- Weekly/monthly digest variants (`/reports/gst-summary` is already there).
- Per-restaurant send time instead of one global hour.
- Inline bot buttons ("Send now", "This week") via `callback_query`.
- Low-stock and large-cancellation alerts through the same bot.
- Admin view over `telegram_delivery_logs` for support.
- WhatsApp channel behind the same `deliver_to_user` interface once a Business
  account exists.
