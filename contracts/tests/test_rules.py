from pathlib import Path
from uuid import UUID, uuid5

import pytest
from pydantic import ValidationError

import rapporteur_contracts
from conftest import VALID, load_example
from rapporteur_contracts import (
    API_ERROR_STATUS,
    CONTRACT_VERSION,
    PUBLIC_MODELS,
    TALK_TRANSITIONS,
    TalkSummaryBundle,
    can_transition,
    source_file_id,
)
from rapporteur_contracts.enums import ApiErrorCode, InferenceEngine, TalkStatus
from rapporteur_contracts.export import bundle_schema


def test_public_models_are_exported() -> None:
    assert set(PUBLIC_MODELS) <= set(rapporteur_contracts.__all__)


def test_version_matches_file() -> None:
    version_file = Path(__file__).parent.parent / "VERSION"
    assert CONTRACT_VERSION == version_file.read_text(encoding="utf-8").strip()


def test_every_status_has_transitions() -> None:
    assert set(TALK_TRANSITIONS) == set(TalkStatus)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (TalkStatus.DRAFT, TalkStatus.INSUFFICIENT_DATA),
        (TalkStatus.INSUFFICIENT_DATA, TalkStatus.MATCHED),
        (TalkStatus.REVIEWED, TalkStatus.PROCESSED),
        (TalkStatus.NOT_HELD, TalkStatus.MATCHED),
    ],
)
def test_amended_transitions_are_allowed(current: TalkStatus, target: TalkStatus) -> None:
    assert can_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (TalkStatus.DRAFT, TalkStatus.REVIEWED),
        (TalkStatus.INSUFFICIENT_DATA, TalkStatus.PROCESSED),
        (TalkStatus.NOT_HELD, TalkStatus.PROCESSED),
        (TalkStatus.STALE, TalkStatus.REVIEWED),
    ],
)
def test_forbidden_transitions_are_rejected(current: TalkStatus, target: TalkStatus) -> None:
    assert not can_transition(current, target)


def test_engine_list_has_no_vllm() -> None:
    assert {engine.value for engine in InferenceEngine} == {"ollama", "stub"}


def test_every_api_error_has_http_status() -> None:
    assert set(API_ERROR_STATUS) == set(ApiErrorCode)


def _bundle_with_text(text: str, span: list[int]) -> dict[str, object]:
    document = load_example(VALID / "TalkSummaryBundle--ok.json")
    document["pack"]["fragments"][0]["text"] = text
    document["summary"]["claims"][0]["evidence"][0]["span"] = span
    return document  # type: ignore[no-any-return]


def test_span_counts_code_points_outside_bmp() -> None:
    text = "𝑥 и 😀"
    assert len(text) == 5
    assert len(text.encode("utf-16-le")) // 2 == 7

    TalkSummaryBundle.model_validate(_bundle_with_text(text, [4, 5]))

    with pytest.raises(ValidationError) as caught:
        TalkSummaryBundle.model_validate(_bundle_with_text(text, [4, 6]))
    assert caught.value.errors()[0]["type"] == "span_out_of_range"


def test_empty_span_is_rejected() -> None:
    with pytest.raises(ValidationError) as caught:
        TalkSummaryBundle.model_validate(_bundle_with_text("текст", [3, 3]))
    assert "empty_span" in {error["type"] for error in caught.value.errors()}


def test_bundle_schema_contains_nested_models() -> None:
    assert {"Claim", "Evidence", "PackedFragment", "StageSpec"} <= set(bundle_schema()["$defs"])


def test_source_file_id_is_stable_per_conference_and_sha() -> None:
    conference = UUID("00000000-0000-4000-8000-000000000001")
    other = UUID("00000000-0000-4000-8000-000000000002")
    sha = "a" * 64
    assert source_file_id(conference, sha) == source_file_id(conference, sha)
    assert source_file_id(conference, sha) != source_file_id(other, sha)
    assert source_file_id(conference, sha) != source_file_id(conference, "b" * 64)
    # Значение закреплено: от него зависят id в БД и в пакетах фрагментов (ADR-013).
    assert source_file_id(conference, sha) == uuid5(conference, f"file:{sha}")


@pytest.mark.parametrize("sha", ["A" * 64, "a" * 63, "not-a-sha"])
def test_source_file_id_rejects_non_canonical_sha(sha: str) -> None:
    with pytest.raises(ValueError, match="sha256"):
        source_file_id(UUID("00000000-0000-4000-8000-000000000001"), sha)
