"""
API integration tests — token, health, appointments endpoints.

Requires the FastAPI backend running:  PYTHONPATH=backend python backend/main.py
Run:  python backend/tests/test_api.py
"""
import sys
import uuid

import httpx

API = "http://localhost:8000"
PASS, FAIL = "\033[32m✓\033[0m", "\033[31m✗\033[0m"


def check(label: str, ok: bool, detail: str = "") -> None:
    """Print a pass/fail line; exit non-zero on first failure."""
    print(f"  {PASS if ok else FAIL}  {label}" + (f"  ({detail})" if detail else ""))
    if not ok:
        sys.exit(1)


def main() -> None:
    """Run the API endpoint checks against a live backend."""
    print("\nAPI tests\n")
    try:
        client = httpx.Client(base_url=API, timeout=5.0)
        r = client.get("/health")
    except httpx.ConnectError:
        print(f"  {FAIL}  Could not connect to {API} — is the backend running?")
        sys.exit(1)

    # health
    check("GET /health returns 200", r.status_code == 200, str(r.status_code))
    check("health status is ok", r.json().get("status") == "ok")

    # token — caller gets a token + livekit_url + caller identity
    rc = client.post("/api/token", json={
        "room_name": "test-room", "participant_name": "tester", "role": "caller"})
    check("POST /api/token (caller) returns 200", rc.status_code == 200, str(rc.status_code))
    body = rc.json()
    check("token present", bool(body.get("token")))
    check("livekit_url present", bool(body.get("livekit_url")))
    check("identity is a caller", str(body.get("identity", "")).startswith("caller-"), body.get("identity"))

    # token — watcher role also works
    rw = client.post("/api/token", json={
        "room_name": "test-room", "participant_name": "watch", "role": "watcher"})
    check("POST /api/token (watcher) returns 200", rw.status_code == 200, str(rw.status_code))
    check("identity is a watcher", str(rw.json().get("identity", "")).startswith("watcher-"))

    # appointments list
    ra = client.get("/api/appointments")
    check("GET /api/appointments returns 200", ra.status_code == 200, str(ra.status_code))
    check("appointments is a list", isinstance(ra.json(), list))

    # appointment by id — bad format → 400, valid-but-missing → 404
    check("GET /api/appointments/<bad> returns 400",
          client.get("/api/appointments/not-a-uuid").status_code == 400)
    check("GET /api/appointments/<unknown-uuid> returns 404",
          client.get(f"/api/appointments/{uuid.uuid4()}").status_code == 404)

    client.close()
    print("\nAll API tests passed.\n")


if __name__ == "__main__":
    main()
