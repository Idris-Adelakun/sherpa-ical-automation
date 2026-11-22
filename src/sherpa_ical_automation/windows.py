import csv
import os
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from xml.etree.ElementTree import Element, SubElement, tostring

from .sync import seed_snapshot

TASK_NAMESPACE = "http://schemas.microsoft.com/windows/2004/02/mit/task"


def install_windows(state: Path, seed: Path | None, port: int) -> None:
    if sys.platform != "win32":
        raise ValueError("Windows Task Scheduler is only available on Windows.")
    snapshot = seed_snapshot(state, seed)
    install_task(state, "Feed", [
        "serve", "--source", str(snapshot), "--port", str(port), "--chrome-state", str(state),
    ])
    install_task(state, "Scan", ["sync", "--state", str(state)], interval="PT1H")


def install_task(state: Path, name: str, arguments: list[str], interval: str | None = None) -> None:
    system = Path(os.environ["SystemRoot"]) / "System32"
    identity = run_system(system / "whoami.exe", "/user", "/fo", "csv", "/nh").stdout.strip()
    _, sid = next(csv.reader([identity]))
    scheduler = system / "schtasks.exe"
    task_name = f"Sherpa Calendar {name} ({sid})"
    python = Path(sys.executable).with_name("pythonw.exe")
    if not python.is_file():
        raise ValueError("Install a Windows Python distribution with pythonw.exe and rerun setup.")
    log = state / f"{name.lower()}.log"
    command = ["-m", "sherpa_ical_automation.windows", str(log), *arguments]
    hour = datetime.now().replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    with TemporaryDirectory(prefix="sherpa-task-", dir=state) as temporary:
        task = Path(temporary) / "task.xml"
        task.write_bytes(task_xml(sid, state.parent, python, command, hour, interval))
        run_system(scheduler, "/Create", "/TN", task_name, "/XML", str(task), "/F")
    if interval is None:
        run_system(scheduler, "/End", "/TN", task_name, check=False)
    run_system(scheduler, "/Run", "/TN", task_name)


def task_xml(
    sid: str, directory: Path, python: Path, arguments: list[str],
    next_hour: datetime, interval: str | None,
) -> bytes:
    task = Element("Task", xmlns=TASK_NAMESPACE, version="1.2")
    triggers = SubElement(task, "Triggers")
    if interval is None:
        SubElement(SubElement(triggers, "LogonTrigger"), "UserId").text = sid
    else:
        trigger = SubElement(triggers, "TimeTrigger")
        SubElement(trigger, "StartBoundary").text = next_hour.isoformat(timespec="seconds")
        SubElement(SubElement(trigger, "Repetition"), "Interval").text = interval

    principal = SubElement(SubElement(task, "Principals"), "Principal", id="current-user")
    SubElement(principal, "UserId").text = sid
    SubElement(principal, "LogonType").text = "InteractiveToken"
    SubElement(principal, "RunLevel").text = "LeastPrivilege"

    settings = SubElement(task, "Settings")
    SubElement(settings, "MultipleInstancesPolicy").text = "IgnoreNew"
    SubElement(settings, "DisallowStartIfOnBatteries").text = "false"
    SubElement(settings, "StopIfGoingOnBatteries").text = "false"
    SubElement(settings, "StartWhenAvailable").text = "true"
    SubElement(settings, "ExecutionTimeLimit").text = "PT0S" if interval is None else "PT10M"
    if interval is None:
        restart = SubElement(settings, "RestartOnFailure")
        SubElement(restart, "Interval").text = "PT1M"
        SubElement(restart, "Count").text = "3"

    action = SubElement(SubElement(task, "Actions", Context="current-user"), "Exec")
    SubElement(action, "Command").text = str(python)
    SubElement(action, "Arguments").text = subprocess.list2cmdline(arguments)
    SubElement(action, "WorkingDirectory").text = str(directory)
    return tostring(task, encoding="utf-16", xml_declaration=True)


def run_system(executable: Path, *arguments: str, check: bool = True):
    result = subprocess.run(
        [str(executable), *arguments], capture_output=True, text=True, errors="replace", check=False,
    )
    if check and result.returncode:
        raise ValueError(result.stderr.strip() or result.stdout.strip() or f"{executable.name} failed")
    return result


if __name__ == "__main__":
    from .cli import main

    log_path = Path(sys.argv.pop(1))
    with log_path.open("a", encoding="utf-8", buffering=1) as log:
        with redirect_stdout(log), redirect_stderr(log):
            main()
