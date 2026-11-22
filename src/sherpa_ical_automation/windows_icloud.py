import getpass
import json
import re
import sys
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlsplit

from .calendar import Event, read_snapshot, render_calendar
from .storage import write_atomic, write_json
from .sync import PENDING_PREFERENCE, ZONE, seed_snapshot
from .windows import install_task

CALENDAR_NAME = "Sherpa Lessons (Windows)"
ICLOUD_URL = "https://caldav.icloud.com/"
OWNERSHIP_PREFIX = "Sherpa sync ID: "


class ICloudError(ValueError):
    pass


def publish_windows_icloud(state: Path, *, setup: bool = False) -> dict | None:
    if sys.platform != "win32":
        raise ValueError("This iCloud publisher requires Windows.")
    try:
        import msvcrt
        from caldav import DAVClient
        from keyring.backends.Windows import WinVaultKeyring
    except ImportError:
        raise ValueError("Run uv sync --extra sync --extra icloud-windows first.") from None

    if setup:
        seed_snapshot(state, None)
    settings_path = state / "icloud-windows.json"
    if not setup and not settings_path.is_file():
        raise ValueError("Run setup-icloud on Windows first.")
    with (state / "icloud-windows.lock").open("a+b") as lock:
        if lock.tell() == 0:
            lock.write(b"\0")
            lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            if setup:
                raise ValueError("An iCloud sync is already in progress.") from None
            return None
        result = publish_locked(state, settings_path, setup, DAVClient, WinVaultKeyring())
    if setup:
        install_task(state, "iCloud", ["sync-icloud", "--state", str(state)], interval="PT1M")
    return result


def publish_locked(state: Path, settings_path: Path, setup: bool, client_class, vault) -> dict | None:
    events, _ = read_snapshot(
        state / "bookings.json", ZONE, (state / PENDING_PREFERENCE).is_file(), True
    )
    digest = sha256(render_calendar(events, datetime.fromtimestamp(0, UTC))).hexdigest()
    revision = state / "icloud-windows-last-sync"
    if not setup and revision.is_file() and revision.read_text(encoding="ascii") == digest:
        return None

    service = "Sherpa Calendar iCloud " + sha256(str(state).encode()).hexdigest()[:16]
    credential = vault.get_credential(service, None)
    if setup:
        if not sys.stdin.isatty():
            raise ValueError("Run setup-icloud in an interactive PowerShell or Command Prompt.")
        email = input("Apple Account email: ").strip()
        if not email or (credential and settings_path.is_file() and credential.username != email):
            raise ValueError("Enter the Apple Account for this installation.")
        password = getpass.getpass("Apple app-specific password: ").strip()
        if not password:
            raise ValueError("An app-specific password is required.")
    else:
        if not credential:
            raise ValueError("iCloud credentials are missing. Run setup-icloud again.")
        email, password = credential.username, credential.password

    try:
        with client_class(url=ICLOUD_URL, username=email, password=password, features="icloud", timeout=20) as client:
            if settings_path.is_file():
                url = json.loads(settings_path.read_bytes())["url"]
                calendar = client.calendar(url=icloud_calendar_url(url))
            else:
                principal = client.principal()
                if any(calendar.name == CALENDAR_NAME for calendar in principal.get_calendars()):
                    raise ICloudError(f"An iCloud calendar named {CALENDAR_NAME} already exists. Rename it before setup.")
                calendar = principal.make_calendar(name=CALENDAR_NAME)
                write_json(settings_path, {"url": icloud_calendar_url(str(calendar.url))})
            if setup:
                vault.set_password(service, email, password)
            report = reconcile(calendar, events)
    except ICloudError:
        raise
    except Exception as error:
        # Client errors may include private calendar URLs or account information.
        raise ValueError(
            f"iCloud Calendar request failed ({type(error).__name__}). "
            "Check iCloud Calendar and the app-specific password, then try again."
        ) from None
    write_atomic(revision, digest.encode("ascii"))
    return report


def icloud_calendar_url(value: str) -> str:
    parts = urlsplit(value)
    host = parts.hostname or ""
    valid_host = host == "caldav.icloud.com" or re.fullmatch(r"p[0-9]+-caldav\.icloud\.com", host)
    if (parts.scheme != "https" or not valid_host or parts.port not in (None, 443)
            or parts.username or parts.password or parts.query or parts.fragment or parts.path in ("", "/")):
        raise ICloudError("iCloud returned an unexpected calendar address.")
    return value


def reconcile(calendar, events: list[Event]) -> dict:
    from icalendar import Calendar

    now = datetime.now(UTC)
    managed = {}
    for resource in calendar.get_events():
        component = resource.icalendar_component
        uid = str(component.get("UID", ""))
        notes = str(component.get("DESCRIPTION", "")).splitlines()
        if uid and notes and notes[0] == OWNERSHIP_PREFIX + uid:
            managed[uid] = resource

    report = {"added": 0, "updated": 0, "removed": 0}
    fields = ("SUMMARY", "DTSTART", "DTEND", "STATUS", "TRANSP")
    for event in events:
        desired = Calendar.from_ical(render_calendar([event], now))
        component = desired.walk("VEVENT")[0]
        resource = managed.pop(event.uid, None)
        if resource is None:
            component.add("DESCRIPTION", OWNERSHIP_PREFIX + event.uid)
            calendar.add_event(desired.to_ical().decode("utf-8"), no_overwrite=True)
            report["added"] += 1
            continue
        data = resource.icalendar_instance
        current = data.walk("VEVENT")[0]
        if any(current.get(field) != component[field] for field in fields):
            current.pop("DURATION", None)
            for field in (*fields, "DTSTAMP"):
                current[field] = component[field]
            resource.save(no_create=True, precondition="etag", raw_data=data.to_ical())
            report["updated"] += 1
    for resource in managed.values():
        start = resource.icalendar_component.decoded("DTSTART", None)
        if isinstance(start, datetime) and start.tzinfo is not None and start > now:
            resource.delete()
            report["removed"] += 1
    return report
