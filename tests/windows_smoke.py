import csv
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.request import urlopen
from uuid import uuid4

from keyring.backends.Windows import WinVaultKeyring

from sherpa_ical_automation.storage import write_json
from sherpa_ical_automation.windows import run_system


def main() -> None:
    vault = WinVaultKeyring()
    service = f"Sherpa test {uuid4()}"
    vault.set_password(service, "test-account", "test-only")
    try:
        credential = vault.get_credential(service, None)
        assert credential.username == "test-account" and credential.password == "test-only"
    finally:
        vault.delete_password(service, "test-account")

    system = Path(os.environ["SystemRoot"]) / "System32"
    scheduler = system / "schtasks.exe"
    identity = run_system(system / "whoami.exe", "/user", "/fo", "csv", "/nh").stdout.strip()
    _, sid = next(csv.reader([identity]))
    tasks = [f"Sherpa Calendar {kind} ({sid})" for kind in ("Feed", "Scan")]
    if any(run_system(scheduler, "/Query", "/TN", name, check=False).returncode == 0 for name in tasks):
        raise RuntimeError("Run the smoke test on a Windows account without Sherpa tasks installed.")
    command = [sys.executable, "-m", "sherpa_ical_automation"]
    with TemporaryDirectory(prefix="Sherpa smoke ") as temporary:
        state = Path(temporary) / "Sherpa state"
        seed = Path(temporary) / "sample.json"
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        booking = {
            "id": 42, "status": "upcoming", "when": "2031-07-06 18:00:00", "duration": "1.00",
            "booking_students": [{"user": {"name": "Test Élodie"}}],
        }
        write_json(seed, [booking, {**booking, "id": 43, "status": "pending"}])
        shared = ["--state", str(state), "--port", str(port)]
        try:
            subprocess.run([*command, "prepare-chrome", *shared], check=True, timeout=25)
            subprocess.run([*command, "install-windows", *shared, "--seed", str(seed)], check=True, timeout=35)
            deadline = time.monotonic() + 40
            while True:
                try:
                    with urlopen(f"http://127.0.0.1:{port}/sherpa.ics", timeout=3) as response:
                        data = response.read().decode("utf-8")
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        for path in state.glob("*.log"):
                            print(path.read_text(encoding="utf-8"))
                        raise
                    time.sleep(0.5)
            assert "Test Élodie" in data and "DTSTART:20310706T170000Z" in data
            assert data.count("BEGIN:VEVENT") == 1
            assert "STATUS:CONFIRMED" in data
            subprocess.run([*command, "sync", "--state", str(state), "--include-pending"], check=True)
            with urlopen(f"http://127.0.0.1:{port}/sherpa.ics", timeout=3) as response:
                pending = response.read().decode("utf-8")
            assert pending.count("BEGIN:VEVENT") == 2
            assert "STATUS:TENTATIVE" in pending and "TRANSP:TRANSPARENT" in pending
            for name in tasks:
                run_system(scheduler, "/Query", "/TN", name)
            print("Windows jobs installed and the Unicode calendar feed passed.")
        finally:
            for name in reversed(tasks):
                run_system(scheduler, "/End", "/TN", name, check=False)
                run_system(scheduler, "/Delete", "/TN", name, "/F", check=False)
            # End stops the task asynchronously. Wait for its redirected logs to close.
            deadline = time.monotonic() + 10
            for path in state.glob("*.log"):
                while True:
                    try:
                        path.unlink(missing_ok=True)
                        break
                    except PermissionError:
                        if time.monotonic() >= deadline:
                            raise
                        time.sleep(0.2)


if __name__ == "__main__":
    main()
