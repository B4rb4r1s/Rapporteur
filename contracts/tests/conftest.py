import json
from pathlib import Path
from typing import Any

import yaml

EXAMPLES = Path(__file__).parent.parent / "examples"
VALID = EXAMPLES / "valid"


def load_example(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    return yaml.safe_load(text) if path.suffix == ".yaml" else json.loads(text)


def valid_examples() -> list[Path]:
    return sorted(path for path in VALID.iterdir() if path.suffix in {".json", ".yaml"})


def model_name(path: Path) -> str:
    return path.name.split("--", 1)[0]
