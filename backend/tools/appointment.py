"""
Appointment booking tool implementations.

These are called by the LLM tool wrapper in agent.py.
They run DB queries and return plain dicts that the agent serialises to JSON
and speaks back to the caller.
"""
import logging
from datetime import date, datetime

from sqlalchemy import and_, select

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
