import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, DecimalException
from pathlib import Path
from uuid import NAMESPACE_DNS, uuid5
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Event:
    uid: str
    start: datetime
    end: datetime
    summary: str
    tentative: bool


def read_snapshot(
    source: Path, zone: ZoneInfo, include_pending: bool, include_student_names: bool
) -> tuple[list[Event], datetime]:
    with source.open(encoding="utf-8-sig") as file:
        payload = json.load(file)
        revision = datetime.fromtimestamp(os.fstat(file.fileno()).st_mtime, UTC)

    return parse_bookings(payload, zone, include_pending, include_student_names), revision


def parse_bookings(
    payload: object, zone: ZoneInfo, include_pending: bool, include_student_names: bool
) -> list[Event]:
    if isinstance(payload, dict):
        records = payload.values()
    elif isinstance(payload, list):
        records = payload
    else:
        raise ValueError("Expected a Sherpa booking map or array")

    events = {}
    for number, record in enumerate(records, start=1):
        try:
            event = booking_event(record, zone, include_pending, include_student_names)
            if event is None:
                continue
            if event.uid in events and events[event.uid] != event:
                raise ValueError("Conflicting entries for the same booking ID")
            events[event.uid] = event
        except (ValueError, OverflowError) as error:
            raise ValueError(f"Booking {number}: {error}") from None
    return sorted(events.values(), key=lambda event: (event.start, event.uid))


def booking_event(
    record: object, zone: ZoneInfo, include_pending: bool, include_student_names: bool
) -> Event | None:
    if not isinstance(record, dict) or not isinstance(record.get("status"), str):
        raise ValueError("Expected a Sherpa booking with a status")
    if record.get("modelType") in ("cancelled", "canceled"):
        return None
    status = record["status"]
    tentative = status == "pending"
    if status != "upcoming" and not (include_pending and tentative):
        return None

    booking_id = record.get("id")
    if type(booking_id) is not int or booking_id <= 0:
        raise ValueError("Expected a positive integer booking ID")
    uid = str(uuid5(NAMESPACE_DNS, f"sherpa-online.com/booking/{booking_id}"))

    start = parse_time(record.get("meetingDateTime") or record.get("when"), zone, "start")
    if record.get("until"):
        end = parse_time(record["until"], zone, "end")
    else:
        end = start + parse_duration(record.get("duration"))
    if end <= start:
        raise ValueError("The end time must be later than the start time")

    names = student_names(record) if include_student_names else []
    summary = ", ".join(names) if names else "Sherpa lesson"
    subject = text_field(record.get("subject"), "name")
    if subject:
        summary += f" – {subject}"
    if tentative:
        summary = f"Pending: {summary}"
    return Event(uid, start, end, summary, tentative)


def parse_time(value: object, zone: ZoneInfo, field: str) -> datetime:
    if not isinstance(value, str) or value[10:11] not in ("T", " "):
        raise ValueError(f"Expected an ISO date and time for the {field}")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError(f"Invalid {field} time") from None

    if parsed.tzinfo is not None:
        return parsed.astimezone(UTC)
    localized = parsed.replace(tzinfo=zone)
    utc_time = localized.astimezone(UTC)
    if utc_time.astimezone(zone).replace(tzinfo=None) != parsed:
        raise ValueError(f"The {field} falls inside a daylight-saving gap")
    if localized.utcoffset() != localized.replace(fold=1).utcoffset():
        raise ValueError(f"The {field} is ambiguous; provide a timestamp with a UTC offset")
    return utc_time


def parse_duration(value: object) -> timedelta:
    try:
        seconds = Decimal(str(value)) * 3600
        if seconds.is_finite() and seconds > 0:
            return timedelta(seconds=float(seconds))
    except (DecimalException, OverflowError):
        pass
    raise ValueError("Expected a positive duration in hours when the end time is missing")


def text_field(record: object, key: str) -> str:
    if isinstance(record, dict) and isinstance(record.get(key), str):
        return " ".join(record[key].split())
    return ""


def student_names(record: dict) -> list[str]:
    students = record.get("booking_students")
    if isinstance(students, list):
        names = [
            text_field(student.get("user"), "name")
            for student in students
            if isinstance(student, dict)
        ]
        if any(names):
            return list(dict.fromkeys(name for name in names if name))
    user = record.get("user")
    if isinstance(user, dict) and user.get("is_student") is True:
        name = text_field(user, "name")
        if name:
            return [name]
    return []


def escape_text(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def fold_line(line: str) -> str:
    parts = []
    current = []
    size = 0
    for character in line:
        width = len(character.encode("utf-8"))
        if size + width > 75:
            parts.append("".join(current))
            current = [" "]
            size = 1
        current.append(character)
        size += width
    parts.append("".join(current))
    return "\r\n".join(parts)


def render_calendar(events: list[Event], revision: datetime) -> bytes:
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Sherpa iCal Automation//Bookings//EN",
        "CALSCALE:GREGORIAN",
        "X-WR-CALNAME:Sherpa Lessons",
    ]
    if not events:
        # RFC 5545 requires a component even when there are no bookings.
        lines.extend([
            "BEGIN:VTIMEZONE",
            "TZID:Etc/UTC",
            "BEGIN:STANDARD",
            "DTSTART:19700101T000000",
            "TZOFFSETFROM:+0000",
            "TZOFFSETTO:+0000",
            "END:STANDARD",
            "END:VTIMEZONE",
        ])
    for event in events:
        lines.extend([
            "BEGIN:VEVENT",
            f"UID:{event.uid}",
            f"DTSTAMP:{revision:%Y%m%dT%H%M%SZ}",
            f"DTSTART:{event.start:%Y%m%dT%H%M%SZ}",
            f"DTEND:{event.end:%Y%m%dT%H%M%SZ}",
            f"SUMMARY:{escape_text(event.summary)}",
            "STATUS:TENTATIVE" if event.tentative else "STATUS:CONFIRMED",
            "TRANSP:TRANSPARENT" if event.tentative else "TRANSP:OPAQUE",
            "END:VEVENT",
        ])
    lines.append("END:VCALENDAR")
    return ("\r\n".join(fold_line(line) for line in lines) + "\r\n").encode("utf-8")
