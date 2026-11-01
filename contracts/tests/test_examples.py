import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest
import yaml
from pydantic import ValidationError

from conftest import EXAMPLES, VALID, load_example, model_name, valid_examples
from rapporteur_contracts import PUBLIC_MODELS

SCHEMA_DIR = EXAMPLES.parent / "schema"
INVALID_CASES: list[dict[str, Any]] = yaml.safe_load(
    (EXAMPLES / "invalid.yaml").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("path", valid_examples(), ids=lambda path: path.name)
def test_valid_example_passes_model(path: Path) -> None:
    PUBLIC_MODELS[model_name(path)].model_validate(load_example(path))


@pytest.mark.parametrize("path", valid_examples(), ids=lambda path: path.name)
def test_valid_example_passes_json_schema(path: Path) -> None:
    schema = json.loads(
        (SCHEMA_DIR / f"{model_name(path)}.schema.json").read_text(encoding="utf-8")
    )
    jsonschema.Draft202012Validator(schema).validate(load_example(path))


def test_every_public_model_has_an_example() -> None:
    assert {model_name(path) for path in valid_examples()} == set(PUBLIC_MODELS)


def _apply(document: Any, dotted_path: str, value: Any) -> None:
    *parents, last = [int(part) if part.isdigit() else part for part in dotted_path.split(".")]
    target = document
    for part in parents:
        target = target[part]
    target[last] = value


@pytest.mark.parametrize("case", INVALID_CASES, ids=lambda case: case["name"])
def test_invalid_case_is_rejected(case: dict[str, Any]) -> None:
    document = load_example(VALID / case["base"])
    for path, value in case["set"].items():
        _apply(document, path, value)

    with pytest.raises(ValidationError) as caught:
        PUBLIC_MODELS[case["model"]].model_validate(document)

    assert case["expect"] in {error["type"] for error in caught.value.errors()}
