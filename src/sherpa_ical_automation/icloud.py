import json
import platform
import plistlib
import subprocess
import sys
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory

from .calendar import read_snapshot
from .macos import install_agent
from .storage import write_atomic, write_json
from .sync import PENDING_PREFERENCE, ZONE

APP_NAME = "Sherpa iCloud Sync"
APP_EXECUTABLE = "SherpaICloudSync"


def install_icloud(state: Path) -> dict:
    result = publish_icloud(state, setup=True)
    install_agent(state, "icloud", {
        "ProgramArguments": [
            sys.executable, "-m", "sherpa_ical_automation", "sync-icloud", "--state", str(state),
        ],
        "RunAtLoad": True,
        "StartInterval": 60,
    })
    return result


def publish_icloud(state: Path, *, setup: bool = False) -> dict | None:
    if sys.platform != "darwin" or int(platform.mac_ver()[0].split(".")[0]) < 14:
        raise ValueError("iCloud calendar sync requires macOS 14 or later.")

    import fcntl

    target = state / "icloud-calendar.json"
    if not setup and not target.is_file():
        raise ValueError("Run setup-icloud to create the dedicated iCloud calendar.")
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (state / "icloud.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            if setup:
                raise ValueError("An iCloud sync is already in progress.") from None
            return None
        return publish_locked(state, target, setup)


def publish_locked(state: Path, target: Path, setup: bool) -> dict | None:
    include_pending = (state / PENDING_PREFERENCE).is_file()
    events, _ = read_snapshot(state / "bookings.json", ZONE, include_pending, True)
    lessons = [
        {
            "uid": event.uid,
            "summary": event.summary,
            "start": event.start.timestamp(),
            "end": event.end.timestamp(),
            "tentative": event.tentative,
        }
        for event in events
    ]
    digest = sha256(json.dumps(lessons, sort_keys=True).encode("utf-8")).hexdigest()
    last_digest = state / "icloud-last-sync"
    if not setup and last_digest.is_file() and last_digest.read_text() == digest:
        return None

    app = state / f"{APP_NAME}.app"
    if setup and not app.is_dir():
        build_helper(app)
    if not app.is_dir():
        raise ValueError("The iCloud sync helper is missing. Run setup-icloud again.")

    with TemporaryDirectory(prefix="icloud-", dir=state) as temporary:
        folder = Path(temporary)
        request = folder / "request.json"
        response = folder / "response.json"
        write_json(request, {"setup": setup, "lessons": lessons})
        command = ["/usr/bin/open", "-W", "-n"]
        if not setup:
            command.append("-g")
        command.extend([str(app), "--args", str(request), str(response), str(target)])
        timeout = 180 if setup else 45
        try:
            result = subprocess.run(command, capture_output=True, check=False, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise ValueError("Timed out waiting for the iCloud Calendar helper.") from None
        if result.returncode or not response.is_file():
            raise ValueError("The iCloud Calendar helper could not complete its request.")
        report = json.loads(response.read_bytes())
        if report.get("error"):
            raise ValueError(report["error"])
    write_atomic(last_digest, digest.encode("ascii"))
    return report


def build_helper(app: Path) -> None:
    with TemporaryDirectory(prefix="icloud-build-", dir=app.parent) as temporary:
        bundle = Path(temporary) / app.name
        contents = bundle / "Contents"
        macos = contents / "MacOS"
        macos.mkdir(parents=True)
        description = (
            "Sherpa iCloud Sync needs Calendar access to add, reschedule, and remove its "
            "own lesson events in your dedicated Sherpa iCloud calendar."
        )
        metadata = {
            "CFBundleIdentifier": "local.sherpa-ical-automation.icloud",
            "CFBundleName": APP_NAME,
            "CFBundleDisplayName": APP_NAME,
            "CFBundleExecutable": APP_EXECUTABLE,
            "CFBundleInfoDictionaryVersion": "6.0",
            "CFBundlePackageType": "APPL",
            "CFBundleVersion": "1",
            "LSMinimumSystemVersion": "14.0",
            "LSUIElement": True,
            "NSCalendarsFullAccessUsageDescription": description,
        }
        write_atomic(contents / "Info.plist", plistlib.dumps(metadata))
        commands = [
            [
                "/usr/bin/swiftc", "-parse-as-library",
                "-target", f"{platform.machine()}-apple-macosx14.0",
                "-module-cache-path", str(Path(temporary) / "swift-cache"),
                str(Path(__file__).with_name("icloud_sync.swift")), "-o", str(macos / APP_EXECUTABLE),
            ],
            ["/usr/bin/codesign", "--sign", "-", str(bundle)],
        ]
        for command in commands:
            completed = subprocess.run(command, capture_output=True, text=True, check=False)
            if completed.returncode:
                raise ValueError(completed.stderr.strip() or f"{Path(command[0]).name} failed")
        bundle.rename(app)
