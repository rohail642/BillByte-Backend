"""Daily-report generation: data queries + text / CSV / PDF renderers.

`generate_daily_report(db, restaurant_id)` is the single entry point the
scheduler and the send-now endpoint use. It returns a ReportBundle — swap the
internals (or the renderers) without touching senders. Queries mirror
app/routers/reports.py so Telegram numbers always match the Reports page.
"""

import csv
import io
import html
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.order import Order, OrderItem
from app.models.customer import Customer
from app.models.user import Restaurant

_TOP_DISHES = 5


@dataclass
class ReportBundle:
    report_date: str                 # YYYY-MM-DD (restaurant-local day)
    date_label: str                  # "Fri, 11 Jul 2026"
    text: str                        # HTML-formatted Telegram message
    files: list = field(default_factory=list)  # [(filename, bytes, mime), ...]
    data: dict = field(default_factory=dict)   # raw numbers, for logging/tests


def _local_day_window(when: datetime | None = None):
    """The restaurant-local calendar day containing `when` (default: now).

    Bounds are timezone-AWARE UTC datetimes: naive values get reinterpreted in
    the machine's local timezone by the driver, which silently shifts them on
    any non-UTC host (bit us on an IST dev machine)."""
    tz = ZoneInfo(settings.TIMEZONE)
    now_local = (when.astimezone(tz) if when and when.tzinfo else datetime.now(tz))
    day_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    to_utc = lambda d: d.astimezone(timezone.utc)
    return to_utc(day_start), to_utc(min(day_end, now_local)), now_local.date()


def _inr(n) -> str:
    """Indian digit grouping: 1234567 -> 12,34,567."""
    i = int(round(n or 0))
    s, sign = str(abs(i)), "-" if i < 0 else ""
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts + [tail])
    return sign + s


async def _collect(db: AsyncSession, restaurant_id: int, start, end) -> dict:
    rest = await db.get(Restaurant, restaurant_id)

    paid = (Order.restaurant_id == restaurant_id,
            Order.payment_status == "paid",
            Order.created_at >= start, Order.created_at < end)

    row = (await db.execute(
        select(
            func.coalesce(func.sum(Order.total_amount), 0).label("revenue"),
            func.count(Order.id).label("orders"),
            func.coalesce(func.sum(Order.gst_amount), 0).label("gst"),
            func.coalesce(func.sum(Order.discount_amount), 0).label("discounts"),
        ).where(*paid)
    )).one()

    by_payment = (await db.execute(
        select(Order.payment_method, func.count(Order.id), func.coalesce(func.sum(Order.total_amount), 0))
        .where(*paid).group_by(Order.payment_method)
    )).all()

    by_type = (await db.execute(
        select(Order.order_type, func.coalesce(func.sum(Order.total_amount), 0))
        .where(*paid).group_by(Order.order_type)
    )).all()

    top = (await db.execute(
        select(OrderItem.name, func.sum(OrderItem.quantity).label("qty"),
               func.coalesce(func.sum(OrderItem.total), 0).label("rev"))
        .join(Order, Order.id == OrderItem.order_id)
        .where(*paid, OrderItem.cancelled_at.is_(None))
        .group_by(OrderItem.name)
        .order_by(func.sum(OrderItem.quantity).desc())
        .limit(_TOP_DISHES)
    )).all()

    new_customers = (await db.execute(
        select(func.count(Customer.id)).where(
            Customer.restaurant_id == restaurant_id,
            Customer.created_at >= start, Customer.created_at < end)
    )).scalar() or 0

    cancelled = (await db.execute(
        select(func.count(Order.id)).where(
            Order.restaurant_id == restaurant_id, Order.status == "cancelled",
            Order.created_at >= start, Order.created_at < end)
    )).scalar() or 0

    return {
        "restaurant_name": rest.name if rest else "Restaurant",
        "revenue": round(row.revenue, 2),
        "orders": row.orders,
        "gst": round(row.gst, 2),
        "discounts": round(row.discounts, 2),
        "avg_bill": round(row.revenue / row.orders, 2) if row.orders else 0,
        "by_payment": [{"method": m or "other", "count": c, "revenue": round(r, 2)} for m, c, r in by_payment],
        "by_type": [{"type": t or "other", "revenue": round(r, 2)} for t, r in by_type],
        "top_dishes": [{"name": n, "quantity": int(q), "revenue": round(r, 2)} for n, q, r in top],
        "new_customers": new_customers,
        "cancelled_orders": cancelled,
    }


# ── Renderers ───────────────────────────────────────────────────────────────────

_TYPE_LABELS = {"dine_in": "Dine-in", "takeaway": "Takeaway", "delivery": "Delivery",
                "zomato": "Zomato", "swiggy": "Swiggy", "whatsapp": "WhatsApp"}


def format_report_text(d: dict, date_label: str) -> str:
    """Telegram HTML message. Dynamic values are escaped."""
    e = html.escape
    lines = [
        f"📊 <b>Daily Report — {e(d['restaurant_name'])}</b>",
        f"🗓 {date_label}",
        "",
        f"💰 Revenue: <b>₹{_inr(d['revenue'])}</b>",
        f"🧾 Orders: <b>{d['orders']}</b>  ·  Avg bill: ₹{_inr(d['avg_bill'])}",
        f"🏷 GST collected: ₹{_inr(d['gst'])}  ·  Discounts: ₹{_inr(d['discounts'])}",
        f"🙋 New customers: <b>{d['new_customers']}</b>",
    ]
    if d["cancelled_orders"]:
        lines.append(f"❌ Cancelled orders: {d['cancelled_orders']}")

    if d["by_payment"]:
        lines += ["", "<b>By payment</b>"]
        lines += [f"  • {e(p['method'].upper())}: {p['count']} orders — ₹{_inr(p['revenue'])}"
                  for p in d["by_payment"]]

    if d["by_type"]:
        lines += ["", "<b>By channel</b>"]
        lines += [f"  • {e(_TYPE_LABELS.get(t['type'], t['type']))}: ₹{_inr(t['revenue'])}"
                  for t in d["by_type"]]

    if d["top_dishes"]:
        lines += ["", "<b>Top dishes</b>"]
        lines += [f"  {i}. {e(t['name'])} ×{t['quantity']} — ₹{_inr(t['revenue'])}"
                  for i, t in enumerate(d["top_dishes"], 1)]

    if not d["orders"]:
        lines += ["", "<i>No paid orders today.</i>"]

    lines += ["", "<i>— BillByte</i>"]
    return "\n".join(lines)


def build_csv(d: dict, report_date: str) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["BillByte Daily Report", d["restaurant_name"], report_date])
    w.writerow([])
    w.writerow(["Metric", "Value"])
    for k, label in [("revenue", "Revenue (Rs.)"), ("orders", "Paid orders"),
                     ("avg_bill", "Average bill (Rs.)"), ("gst", "GST collected (Rs.)"),
                     ("discounts", "Discounts given (Rs.)"), ("new_customers", "New customers"),
                     ("cancelled_orders", "Cancelled orders")]:
        w.writerow([label, d[k]])
    w.writerow([])
    w.writerow(["Payment method", "Orders", "Revenue (Rs.)"])
    for p in d["by_payment"]:
        w.writerow([p["method"], p["count"], p["revenue"]])
    w.writerow([])
    w.writerow(["Channel", "Revenue (Rs.)"])
    for t in d["by_type"]:
        w.writerow([_TYPE_LABELS.get(t["type"], t["type"]), t["revenue"]])
    w.writerow([])
    w.writerow(["Top dish", "Qty", "Revenue (Rs.)"])
    for t in d["top_dishes"]:
        w.writerow([t["name"], t["quantity"], t["revenue"]])
    return buf.getvalue().encode("utf-8-sig")  # BOM so Excel opens it cleanly


def build_pdf(d: dict, date_label: str) -> bytes:
    """One-page summary PDF. Core fonts are latin-1, so amounts use "Rs."."""
    from fpdf import FPDF

    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    pdf.set_font("helvetica", "B", 18)
    pdf.cell(0, 10, "Daily Report", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("helvetica", "", 12)
    pdf.cell(0, 7, f"{d['restaurant_name']} - {date_label}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    def kv_row(label, value, bold=False):
        pdf.set_font("helvetica", "B" if bold else "", 11)
        pdf.cell(90, 8, label, border="B")
        pdf.cell(0, 8, str(value), border="B", align="R", new_x="LMARGIN", new_y="NEXT")

    kv_row("Revenue", f"Rs. {_inr(d['revenue'])}", bold=True)
    kv_row("Paid orders", d["orders"])
    kv_row("Average bill", f"Rs. {_inr(d['avg_bill'])}")
    kv_row("GST collected", f"Rs. {_inr(d['gst'])}")
    kv_row("Discounts given", f"Rs. {_inr(d['discounts'])}")
    kv_row("New customers", d["new_customers"])
    kv_row("Cancelled orders", d["cancelled_orders"])

    def table(title, headers, rows):
        if not rows:
            return
        pdf.ln(6)
        pdf.set_font("helvetica", "B", 13)
        pdf.cell(0, 8, title, new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("helvetica", "B", 10)
        widths = [100, 30, 50]
        for h, wd in zip(headers, widths):
            pdf.cell(wd, 7, h, border=1)
        pdf.ln()
        pdf.set_font("helvetica", "", 10)
        for r in rows:
            for v, wd in zip(r, widths):
                pdf.cell(wd, 7, str(v)[:52], border=1)
            pdf.ln()

    table("Payment methods", ["Method", "Orders", "Revenue (Rs.)"],
          [(p["method"].upper(), p["count"], _inr(p["revenue"])) for p in d["by_payment"]])
    table("Top dishes", ["Dish", "Qty", "Revenue (Rs.)"],
          [(t["name"], t["quantity"], _inr(t["revenue"])) for t in d["top_dishes"]])

    pdf.set_y(-25)
    pdf.set_font("helvetica", "I", 9)
    pdf.cell(0, 6, "Generated by BillByte", align="C")
    return bytes(pdf.output())


# ── Entry point ─────────────────────────────────────────────────────────────────

async def generate_daily_report(db: AsyncSession, restaurant_id: int,
                                when: datetime | None = None) -> ReportBundle:
    start, end, local_date = _local_day_window(when)
    d = await _collect(db, restaurant_id, start, end)

    report_date = local_date.isoformat()
    date_label = local_date.strftime("%a, %d %b %Y")

    files = []
    wanted = {p.strip().lower() for p in settings.REPORT_ATTACHMENTS.split(",") if p.strip()}
    if "csv" in wanted:
        files.append((f"BillByte-{report_date}.csv", build_csv(d, report_date), "text/csv"))
    if "pdf" in wanted:
        files.append((f"BillByte-{report_date}.pdf", build_pdf(d, date_label), "application/pdf"))

    return ReportBundle(
        report_date=report_date,
        date_label=date_label,
        text=format_report_text(d, date_label),
        files=files,
        data=d,
    )
