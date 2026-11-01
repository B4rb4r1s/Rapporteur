"""Генерация JSON Schema из моделей: python -m rapporteur_contracts.export."""

import json
from pathlib import Path
from typing import Any

from pydantic.json_schema import models_json_schema

from rapporteur_contracts import CONTRACT_VERSION, PUBLIC_MODELS

SCHEMA_DIR = Path("schema")
REF_TEMPLATE = "#/$defs/{model}"
DIALECT = "https://json-schema.org/draft/2020-12/schema"


def model_schema(name: str) -> dict[str, Any]:
    schema = PUBLIC_MODELS[name].model_json_schema(ref_template=REF_TEMPLATE)
    return {"$schema": DIALECT, "version": CONTRACT_VERSION, **schema}


def bundle_schema() -> dict[str, Any]:
    pairs = [(model, "validation") for model in PUBLIC_MODELS.values()]
    _, definitions = models_json_schema(pairs, ref_template=REF_TEMPLATE)  # type: ignore[arg-type]
    return {
        "$schema": DIALECT,
        "version": CONTRACT_VERSION,
        "title": "RapporteurContracts",
        "$defs": definitions["$defs"],
    }


def _write(path: Path, schema: dict[str, Any]) -> None:
    text = json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True)
    path.write_text(text + "\n", encoding="utf-8")


def main() -> None:
    SCHEMA_DIR.mkdir(exist_ok=True)
    for stale in SCHEMA_DIR.glob("*.schema.json"):
        stale.unlink()
    for name in PUBLIC_MODELS:
        _write(SCHEMA_DIR / f"{name}.schema.json", model_schema(name))
    _write(SCHEMA_DIR / "all.schema.json", bundle_schema())


if __name__ == "__main__":
    main()
