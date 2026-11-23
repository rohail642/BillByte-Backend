"""Staff records, attendance, advances and monthly salary.

All endpoints are owner/manager only: they expose salaries.
Dates are India-local (settings.TIMEZONE), never the server's UTC date.
"""
from calendar import monthrange
from datetime import date, datetime, timedelta
from typing import List, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import require_owner
from app.db.session import get_db
from app.models.staff import Staff, AttendanceLog, StaffAdvance, SalaryPayout
from app.models.user import User, Restaurant
from app.schemas.staff import (
    StaffCreate, StaffUpdate, StaffOut, AttendanceMark, BulkAttendance,
    AdvanceCreate, PayoutCreate, StaffSettings,
)

router = APIRouter(prefix="/staff", tags=["Staff"])

BACKFILL_DAYS = 60  # how far back attendance can be marked or corrected


# ── helpers ───────────────────────────────────────────────────────────────────
def today_local() -> date:
    return datetime.now(ZoneInfo(settings.TIMEZONE)).date()


def _local_date(dt) -> Optional[date]:
    if dt is None:
        return None
    if isinstance(dt, datetime):
        return (dt.astimezone(ZoneInfo(settings.TIMEZONE)) if dt.tzinfo else dt).date()
    return dt


def _month_bounds(year: int, month: int):
    return date(year, month, 1), date(year, month, monthrange(year, month)[1])


def _resolve_month(month: Optional[int], year: Optional[int]):
    t = today_local()
    month, year = month or t.month, year or t.year
    if not (1 <= month <= 12 and 2000 <= year <= 2100):
        raise HTTPException(422, "Invalid month")
    return month, year


def _status_for(member: Staff, day: date, logged: Optional[str]) -> str:
    if logged:
        return logged
    if member.weekly_off is not None and day.weekday() == member.weekly_off:
        return "weekly_off"
    return "unmarked"


def _join_dt(dt):
    """Joining dates arrive as a bare date; store them as midnight India time so the
    calendar day survives whatever time zone the database session uses."""
    if isinstance(dt, datetime) and dt.tzinfo is None:
        return dt.replace(tzinfo=ZoneInfo(settings.TIMEZONE))
    return dt


def _out(member: Staff, status: str) -> dict:
    return {
        "id": member.id, "name": member.name, "role": member.role, "phone": member.phone,
        "email": member.email, "salary": member.salary or 0.0, "status": status,
        "is_active": member.is_active, "join_date": member.join_date,
        "weekly_off": member.weekly_off, "notes": member.notes,
    }


async def _get_member(db: AsyncSession, rid: int, staff_id: int, active_only: bool = True) -> Staff:
    q = select(Staff).where(Staff.id == staff_id, Staff.restaurant_id == rid)
    if active_only:
        q = q.where(Staff.is_active == True)  # noqa: E712
    member = (await db.execute(q)).scalar_one_or_none()
    if not member:
        raise HTTPException(404, "Staff member not found")
    return member


async def _logs_on(db: AsyncSession, rid: int, day: date) -> dict:
    rows = (await db.execute(
        select(AttendanceLog).where(AttendanceLog.restaurant_id == rid, AttendanceLog.date == day)
        .order_by(AttendanceLog.marked_at)
    )).scalars().all()
    return {l.staff_id: l for l in rows}  # latest wins if duplicates exist


def _check_mark_date(member: Staff, day: date):
    t = today_local()
    if day > t:
        raise HTTPException(422, "Can't mark attendance for a future date")
    if day < t - timedelta(days=BACKFILL_DAYS):
        raise HTTPException(422, f"Attendance can only be changed for the last {BACKFILL_DAYS} days")
    jd = _local_date(member.join_date)
    if jd and day < jd:
        raise HTTPException(422, f"{member.name} joined on {jd.strftime('%d %b %Y')}")


async def _upsert_log(db: AsyncSession, member: Staff, day: date, status: Optional[str], notes: Optional[str]):
    existing = (await db.execute(
        select(AttendanceLog).where(AttendanceLog.staff_id == member.id, AttendanceLog.date == day)
    )).scalars().all()
    if status is None:
        for l in existing:
            await db.delete(l)
        return
    if existing:
        keep, *dupes = existing
        keep.status, keep.notes = status, notes
        for d in dupes:
            await db.delete(d)
    else:
        db.add(AttendanceLog(staff_id=member.id, restaurant_id=member.restaurant_id,
                             date=day, status=status, notes=notes))
    if day == today_local():
        member.status = status  # legacy column


# ── salary calculation ────────────────────────────────────────────────────────
def compute_salary(member: Staff, logs: dict, advances: float, year: int, month: int,
                   paid_leaves: int, today: date) -> dict:
    """Month salary from attendance.

    - present = 1 day worked, half_day = 0.5 worked + 0.5 leave
    - off (marked) and unmarked weekly-off days are paid days off
    - leave beyond the restaurant's paid-leave allowance is deducted
    - days before the join date are unpaid
    - unmarked working days and future days are not deducted (shown as a warning)
    """
    days_in_month = monthrange(year, month)[1]
    jd = _local_date(member.join_date)
    c = {"present": 0, "half_day": 0, "leave": 0, "off": 0, "weekly_off": 0, "unmarked": 0,
         "before_joining": 0, "upcoming": 0}
    for d in range(1, days_in_month + 1):
        day = date(year, month, d)
        if jd and day < jd:
            c["before_joining"] += 1
        elif day > today:
            c["upcoming"] += 1
        else:
            c[_status_for(member, day, logs.get(day))] += 1
    salary = float(member.salary or 0)
    leave_days = c["leave"] + 0.5 * c["half_day"]
    paid_leave = min(leave_days, paid_leaves)
    unpaid_days = (leave_days - paid_leave) + c["before_joining"]
    deduction = round(salary / days_in_month * unpaid_days, 2)
    earned = round(salary - deduction, 2)
    return {
        "days_in_month": days_in_month,
        "counts": c,
        "worked_days": c["present"] + 0.5 * c["half_day"],
        "leave_days": leave_days,
        "paid_leave_used": paid_leave,
        "unpaid_days": unpaid_days,
        "base_salary": salary,
        "deduction": deduction,
        "earned": earned,
        "advances": round(advances, 2),
        "net_payable": round(earned - advances, 2),
    }


async def _month_data(db: AsyncSession, rid: int, year: int, month: int, staff_ids: List[int]):
    start, end = _month_bounds(year, month)
    logs = {}
    for l in (await db.execute(
        select(AttendanceLog).where(AttendanceLog.restaurant_id == rid, AttendanceLog.staff_id.in_(staff_ids),
                                    AttendanceLog.date >= start, AttendanceLog.date <= end)
        .order_by(AttendanceLog.marked_at)
    )).scalars().all():
        logs.setdefault(l.staff_id, {})[l.date] = l.status
    adv = dict((await db.execute(
        select(StaffAdvance.staff_id, func.coalesce(func.sum(StaffAdvance.amount), 0))
        .where(StaffAdvance.restaurant_id == rid, StaffAdvance.staff_id.in_(staff_ids),
               StaffAdvance.date >= start, StaffAdvance.date <= end)
        .group_by(StaffAdvance.staff_id)
    )).all())
    payouts = {p.staff_id: p for p in (await db.execute(
        select(SalaryPayout).where(SalaryPayout.restaurant_id == rid, SalaryPayout.year == year,
                                   SalaryPayout.month == month, SalaryPayout.staff_id.in_(staff_ids))
    )).scalars().all()}
    return logs, adv, payouts


async def _paid_month(db: AsyncSession, staff_id: int, day: date) -> bool:
    return (await db.execute(select(SalaryPayout.id).where(
        SalaryPayout.staff_id == staff_id, SalaryPayout.year == day.year, SalaryPayout.month == day.month
    ))).first() is not None


def _payout_out(p: SalaryPayout) -> dict:
    return {
        "id": p.id, "paid_amount": p.paid_amount, "method": p.method, "paid_on": str(p.paid_on),
        "note": p.note, "net_payable": p.net_payable, "base_salary": p.base_salary,
        "deduction": p.deduction, "advances": p.advances, "worked_days": p.worked_days,
        "unpaid_days": p.unpaid_days,
    }


# ── settings ──────────────────────────────────────────────────────────────────
@router.get("/settings")
async def get_staff_settings(db: AsyncSession = Depends(get_db), u: User = Depends(require_owner)):
    rest = await db.get(Restaurant, u.restaurant_id)
    return {"paid_leaves_per_month": rest.paid_leaves_per_month or 0}


@router.put("/settings")
async def save_staff_settings(body: StaffSettings, db: AsyncSession = Depends(get_db), u: User = Depends(require_owner)):
    rest = await db.get(Restaurant, u.restaurant_id)
    rest.paid_leaves_per_month = body.paid_leaves_per_month
    return {"paid_leaves_per_month": rest.paid_leaves_per_month}


# ── staff records ─────────────────────────────────────────────────────────────
@router.get("/", response_model=List[StaffOut])
async def list_staff(db: AsyncSession = Depends(get_db), u: User = Depends(require_owner)):
    members = (await db.execute(
        select(Staff).where(Staff.restaurant_id == u.restaurant_id, Staff.is_active == True)  # noqa: E712
        .order_by(Staff.name)
    )).scalars().all()
    t = today_local()
    logs = await _logs_on(db, u.restaurant_id, t)
    return [_out(m, _status_for(m, t, logs[m.id].status if m.id in logs else None)) for m in members]


@router.post("/", response_model=StaffOut, status_code=201)
async def create_staff(body: StaffCreate, db: AsyncSession = Depends(get_db), u: User = Depends(require_owner)):
    data = body.model_dump()
    data["join_date"] = _join_dt(data.get("join_date"))
    member = Staff(restaurant_id=u.restaurant_id, status="present", **data)
    db.add(member)
    await db.flush()
    return _out(member, _status_for(member, today_local(), None))


@router.patch("/{staff_id}", response_model=StaffOut)
async def update_staff(staff_id: int, body: StaffUpdate, db: AsyncSession = Depends(get_db), u: User = Depends(require_owner)):
    member = await _get_member(db, u.restaurant_id, staff_id, active_only=False)
    for k, v in body.model_dump(exclude_unset=True).items():
        if v is None and k in ("name", "role", "salary", "is_active"):
            continue  # required fields can't be cleared
        setattr(member, k, _join_dt(v) if k == "join_date" else v)
    t = today_local()
    logs = await _logs_on(db, u.restaurant_id, t)
    return _out(member, _status_for(member, t, logs[member.id].status if member.id in logs else None))


@router.delete("/{staff_id}", status_code=204)
async def delete_staff(staff_id: int, db: AsyncSession = Depends(get_db), u: User = Depends(require_owner)):
    member = await _get_member(db, u.restaurant_id, staff_id)
    member.is_active = False  # soft delete: keeps attendance and salary history
    return Response(status_code=204)


# ── attendance ────────────────────────────────────────────────────────────────
@router.get("/attendance/day")
async def attendance_for_day(day: Optional[date] = None, db: AsyncSession = Depends(get_db), u: User = Depends(require_owner)):
    """Everyone's status on one day — the daily attendance sheet."""
    day = day or today_local()
    members = (await db.execute(
        select(Staff).where(Staff.restaurant_id == u.restaurant_id, Staff.is_active == True)  # noqa: E712
        .order_by(Staff.name)
    )).scalars().all()
    logs = await _logs_on(db, u.restaurant_id, day)
    out = []
    for m in members:
        jd = _local_date(m.join_date)
        log = logs.get(m.id)
        out.append({
            "staff_id": m.id, "name": m.name, "role": m.role,
            "status": _status_for(m, day, log.status if log else None),
            "notes": log.notes if log else None,
            "not_joined": bool(jd and day < jd),
        })
    return {"date": str(day), "today": str(today_local()), "backfill_days": BACKFILL_DAYS, "staff": out}


@router.post("/attendance/bulk")
async def mark_bulk(body: BulkAttendance, db: AsyncSession = Depends(get_db), u: User = Depends(require_owner)):
    day = body.date or today_local()
    ids = [e.staff_id for e in body.entries]
    members = {m.id: m for m in (await db.execute(
        select(Staff).where(Staff.restaurant_id == u.restaurant_id, Staff.id.in_(ids), Staff.is_active == True)  # noqa: E712
    )).scalars().all()}
    if len(members) != len(set(ids)):
        raise HTTPException(404, "Staff member not found")
    for e in body.entries:
        _check_mark_date(members[e.staff_id], day)
    for e in body.entries:
        await _upsert_log(db, members[e.staff_id], day, e.status, e.notes)
    return {"ok": True, "date": str(day), "updated": len(body.entries)}


@router.get("/attendance/month")
async def attendance_month(month: Optional[int] = None, year: Optional[int] = None,
                           db: AsyncSession = Depends(get_db), u: User = Depends(require_owner)):
    """Every active staff member's day-by-day attendance for a month (for export)."""
    month, year = _resolve_month(month, year)
    members = (await db.execute(
        select(Staff).where(Staff.restaurant_id == u.restaurant_id, Staff.is_active == True)  # noqa: E712
        .order_by(Staff.name)
    )).scalars().all()
    _, month_end = _month_bounds(year, month)
    members = [m for m in members if not (_local_date(m.join_date) and _local_date(m.join_date) > month_end)]
    logs, _, _ = await _month_data(db, u.restaurant_id, year, month, [m.id for m in members] or [0])
    t = today_local()
    days = monthrange(year, month)[1]
    staff = []
    for m in members:
        ml = logs.get(m.id, {})
        jd = _local_date(m.join_date)
        row = {}
        for d in range(1, days + 1):
            day = date(year, month, d)
            row[str(day)] = None if day > t or (jd and day < jd) else _status_for(m, day, ml.get(day))
        staff.append({"id": m.id, "name": m.name, "role": m.role, "days": row})
    return {"month": month, "year": year, "days_in_month": days, "staff": staff}


@router.patch("/{staff_id}/attendance", response_model=StaffOut)
async def mark_attendance(staff_id: int, body: AttendanceMark, db: AsyncSession = Depends(get_db), u: User = Depends(require_owner)):
    """Mark or correct one person's attendance for today or a recent date."""
    member = await _get_member(db, u.restaurant_id, staff_id)
    day = body.date or today_local()
    _check_mark_date(member, day)
    await _upsert_log(db, member, day, body.status, body.notes)
    t = today_local()
    logs = await _logs_on(db, u.restaurant_id, t)
    return _out(member, _status_for(member, t, logs[member.id].status if member.id in logs else None))


@router.delete("/{staff_id}/attendance/{day}", status_code=204)
async def clear_attendance(staff_id: int, day: date, db: AsyncSession = Depends(get_db), u: User = Depends(require_owner)):
    member = await _get_member(db, u.restaurant_id, staff_id)
    _check_mark_date(member, day)
    await _upsert_log(db, member, day, None, None)
    return Response(status_code=204)


@router.get("/{staff_id}/attendance")
async def get_attendance(staff_id: int, month: Optional[int] = None, year: Optional[int] = None,
                         db: AsyncSession = Depends(get_db), u: User = Depends(require_owner)):
    """One person's month: day logs, counts and the salary calculation."""
    member = await _get_member(db, u.restaurant_id, staff_id, active_only=False)
    month, year = _resolve_month(month, year)
    logs, adv, payouts = await _month_data(db, u.restaurant_id, year, month, [member.id])
    ml = logs.get(member.id, {})
    rest = await db.get(Restaurant, u.restaurant_id)
    t = today_local()
    calc = compute_salary(member, ml, float(adv.get(member.id, 0)), year, month, rest.paid_leaves_per_month or 0, t)
    notes = dict((l.date, l.notes) for l in (await db.execute(
        select(AttendanceLog).where(AttendanceLog.staff_id == member.id,
                                    AttendanceLog.date >= date(year, month, 1),
                                    AttendanceLog.date <= _month_bounds(year, month)[1])
    )).scalars().all())
    c = calc["counts"]
    return {
        "staff_id": member.id, "month": month, "year": year, "today": str(t),
        "backfill_days": BACKFILL_DAYS, "weekly_off": member.weekly_off,
        "join_date": str(_local_date(member.join_date)) if member.join_date else None,
        "summary": {"present": c["present"], "half_day": c["half_day"], "leave": c["leave"],
                    "off": c["off"] + c["weekly_off"], "unmarked": c["unmarked"]},
        "logs": [{"date": str(d), "status": s, "notes": notes.get(d)} for d, s in sorted(ml.items())],
        "salary": calc,
        "payout": _payout_out(payouts[member.id]) if member.id in payouts else None,
    }


# ── advances ──────────────────────────────────────────────────────────────────
@router.get("/{staff_id}/advances")
async def list_advances(staff_id: int, month: Optional[int] = None, year: Optional[int] = None,
                        db: AsyncSession = Depends(get_db), u: User = Depends(require_owner)):
    await _get_member(db, u.restaurant_id, staff_id, active_only=False)
    month, year = _resolve_month(month, year)
    start, end = _month_bounds(year, month)
    rows = (await db.execute(
        select(StaffAdvance).where(StaffAdvance.restaurant_id == u.restaurant_id, StaffAdvance.staff_id == staff_id,
                                   StaffAdvance.date >= start, StaffAdvance.date <= end)
        .order_by(StaffAdvance.date, StaffAdvance.id)
    )).scalars().all()
    return [{"id": a.id, "date": str(a.date), "amount": a.amount, "note": a.note} for a in rows]


@router.post("/{staff_id}/advances", status_code=201)
async def add_advance(staff_id: int, body: AdvanceCreate, db: AsyncSession = Depends(get_db), u: User = Depends(require_owner)):
    member = await _get_member(db, u.restaurant_id, staff_id)
    day = body.date or today_local()
    if day > today_local():
        raise HTTPException(422, "Advance date can't be in the future")
    if await _paid_month(db, member.id, day):
        raise HTTPException(409, "Salary for that month is already marked paid — undo the payment first")
    a = StaffAdvance(restaurant_id=u.restaurant_id, staff_id=member.id, date=day,
                     amount=round(body.amount, 2), note=body.note, created_by=u.id)
    db.add(a)
    await db.flush()
    return {"id": a.id, "date": str(a.date), "amount": a.amount, "note": a.note}


@router.delete("/advances/{advance_id}", status_code=204)
async def delete_advance(advance_id: int, db: AsyncSession = Depends(get_db), u: User = Depends(require_owner)):
    a = await db.get(StaffAdvance, advance_id)
    if not a or a.restaurant_id != u.restaurant_id:
        raise HTTPException(404, "Advance not found")
    if await _paid_month(db, a.staff_id, a.date):
        raise HTTPException(409, "Salary for that month is already marked paid — undo the payment first")
    await db.delete(a)
    return Response(status_code=204)


# ── salary sheet + payouts ────────────────────────────────────────────────────
@router.get("/payroll")
async def payroll(month: Optional[int] = None, year: Optional[int] = None,
                  db: AsyncSession = Depends(get_db), u: User = Depends(require_owner)):
    month, year = _resolve_month(month, year)
    start, end = _month_bounds(year, month)
    # Active staff, plus anyone who left but worked or was paid that month.
    active = (await db.execute(select(Staff).where(
        Staff.restaurant_id == u.restaurant_id, Staff.is_active == True))).scalars().all()  # noqa: E712
    left_ids = set((await db.execute(select(AttendanceLog.staff_id).join(Staff, Staff.id == AttendanceLog.staff_id).where(
        Staff.restaurant_id == u.restaurant_id, Staff.is_active == False,  # noqa: E712
        AttendanceLog.date >= start, AttendanceLog.date <= end))).scalars().all())
    left_ids |= set((await db.execute(select(SalaryPayout.staff_id).join(Staff, Staff.id == SalaryPayout.staff_id).where(
        Staff.restaurant_id == u.restaurant_id, Staff.is_active == False,  # noqa: E712
        SalaryPayout.year == year, SalaryPayout.month == month))).scalars().all())
    left = (await db.execute(select(Staff).where(Staff.id.in_(left_ids)))).scalars().all() if left_ids else []
    # Skip people who hadn't joined yet by the end of this month.
    members = sorted([m for m in [*active, *left] if not (_local_date(m.join_date) and _local_date(m.join_date) > end)],
                     key=lambda m: (not m.is_active, m.name.lower()))

    rest = await db.get(Restaurant, u.restaurant_id)
    paid_leaves = rest.paid_leaves_per_month or 0
    t = today_local()
    logs, adv, payouts = await _month_data(db, u.restaurant_id, year, month, [m.id for m in members] or [0])
    rows, totals = [], {"base_salary": 0.0, "deduction": 0.0, "advances": 0.0, "net_payable": 0.0, "paid": 0.0}
    for m in members:
        calc = compute_salary(m, logs.get(m.id, {}), float(adv.get(m.id, 0)), year, month, paid_leaves, t)
        p = payouts.get(m.id)
        rows.append({"staff_id": m.id, "name": m.name, "role": m.role, "is_active": m.is_active,
                     **calc, "payout": _payout_out(p) if p else None})
        src = _payout_out(p) if p else calc
        totals["base_salary"] += src["base_salary"]
        totals["deduction"] += src["deduction"]
        totals["advances"] += src["advances"]
        totals["net_payable"] += src["net_payable"]
        totals["paid"] += p.paid_amount if p else 0
    return {
        "month": month, "year": year, "days_in_month": monthrange(year, month)[1],
        "today": str(t), "is_current_month": (year, month) == (t.year, t.month),
        "paid_leaves_per_month": paid_leaves,
        "rows": rows, "totals": {k: round(v, 2) for k, v in totals.items()},
    }


@router.post("/{staff_id}/payout", status_code=201)
async def mark_paid(staff_id: int, body: PayoutCreate, db: AsyncSession = Depends(get_db), u: User = Depends(require_owner)):
    member = await _get_member(db, u.restaurant_id, staff_id, active_only=False)
    t = today_local()
    if date(body.year, body.month, 1) > t:
        raise HTTPException(422, "Can't pay salary for a future month")
    if await _paid_month(db, member.id, date(body.year, body.month, 1)):
        raise HTTPException(409, "This month's salary is already marked paid")
    logs, adv, _ = await _month_data(db, u.restaurant_id, body.year, body.month, [member.id])
    rest = await db.get(Restaurant, u.restaurant_id)
    calc = compute_salary(member, logs.get(member.id, {}), float(adv.get(member.id, 0)),
                          body.year, body.month, rest.paid_leaves_per_month or 0, t)
    p = SalaryPayout(
        restaurant_id=u.restaurant_id, staff_id=member.id, year=body.year, month=body.month,
        base_salary=calc["base_salary"], worked_days=calc["worked_days"], unpaid_days=calc["unpaid_days"],
        deduction=calc["deduction"], advances=calc["advances"], net_payable=calc["net_payable"],
        paid_amount=round(body.paid_amount if body.paid_amount is not None else max(calc["net_payable"], 0), 2),
        method=body.method, paid_on=body.paid_on or t, note=body.note, created_by=u.id,
    )
    db.add(p)
    await db.flush()
    return _payout_out(p)


@router.delete("/{staff_id}/payout", status_code=204)
async def undo_paid(staff_id: int, year: int, month: int, db: AsyncSession = Depends(get_db), u: User = Depends(require_owner)):
    await _get_member(db, u.restaurant_id, staff_id, active_only=False)
    p = (await db.execute(select(SalaryPayout).where(
        SalaryPayout.restaurant_id == u.restaurant_id, SalaryPayout.staff_id == staff_id,
        SalaryPayout.year == year, SalaryPayout.month == month))).scalar_one_or_none()
    if not p:
        raise HTTPException(404, "No payment recorded for that month")
    await db.delete(p)
    return Response(status_code=204)
