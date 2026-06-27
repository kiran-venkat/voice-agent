"""
Unit tests — pure functions only, no DB / network / live services.

These are safe to run anywhere (used in CI):
  PYTHONPATH=backend python backend/tests/test_unit.py

Covers: phone masking, confirmation-number normalisation, and TwiML builders
(valid XML + correct escaping of caller-supplied text).
"""
import sys
from xml.etree import ElementTree as ET

from services.transfer import (
    _mask_phone,
    build_conference_twiml,
    build_decline_twiml,
    build_transfer_answer_twiml,
)
from tools.appointment import _normalize_confirmation

PASS, FAIL = "\033[32m✓\033[0m", "\033[31m✗\033[0m"


def check(label: str, ok: bool, detail: str = "") -> None:
    """Print a pass/fail line; exit non-zero on first failure."""
    print(f"  {PASS if ok else FAIL}  {label}" + (f"  ({detail})" if detail else ""))
    if not ok:
        sys.exit(1)


def main() -> None:
    """Run all pure-function unit checks."""
    print("\nUnit tests\n")

    # ── _mask_phone ───────────────────────────────────────────────────────────
    check("mask keeps last 4 digits", _mask_phone("+15551234567") == "***4567")
    check("mask handles empty", _mask_phone("") == "(unset)")
    check("mask handles short", _mask_phone("12") == "***")

    # ── _normalize_confirmation ───────────────────────────────────────────────
    check("normalize strips APT- prefix, lowercases",
          _normalize_confirmation("APT-AC39CA4E") == "ac39ca4e")
    check("normalize accepts bare code", _normalize_confirmation("ac39ca4e") == "ac39ca4e")
    check("normalize handles empty", _normalize_confirmation("") == "")

    # ── TwiML builders: must be valid XML even with hostile input ─────────────
    twiml = build_transfer_answer_twiml(
        caller_name="A & B <test>", reason="billing & refunds", conference_name="room-1")
    root = ET.fromstring(twiml)  # raises if escaping is wrong → test fails loudly
    check("transfer-answer TwiML is valid XML", root.tag == "Response")
    check("transfer-answer prompts for 1/2", "Press 1" in twiml and "press 2" in twiml.lower())
    check("transfer-answer escapes raw ampersand", "&amp;" in twiml and " & " not in twiml)

    decline = build_decline_twiml()
    check("decline TwiML is valid XML", ET.fromstring(decline).tag == "Response")
    check("decline hangs up", "Hangup" in decline)

    conf = build_conference_twiml("room-1")
    check("conference TwiML is valid XML", ET.fromstring(conf).tag == "Response")
    check("conference joins the named room", "<Conference>room-1</Conference>" in conf)

    print("\nAll unit tests passed.\n")


if __name__ == "__main__":
    main()
