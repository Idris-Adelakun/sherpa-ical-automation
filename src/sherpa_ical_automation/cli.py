import argparse
import json
import sys
from collections.abc import Callable
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .calendar import read_snapshot, render_calendar
from .chrome_bridge import (
    SCAN_PATH,
    authorized,
    chrome_config,
    pending_scan,
    prepare_extension,
    queue_scan,
    receive_scan,
)
from .icloud import install_icloud, publish_icloud
from .macos import install_agents
from .storage import write_atomic
from .sync import PENDING_PREFERENCE, STATE, refresh
from .windows import install_windows
from .windows_icloud import publish_windows_icloud

FEED_PATH = "/sherpa.ics"


def main() -> None:
    parser = argparse.ArgumentParser(description="Export a Sherpa Online bookings snapshot.")
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export", help="Write an importable .ics file")
    serve = commands.add_parser("serve", help="Serve a refreshing local .ics feed")
    connect = commands.add_parser("connect", help="Sign in and scan Your Bookings in Chrome")
    sync = commands.add_parser("sync", help="Scan Sherpa using the connected Chrome profile")
    chrome = commands.add_parser("prepare-chrome", help="Prepare the extension for your regular Chrome profile")
    mac = commands.add_parser("install-mac", help="Install the local feed and hourly scan")
    windows = commands.add_parser("install-windows", help="Install the feed and hourly scan in Windows Task Scheduler")
    icloud = commands.add_parser("setup-icloud", help="Set up a private iCloud calendar")
    icloud_sync = commands.add_parser("sync-icloud", help="Update the dedicated iCloud calendar now")
    for command in (connect, sync, chrome, mac, windows, icloud, icloud_sync):
        command.add_argument("--state", type=Path, default=STATE, help="Private local state directory")
    pending = sync.add_mutually_exclusive_group()
    pending.add_argument(
        "--include-pending", dest="include_pending", action="store_true",
        help="Keep pending requests visible in the local subscription",
    )
    pending.add_argument(
        "--exclude-pending", dest="include_pending", action="store_false",
        help="Show only confirmed bookings in the local subscription",
    )
    sync.set_defaults(include_pending=None)
    for command in (mac, windows):
        command.add_argument("--seed", type=Path, help="Initial bookings JSON (uses PII.json if available)")
        command.add_argument("--port", type=int, default=8765, help="Local feed port (default: 8765)")
    chrome.add_argument("--port", type=int, default=8765, help="Local feed port (default: 8765)")
    chrome.add_argument("--bookings-url", help="The signed-in Chrome profile's Sherpa bookings URL")
    for command in (export, serve):
        command.add_argument(
            "--source", type=Path, default=Path("PII.json"),
            help="Sherpa bookings JSON (default: PII.json)",
        )
        command.add_argument(
            "--source-timezone", default="Europe/London",
            help="Time zone of timestamps without offsets (default: Europe/London)",
        )
        command.add_argument(
            "--include-pending", action="store_true",
            help="Include pending requests as tentative, free events",
        )
        names = command.add_mutually_exclusive_group()
        names.add_argument(
            "--hide-student-names", dest="include_student_names", action="store_false",
            help="Use Sherpa lesson in place of student names",
        )
        names.add_argument(
            "--include-student-names", dest="include_student_names", action="store_true",
            help=argparse.SUPPRESS,
        )
        command.set_defaults(include_student_names=True)
    export.add_argument(
        "--output", type=Path, default=Path("sherpa.ics"),
        help="Path to write (default: sherpa.ics)",
    )
    serve.add_argument("--port", type=int, default=8765, help="Local port (default: 8765)")
    serve.add_argument("--chrome-state", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()

    if hasattr(args, "state"):
        try:
            state = args.state.resolve()
            if args.command == "sync" and args.include_pending is not None:
                state.mkdir(mode=0o700, parents=True, exist_ok=True)
                preference = state / PENDING_PREFERENCE
                if args.include_pending:
                    write_atomic(preference, b"")
                    print("Pending requests will appear as tentative events in the local feed.")
                else:
                    preference.unlink(missing_ok=True)
                    print("The local feed will show confirmed bookings only.")
            if hasattr(args, "port") and not 1 <= args.port <= 65535:
                raise ValueError("Port must be between 1 and 65535")
            if args.command in ("setup-icloud", "sync-icloud"):
                if sys.platform == "win32":
                    result = publish_windows_icloud(state, setup=args.command == "setup-icloud")
                elif args.command == "setup-icloud":
                    result = install_icloud(state)
                else:
                    result = publish_icloud(state)
                if result:
                    print(
                        f"iCloud calendar: {result['added']} added, {result['updated']} updated, "
                        f"{result['removed']} removed."
                    )
            elif args.command in ("install-mac", "install-windows"):
                installer = install_agents if args.command == "install-mac" else install_windows
                installer(state, args.seed, args.port)
                print(f"Sherpa feed installed at http://127.0.0.1:{args.port}{FEED_PATH}")
                print("Scheduled Sherpa scans at the top of every hour in your local time.")
            elif args.command == "prepare-chrome":
                directory = prepare_extension(state, args.port, args.bookings_url)
                print(f"Load this unpacked extension in the signed-in Chrome profile: {directory}")
            elif args.command == "sync" and chrome_config(state).is_file():
                queue_scan(state)
                print("Queued a Sherpa scan for the signed-in Chrome profile.")
            else:
                count = refresh(state, connect=args.command == "connect")
                print(f"Refreshed {count} confirmed Sherpa booking(s).")
        except (OSError, ValueError, EOFError) as error:
            parser.exit(1, f"Error: {error}\n")
        except KeyboardInterrupt:
            parser.exit(130, "\nSherpa connection cancelled.\n")
        return

    try:
        zone = ZoneInfo(args.source_timezone)
    except (ValueError, ZoneInfoNotFoundError):
        parser.error(f"Unknown source time zone: {args.source_timezone}")

    pending_preference = (
        args.chrome_state / PENDING_PREFERENCE
        if args.command == "serve" and args.chrome_state is not None
        else None
    )

    def load_calendar() -> tuple[bytes, int]:
        include_pending = args.include_pending or (
            pending_preference is not None and pending_preference.is_file()
        )
        events, revision = read_snapshot(
            args.source, zone, include_pending, args.include_student_names
        )
        return render_calendar(events, revision), len(events)

    try:
        if args.command == "export":
            if args.source.resolve() == args.output.resolve():
                raise ValueError("The calendar output path must differ from the source path")
            content, count = load_calendar()
            write_atomic(args.output, content)
            print(f"Exported {count} Sherpa booking(s) to {args.output}")
        else:
            if not 1 <= args.port <= 65535:
                raise ValueError("Port must be between 1 and 65535")
            load_calendar()
            serve_calendar(args.port, load_calendar, args.chrome_state)
    except (OSError, ValueError) as error:
        parser.exit(1, f"Error: {error}\n")
    except KeyboardInterrupt:
        print("\nStopped the Sherpa calendar feed.", file=sys.stderr)


def serve_calendar(
    port: int, load_calendar: Callable[[], tuple[bytes, int]], chrome_state: Path | None
) -> None:
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    if port == 80:
        allowed_hosts.update(("127.0.0.1", "localhost"))

    class CalendarHandler(BaseHTTPRequestHandler):
        def do_HEAD(self) -> None:
            self.send_calendar(include_body=False)

        def do_GET(self) -> None:
            if self.path == SCAN_PATH and chrome_state:
                self.handle_scan()
            else:
                self.send_calendar(include_body=True)

        def do_POST(self) -> None:
            self.handle_scan()

        def do_PUT(self) -> None:
            self.handle_scan()

        def handle_scan(self) -> None:
            if self.path != SCAN_PATH or chrome_state is None:
                self.send_error(404)
                return
            if self.headers.get("Host") not in allowed_hosts:
                self.send_error(403)
                return
            try:
                if not authorized(chrome_state, self.headers.get("Authorization", "")):
                    self.send_json(403, {"error": "Chrome extension authorization failed"})
                    return
                if self.command == "PUT":
                    queue_scan(chrome_state)
                if self.command != "POST":
                    self.send_json(200, {"id": pending_scan(chrome_state)})
                    return
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 2_000_000:
                    self.send_json(413, {"error": "The Chrome scan was empty or too large"})
                    return
                self.connection.settimeout(5)
                report = json.loads(self.rfile.read(size))
                count = receive_scan(chrome_state, report)
                self.send_json(200, {"count": count})
            except ValueError as error:
                self.send_json(422, {"error": str(error)})
            except OSError:
                self.send_json(503, {"error": "The local scan files could not be read or written"})

        def send_json(self, status: int, payload: dict) -> None:
            content = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(content)

        def send_calendar(self, include_body: bool) -> None:
            if self.headers.get("Host") not in allowed_hosts:
                self.send_error(403)
                return
            if self.path != FEED_PATH:
                self.send_error(404)
                return
            try:
                content, _ = load_calendar()
            except (OSError, ValueError) as error:
                self.log_error("Could not read Sherpa snapshot: %s", error)
                self.send_error(503, "Sherpa snapshot unavailable")
                return

            etag = f'"{sha256(content).hexdigest()}"'
            validators = self.headers.get("If-None-Match", "").split(",")
            unchanged = any(
                value.strip().removeprefix("W/") in (etag, "*") for value in validators
            )
            self.send_response(304 if unchanged else 200)
            self.send_header("Cache-Control", "private, no-cache")
            self.send_header("ETag", etag)
            if not unchanged:
                self.send_header("Content-Type", "text/calendar; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            if not unchanged and include_body:
                self.wfile.write(content)

        def log_request(self, code="-", size="-") -> None:
            pass

    with ThreadingHTTPServer(("127.0.0.1", port), CalendarHandler) as server:
        print(f"Sherpa calendar feed: http://127.0.0.1:{port}{FEED_PATH}", flush=True)
        server.serve_forever()
