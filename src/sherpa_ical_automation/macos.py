import os
import plistlib
import subprocess
import sys
from pathlib import Path

from .storage import write_atomic
from .sync import seed_snapshot

LABEL = "local.sherpa-ical-automation"


def install_agents(state: Path, seed: Path | None, port: int) -> None:
    if sys.platform != "darwin":
        raise ValueError("The background jobs require macOS.")

    state = state.resolve()
    snapshot = seed_snapshot(state, seed)

    command = [sys.executable, "-m", "sherpa_ical_automation"]
    jobs = {
        "feed": {
            "ProgramArguments": [
                *command, "serve", "--source", str(snapshot), "--port", str(port),
                "--chrome-state", str(state),
            ],
            "KeepAlive": True,
        },
        "scan": {
            "ProgramArguments": [*command, "sync", "--state", str(state)],
            "StartCalendarInterval": {"Minute": 0},
        },
    }
    for name, job in jobs.items():
        install_agent(state, name, job)


def install_agent(state: Path, name: str, job: dict) -> None:
    launch_agents = Path.home() / "Library" / "LaunchAgents"
    launch_agents.mkdir(exist_ok=True)
    domain = f"gui/{os.getuid()}"
    src = Path(__file__).resolve().parent.parent
    label = f"{LABEL}.{name}"
    service = f"{domain}/{label}"
    path = launch_agents / f"{label}.plist"
    config = {
        "Label": label,
        "WorkingDirectory": str(state.parent),
        "EnvironmentVariables": {"PYTHONPATH": str(src)},
        "StandardOutPath": str(state / f"{name}.log"),
        "StandardErrorPath": str(state / f"{name}.log"),
        **job,
    }
    status = subprocess.run(
        ["/bin/launchctl", "print", service], capture_output=True, check=False
    )
    if status.returncode == 0:
        launchctl("bootout", service)
    write_atomic(path, plistlib.dumps(config))
    launchctl("bootstrap", domain, str(path))


def launchctl(*arguments: str) -> None:
    result = subprocess.run(
        ["/bin/launchctl", *arguments], capture_output=True, check=False, text=True
    )
    if result.returncode:
        raise ValueError(result.stderr.strip() or f"launchctl {arguments[0]} failed")
