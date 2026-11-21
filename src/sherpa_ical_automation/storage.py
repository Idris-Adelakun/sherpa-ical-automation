import json
from pathlib import Path
from tempfile import NamedTemporaryFile


def write_atomic(output: Path, content: bytes) -> None:
    file = NamedTemporaryFile(dir=output.parent, delete=False)
    temporary_path = Path(file.name)
    try:
        with file:
            file.write(content)
        temporary_path.replace(output)
    finally:
        temporary_path.unlink(missing_ok=True)


def write_json(output: Path, payload: object) -> None:
    write_atomic(output, (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
