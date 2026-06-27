"""
Booking tool integration tests — availability check + booking against the DB.

Requires PostgreSQL running and DATABASE_URL reachable (see docker-compose.yml).
Run:  PYTHONPATH=backend python backend/tests/test_booking.py

Uses a random far-future date each run so repeated runs never collide on a slot.
"""
import asyncio
import random
import sys
from datetime import date, timedelta

from tools.appointment import (
    book_appointment_impl,
    cancel_appointment_impl,
    check_availability_impl,
    get_appointments_impl,
    lookup_appointment_impl,
    reschedule_appointment_impl,
)

PASS, FAIL = "\033[32m✓\033[0m", "\033[31m✗\033[0m"


def check(label: str, ok: bool, detail: str = "") -> None:
    """Print a pass/fail line; exit non-zero on first failure."""
    print(f"  {PASS if ok else FAIL}  {label}" + (f"  ({detail})" if detail else ""))
    if not ok:
        sys.exit(1)


async def main() -> None:
    """Exercise the full availability → book → re-check → list flow."""
    print("\nBooking tool tests\n")

    # Unique future slot + phone so the test is idempotent across runs.
    day = (date.today() + timedelta(days=random.randint(1000, 5000))).isoformat()
    slot = "10:00 AM"
    phone = f"+1555{random.randint(1000000, 9999999)}"

    # 1. Slot is initially available
    a1 = await check_availability_impl(day, slot)
    check("fresh slot is available", a1.get("available") is True, f"{day} {slot}")

    # 2. Booking succeeds and returns a confirmation number
    booked = await book_appointment_impl(
        name="Test Caller", reason="annual check-up",
        date_str=day, time_slot=slot, phone=phone,
    )
    check("booking succeeds", booked.get("success") is True, booked.get("error", ""))
    conf = booked.get("confirmation_number", "")
    check("confirmation number returned", conf.startswith("APT-"), conf)

    # 3. The same slot is now taken (TOCTOU guard)
    a2 = await check_availability_impl(day, slot)
    check("slot is taken after booking", a2.get("available") is False)
    check("alternatives offered when taken", isinstance(a2.get("alternatives"), list))

    # 4. Invalid date is rejected cleanly
    bad = await check_availability_impl("31-12-2030", slot)
    check("invalid date format returns error", "error" in bad)

    # 5. The booking shows up in the appointments list
    appts = await get_appointments_impl(limit=100)
    check("new booking appears in list", any(x["confirmation_number"] == conf for x in appts))

    # 6. Lookup by phone returns the upcoming appointment
    found = await lookup_appointment_impl(phone)
    check("lookup by phone finds the booking", any(x["confirmation_number"] == conf for x in found))

    # 7. Reschedule to a different free slot on the same day
    new_time = "2:00 PM"
    resch = await reschedule_appointment_impl(conf, day, new_time)
    check("reschedule succeeds", resch.get("success") is True, resch.get("error", ""))
    check("reschedule moved the time", resch.get("time_slot") == new_time)

    # 8. The original slot is free again after the move
    freed = await check_availability_impl(day, slot)
    check("original slot is free after reschedule", freed.get("available") is True)

    # 9. Cancel, then verify it drops out of lookup and is idempotent
    cancelled = await cancel_appointment_impl(conf)
    check("cancel succeeds", cancelled.get("success") is True, cancelled.get("error", ""))
    after = await lookup_appointment_impl(phone)
    check("cancelled booking no longer in lookup", all(x["confirmation_number"] != conf for x in after))
    again = await cancel_appointment_impl(conf)
    check("cancel is idempotent", again.get("already_cancelled") is True)

    print("\nAll booking tests passed.\n")


if __name__ == "__main__":
    asyncio.run(main())
