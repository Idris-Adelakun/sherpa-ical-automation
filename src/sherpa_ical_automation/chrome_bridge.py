import json
import re
from pathlib import Path
from secrets import compare_digest, token_urlsafe
from urllib.parse import urlsplit
from uuid import uuid4

from .storage import write_atomic, write_json
from .sync import BOOKINGS_URL, response_collections, save_bookings, sherpa_route

SCAN_PATH = "/chrome-scan"


def chrome_config(state: Path) -> Path:
    return state / "chrome-extension" / "config.json"


def prepare_extension(state: Path, port: int, bookings_url: str | None = None) -> Path:
    config = chrome_config(state)
    previous = json.loads(config.read_text()) if config.is_file() else {}
    start_url = sherpa_route(bookings_url or previous.get("bookings_url", BOOKINGS_URL))
    if not is_bookings_page(start_url):
        raise ValueError("Enter the HTTPS URL of your Sherpa bookings page")
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    config.parent.mkdir(mode=0o700, exist_ok=True)
    token = previous.get("token") or token_urlsafe(32)
    template = Path(__file__).with_name("chrome_extension")
    for file in template.iterdir():
        if file.suffix in (".js", ".json"):
            write_atomic(config.parent / file.name, file.read_bytes())
    write_json(config, {
        "token": token, "url": f"http://127.0.0.1:{port}{SCAN_PATH}", "bookings_url": start_url,
    })
    queue_scan(state)
    return config.parent


def queue_scan(state: Path) -> None:
    write_json(state / "chrome-request.json", {"id": uuid4().hex})


def authorized(state: Path, header: str) -> bool:
    config = chrome_config(state)
    if not config.is_file() or not header.isascii():
        return False
    token = json.loads(config.read_text())["token"]
    return compare_digest(header, f"Bearer {token}")


def pending_scan(state: Path) -> str | None:
    request = state / "chrome-request.json"
    if not request.is_file():
        return None
    request_id = json.loads(request.read_text())["id"]
    result = state / "chrome-result.json"
    if result.is_file() and json.loads(result.read_text())["id"] == request_id:
        return None
    return request_id


def is_bookings_page(route: str) -> bool:
    return "bookings" in urlsplit(route).path.lower()


def receive_scan(state: Path, report: object) -> int:
    if not isinstance(report, dict) or not isinstance(report.get("id"), str):
        raise ValueError("Invalid Chrome scan report")
    if report["id"] != pending_scan(state):
        raise ValueError("This scan request is no longer pending")

    observed_url = report.get("page")
    page = sherpa_route(observed_url) if isinstance(observed_url, str) else ""
    responses = report.get("responses")
    result = {"id": report["id"], "count": None}
    try:
        if not page:
            raise ValueError("Chrome did not report a Sherpa page. Check the extension's Sherpa site access.")
        if not is_bookings_page(page):
            parts = urlsplit(page)
            segments = parts.path.strip("/").split("/")[:4]
            diagnostic_path = "/" + "/".join(
                segment if re.fullmatch(r"[a-zA-Z_-]{1,32}", segment) else "…"
                for segment in segments if segment
            )
            raise ValueError(
                f"Sherpa opened {parts.hostname}{diagnostic_path}. "
                "Open Your Bookings in the signed-in profile."
            )
        if not isinstance(responses, list):
            raise ValueError("Invalid Chrome scan responses")
        source_path = state / "chrome-source.json"
        source = json.loads(source_path.read_text()) if source_path.is_file() else None
        if source and source["page"] != page:
            source = None
        candidates = []
        for response in responses:
            if not isinstance(response, dict):
                continue
            route = response.get("route")
            body = response.get("body")
            kind = response.get("kind")
            if isinstance(route, str) and isinstance(body, str) and sherpa_route(route):
                candidates.extend(response_collections(route, page, kind, body, source))
        if not candidates:
            raise ValueError("Sherpa returned no recognised bookings data. The previous calendar was kept.")
        records, selected = max(candidates, key=lambda candidate: len(candidate[0]))
        result["count"] = save_bookings(state, records)
        write_json(source_path, selected)
        config = chrome_config(state)
        settings = json.loads(config.read_text())
        if settings.get("bookings_url") != page:
            settings["bookings_url"] = page
            write_json(config, settings)
    except ValueError as error:
        result["error"] = str(error)
        write_json(state / "chrome-result.json", result)
        raise
    write_json(state / "chrome-result.json", result)
    return result["count"]
