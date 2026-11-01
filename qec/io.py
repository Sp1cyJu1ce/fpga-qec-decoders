"""Small helpers shared by the experiment commands."""

from pathlib import Path
import hashlib
import json
import math
import platform
import importlib.metadata


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    def finite(obj):
        if isinstance(obj, float) and not math.isfinite(obj):
            return None
        if isinstance(obj, dict):
            return {key: finite(item) for key, item in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [finite(item) for item in obj]
        return obj

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(finite(value), indent=2, allow_nan=False) + "\n", encoding="utf8"
    )
    temporary.replace(path)


def protocol(output, settings):
    root = Path(__file__).resolve().parents[1]
    sources = (
        list((root / "qec").glob("*.py"))
        + list((root / "models").rglob("*.npz"))
        + list((root / "models/2d").glob("*.hex"))
    )
    data = {
        "settings": settings,
        "source_sha256": {
            p.relative_to(root).as_posix(): sha(p) for p in sorted(sources)
        },
        "software": {
            "python": platform.python_version(),
            **{
                name: importlib.metadata.version(name)
                for name in ["numpy", "scipy", "torch", "stim", "pymatching"]
            },
        },
    }
    path = Path(output) / "protocol.json"
    if path.exists():
        if json.loads(path.read_text(encoding="utf8")) != data:
            raise ValueError(
                "Output folder belongs to a different configuration or source. Choose an empty output folder."
            )
    else:
        write(path, data)
    return data
