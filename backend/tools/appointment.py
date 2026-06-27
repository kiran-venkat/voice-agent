"""
Appointment booking tool implementations.

These are called by the LLM tool wrapper in agent.py.
They run DB queries and return plain dicts that the agent serialises to JSON
and speaks back to the caller.
"""
import logging
from datetime import date, datetime

from sqlalchemy import String, and_, cast, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Appointment
from database.session import AsyncSessionLocal

logger = logging.getLogger(__name__)

# Slots considered busy if an appointment already exists for that date+time
# and has status != 'cancelled'
AVAILABLE_STATUSES_BLOCKING = {"confirmed", "completed"}

# Business-hours slots available per day (for availability listing)
ALL_SLOTS = [
    "9:00 AM", "9:30 AM", "10:00 AM", "10:30 AM", "11:00 AM", "11:30 AM",
    "2:00 PM", "2:30 PM", "3:00 PM", "3:30 PM", "4:00 PM", "4:30 PM",
]


async def check_availability_impl(date_str: str, time_slot: str) -> dict:
    """
    Check whether a specific date + time slot is available.

    Returns available=True with nearest alternatives, or available=False with
    the next three open slots on that day.
    """
    try:
        appt_date = date.fromisoformat(date_str)
    except ValueError:
        return {"error": f"Invalid date format: {date_str}. Use YYYY-MM-DD."}

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Appointment).where(
                and_(
                    Appointment.date == appt_date,
                    Appointment.time_slot == time_slot,
                    Appointment.status.in_(AVAILABLE_STATUSES_BLOCKING),
                )
            )
        )
        conflict = result.scalars().first()

        if conflict is None:
            return {
                "available": True,
                "date": date_str,
                "time_slot": time_slot,
                "message": f"{time_slot} on {date_str} is available.",
            }

        # Find other open slots on the same day
        booked_result = await session.execute(
            select(Appointment.time_slot).where(
                and_(
                    Appointment.date == appt_date,
                    Appointment.status.in_(AVAILABLE_STATUSES_BLOCKING),
                )
            )
        )
        booked_slots = {row[0] for row in booked_result.all()}
        alternatives = [s for s in ALL_SLOTS if s not in booked_slots and s != time_slot][:3]

        return {
            "available": False,
            "date": date_str,
            "time_slot": time_slot,
            "message": f"{time_slot} on {date_str} is already booked.",
            "alternatives": alternatives,
        }


async def book_appointment_impl(
    name: str,
    reason: str,
    date_str: str,
    time_slot: str,
    phone: str,
    room_name: str | None = None,
) -> dict:
    """
    Insert a confirmed appointment into the database.

    Performs a final availability check before inserting to guard against
    the TOCTOU window between check and confirm.
    """
    try:
        appt_date = date.fromisoformat(date_str)
    except ValueError:
        return {"error": f"Invalid date format: {date_str}. Use YYYY-MM-DD."}

    async with AsyncSessionLocal() as session:
        # Final conflict check
        result = await session.execute(
            select(Appointment).where(
                and_(
                    Appointment.date == appt_date,
                    Appointment.time_slot == time_slot,
                    Appointment.status.in_(AVAILABLE_STATUSES_BLOCKING),
                )
            )
        )
        if result.scalars().first():
            return {
                "success": False,
                "error": f"{time_slot} on {date_str} was just taken. Please choose another slot.",
            }

        appointment = Appointment(
            name=name,
            reason=reason,
            date=appt_date,
            time_slot=time_slot,
            phone=phone,
            room_name=room_name,
            status="confirmed",
        )
        session.add(appointment)
        await session.commit()
        await session.refresh(appointment)

        logger.info("Appointment booked: %s for %s on %s %s", appointment.id, name, date_str, time_slot)

        return {
            "success": True,
            "confirmation_number": appointment.confirmation_number,
            "name": name,
            "date": date_str,
            "time_slot": time_slot,
            "phone": phone,
            "reason": reason,
            "message": (
                f"Appointment confirmed! Your confirmation number is "
                f"{appointment.confirmation_number}. We'll see you on "
                f"{date_str} at {time_slot}."
            ),
        }


async def get_appointments_impl(limit: int = 50) -> list[dict]:
    """Fetch recent appointments for the dashboard."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Appointment).order_by(Appointment.created_at.desc()).limit(limit)
        )
        appointments = result.scalars().all()
        return [
            {
                "id": str(a.id),
                "confirmation_number": a.confirmation_number,
                "name": a.name,
                "reason": a.reason,
                "date": a.date.isoformat(),
                "time_slot": a.time_slot,
                "phone": a.phone,
                "status": a.status,
                "created_at": a.created_at.isoformat(),
            }
            for a in appointments
        ]


def _normalize_confirmation(confirmation_number: str) -> str:
    """Reduce a confirmation number (e.g. 'APT-AC39CA4E') to the lowercase UUID
    prefix used to look up the appointment. Returns '' if nothing usable."""
    code = (confirmation_number or "").strip().upper()
    if code.startswith("APT-"):
        code = code[len("APT-"):]
    return code.lower()


async def _find_by_confirmation(
    session: AsyncSession, confirmation_number: str
) -> Appointment | None:
    """Resolve an appointment from its confirmation number.

    confirmation_number is APT-<first 8 of the UUID>, derived (not stored), so we
    match the cast-to-text UUID by that prefix.
    """
    prefix = _normalize_confirmation(confirmation_number)
    if not prefix:
        return None
    result = await session.execute(
        select(Appointment).where(cast(Appointment.id, String).ilike(f"{prefix}%"))
    )
    return result.scalars().first()


async def reschedule_appointment_impl(
    confirmation_number: str,
    new_date: str,
    new_time: str,
) -> dict:
    """
    Move an existing appointment to a new date/time.

    Verifies the new slot is free (excluding the appointment itself) before
    updating. Returns the updated confirmation, or an error the agent reads back.
    """
    try:
        new_appt_date = date.fromisoformat(new_date)
    except ValueError:
        return {"success": False, "error": f"Invalid date format: {new_date}. Use YYYY-MM-DD."}

    async with AsyncSessionLocal() as session:
        appt = await _find_by_confirmation(session, confirmation_number)
        if appt is None:
            return {"success": False, "error": f"No appointment found for {confirmation_number}."}
        if appt.status == "cancelled":
            return {
                "success": False,
                "error": "That appointment was cancelled and cannot be rescheduled.",
            }

        conflict = await session.execute(
            select(Appointment).where(
                and_(
                    Appointment.date == new_appt_date,
                    Appointment.time_slot == new_time,
                    Appointment.status.in_(AVAILABLE_STATUSES_BLOCKING),
                    Appointment.id != appt.id,
                )
            )
        )
        if conflict.scalars().first():
            return {
                "success": False,
                "error": f"{new_time} on {new_date} is already booked. Please choose another slot.",
            }

        appt.date = new_appt_date
        appt.time_slot = new_time
        await session.commit()
        await session.refresh(appt)

        logger.info(
            "Appointment rescheduled: %s -> %s %s", appt.confirmation_number, new_date, new_time
        )
        return {
            "success": True,
            "confirmation_number": appt.confirmation_number,
            "name": appt.name,
            "date": new_date,
            "time_slot": new_time,
            "message": (
                f"Done. Your appointment {appt.confirmation_number} is now on "
                f"{new_date} at {new_time}."
            ),
        }


async def cancel_appointment_impl(confirmation_number: str) -> dict:
    """
    Mark an appointment as cancelled.

    Idempotent: cancelling an already-cancelled appointment succeeds with a note.
    """
    async with AsyncSessionLocal() as session:
        appt = await _find_by_confirmation(session, confirmation_number)
        if appt is None:
            return {"success": False, "error": f"No appointment found for {confirmation_number}."}

        if appt.status == "cancelled":
            return {
                "success": True,
                "already_cancelled": True,
                "confirmation_number": appt.confirmation_number,
                "message": f"Appointment {appt.confirmation_number} was already cancelled.",
            }

        appt.status = "cancelled"
        await session.commit()
        await session.refresh(appt)

        logger.info("Appointment cancelled: %s", appt.confirmation_number)
        return {
            "success": True,
            "confirmation_number": appt.confirmation_number,
            "message": (
                f"Your appointment {appt.confirmation_number} on "
                f"{appt.date.isoformat()} at {appt.time_slot} has been cancelled."
            ),
        }


async def lookup_appointment_impl(phone: str) -> list[dict]:
    """
    Return a caller's upcoming (today onward), non-cancelled appointments by phone.

    Ordered soonest-first so the agent can read them back naturally.
    """
    today = date.today()
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Appointment)
            .where(
                and_(
                    Appointment.phone == phone,
                    Appointment.status == "confirmed",
                    Appointment.date >= today,
                )
            )
            .order_by(Appointment.date, Appointment.time_slot)
        )
        appointments = result.scalars().all()
        return [
            {
                "confirmation_number": a.confirmation_number,
                "name": a.name,
                "reason": a.reason,
                "date": a.date.isoformat(),
                "time_slot": a.time_slot,
                "status": a.status,
            }
            for a in appointments
        ]
