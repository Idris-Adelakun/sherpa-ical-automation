import json
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from .calendar import parse_bookings, student_names, text_field
from .storage import write_json

BOOKINGS_URL = "https://sherpa-online.com/tutor/bookings"
SHERPA_HOSTS = {"sherpa-online.com", "www.sherpa-online.com", "app.sherpa-online.com"}
ZONE = ZoneInfo("Europe/London")
STATE = Path(".sherpa")


def sherpa_route(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme == "https" and parts.netloc in SHERPA_HOSTS:
        return f"https://{parts.netloc}{parts.path}"
    return ""


def booking_record(value: object) -> bool:
    return (
        isinstance(value, dict)
        and type(value.get("id")) is int
        and isinstance(value.get("status"), str)
        and ("meetingDateTime" in value or "when" in value)
    )


def collections(payload, path=()):
    if isinstance(payload, dict):
        values = list(payload.values())
    elif isinstance(payload, list):
        values = payload
    else:
        return
    if values and all(booking_record(value) for value in values):
        yield path, values
    elif isinstance(payload, dict) and len(path) < 5:
        for key, value in payload.items():
            yield from collections(value, (*path, key))


class InlineScripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.inside_script = False
        self.scripts = []

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.inside_script = True

    def handle_endtag(self, tag):
        if tag == "script":
            self.inside_script = False

    def handle_data(self, data):
        if self.inside_script and "meetingDateTime" in data:
            self.scripts.append(data)


def inline_collections(html: str):
    parser = InlineScripts()
    parser.feed(html)
    decoder = json.JSONDecoder()
    for script in parser.scripts:
        # Legacy Sherpa can embed a JSON array or a map keyed by booking reference.
        starts = re.finditer(r'\[\s*\{|\{\s*"[^"\\]+"\s*:\s*(?:\{|\[)', script)
        consumed = 0
        for match in starts:
            if match.start() < consumed:
                continue
            try:
                payload, end = decoder.raw_decode(script, match.start())
            except json.JSONDecodeError:
                continue
            found = list(collections(payload))
            if found:
                consumed = end
                yield from found


def calendar_fields(booking: dict) -> dict:
    keys = ("id", "status", "modelType", "meetingDateTime", "when", "until", "duration")
    record = {key: booking[key] for key in keys if key in booking}
    record["subject"] = {"name": text_field(booking.get("subject"), "name")}
    record["booking_students"] = [{"user": {"name": name}} for name in student_names(booking)]
    return record


def save_bookings(state: Path, records: list[dict]) -> int:
    cleaned = [calendar_fields(record) for record in records]
    events = parse_bookings(cleaned, ZONE, True, True)
    path = state / "bookings.json"
    if not path.exists() or json.loads(path.read_text()) != cleaned:
        write_json(path, cleaned)
    return sum(not event.tentative for event in events)


def response_collections(route: str, url: str, kind: str, body: str, source: dict | None):
    if source and (route != source["resource"] or kind != source["kind"]):
        return
    try:
        if kind == "json":
            payload = json.loads(body)
            if source:
                for key in source["path"]:
                    payload = payload[key]
                if isinstance(payload, (dict, list)) and not payload:
                    yield [], source
                    return
                found = ((source["path"], matches) for path, matches in collections(payload)
                         if not path)
            else:
                found = collections(payload)
        elif kind == "html" and route == url:
            found = inline_collections(body)
        else:
            return
        for path, records in found:
            if not source or list(path) == source["path"]:
                yield records, {"page": url, "resource": route, "kind": kind, "path": list(path)}
    except (ValueError, KeyError, TypeError):
        return


def scan(page, url: str, source: dict | None) -> tuple[list[dict], dict]:
    candidates = []

    def observe(response):
        route = sherpa_route(response.url)
        if not route or not response.ok or (source and route != source["resource"]):
            return
        if response.request.resource_type not in ("document", "fetch", "xhr"):
            return
        content_type = response.headers.get("content-type", "")
        if "json" in content_type:
            candidates.extend(response_collections(route, url, "json", response.text(), source))
        elif "html" in content_type and route == url:
            candidates.extend(response_collections(route, url, "html", response.text(), source))

    page.on("response", observe)
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        # Allow the lesson list's fetches to finish after the browser loads the page.
        for _ in range(12):
            if candidates:
                break
            page.wait_for_timeout(1000)
        if candidates:
            page.wait_for_timeout(1500)
    finally:
        page.remove_listener("response", observe)
    if sherpa_route(page.url) != url:
        raise ValueError("Sherpa redirected away from Your Bookings. Run connect to sign in again.")
    if not candidates:
        raise ValueError("No matching booking data appeared. The last calendar snapshot was kept.")
    return max(candidates, key=lambda entry: len(entry[0]))


def refresh(state: Path, connect: bool) -> int:
    try:
        from playwright.sync_api import Error as PlaywrightError, sync_playwright
    except ImportError:
        raise ValueError("Install the project with its sync extra first. See the README.") from None

    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    source_file = state / "source.json"
    if not connect and not source_file.is_file():
        raise ValueError("Run connect and sign into Sherpa before the first scheduled scan.")
    source = None if connect else json.loads(source_file.read_text())
    try:
        with sync_playwright() as browser:
            profile = browser.chromium.launch_persistent_context(
                str(state / "chrome-profile"),
                channel="chrome",
                headless=not connect,
            )
            try:
                page = profile.pages[0]
                if connect:
                    page.goto(BOOKINGS_URL, wait_until="domcontentloaded", timeout=30_000)
                    input("Sign in to Sherpa, open Your Bookings, then press Return here. ")
                    booking_tabs = [
                        tab for tab in profile.pages
                        if sherpa_route(tab.url) and urlsplit(tab.url).path.rstrip("/").endswith("/bookings")
                    ]
                    if not booking_tabs:
                        raise ValueError("Open Your Bookings in the Sherpa browser before pressing Return.")
                    page = booking_tabs[-1]
                url = sherpa_route(page.url) if connect else source["page"]
                records, selected = scan(page, url, source)
                count = save_bookings(state, records)
                if connect:
                    write_json(source_file, selected)
                return count
            finally:
                profile.close()
    except PlaywrightError:
        raise ValueError("Chrome could not complete the Sherpa scan. Check the connection or rerun connect.") from None
