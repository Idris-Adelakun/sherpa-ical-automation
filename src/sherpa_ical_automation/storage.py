import json
from pathlib import Path
from tempfile import NamedTemporaryFile
from time import sleep


def write_atomic(output: Path, content: bytes) -> None:
    file = NamedTemporaryFile(dir=output.parent, delete=False)
    temporary_path = Path(file.name)
    try:
        with file:
            file.write(content)
        for attempt in range(5):
            try:
                temporary_path.replace(output)
                break
            except PermissionError as error:
                if getattr(error, "winerror", None) not in (5, 32, 33) or attempt == 4:
                    raise
                sleep(0.05 * (attempt + 1))
    finally:
        temporary_path.unlink(missing_ok=True)


def write_json(output: Path, payload: object) -> None:
    write_atomic(output, (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
