"""Оценка начальных привязок по снимку экрана материалов (P1-05)."""

import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import yaml
from pydantic import BaseModel, ConfigDict, Field, PositiveInt, ValidationError

from rapporteur_backend.materials import Materials
from rapporteur_contracts.base import NonEmptyStr


class TalkLabel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section: NonEmptyStr
    order: PositiveInt


class ExpectedBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: NonEmptyStr
    talk: TalkLabel | None


class BindingReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    files: Annotated[list[ExpectedBinding], Field(min_length=1)]


@dataclass(frozen=True)
class BindingMetrics:
    files: int
    expected_links: int
    offered_links: int
    correct_links: int
    correct_defaults: int


def score_bindings(snapshot: Materials, reference: BindingReference) -> BindingMetrics:
    expected = {item.path: item.talk for item in reference.files}
    files = {item.relative_path: item for item in snapshot.files}
    if len(expected) != len(reference.files) or len(files) != len(snapshot.files):
        raise ValueError("В снимке или разметке повторяются пути материалов.")
    if expected.keys() != files.keys():
        raise ValueError(
            "Пути в снимке и разметке расходятся. "
            f"Без разметки: {len(files.keys() - expected.keys())}. "
            f"Нет в снимке: {len(expected.keys() - files.keys())}."
        )
    if any(item.binding.confirmed for item in files.values()):
        raise ValueError("Для замера нужен снимок до первого подтверждения привязок.")

    talks = {talk.id: TalkLabel(section=talk.section, order=talk.order) for talk in snapshot.talks}
    labels = {(talk.section, talk.order) for talk in talks.values()}
    if len(talks) != len(snapshot.talks) or len(labels) != len(talks):
        raise ValueError("В снимке есть доклады с одинаковым ID или номером внутри секции.")

    offered_links = correct_links = correct_defaults = 0
    for path, item in files.items():
        suggested_id = item.suggested_talk_id
        if suggested_id is not None and suggested_id not in talks:
            raise ValueError("Подсказка ссылается на доклад, которого нет в снимке.")
        # В интерфейсе дополнительная версия файла по умолчанию остаётся без привязки.
        offered = (
            talks[suggested_id] if suggested_id is not None and not item.alternate_of else None
        )
        correct = offered == expected[path]
        offered_links += int(offered is not None)
        correct_links += int(correct and offered is not None)
        correct_defaults += int(correct)
    return BindingMetrics(
        files=len(files),
        expected_links=sum(talk is not None for talk in expected.values()),
        offered_links=offered_links,
        correct_links=correct_links,
        correct_defaults=correct_defaults,
    )


def _rate(count: int, total: int) -> str:
    return f"{count}/{total} ({count / total:.1%})" if total else "н/д (нет случаев)"


def benchmark_bindings(
    snapshot_path: Path, reference_path: Path, review_seconds: float | None
) -> int:
    try:
        if review_seconds is not None and (
            not math.isfinite(review_seconds) or review_seconds <= 0
        ):
            raise ValueError("Время ручной проверки должно быть конечным числом больше нуля.")
        snapshot = Materials.model_validate_json(snapshot_path.read_bytes())
        reference = BindingReference.model_validate(
            yaml.safe_load(reference_path.read_text(encoding="utf-8"))
        )
        metrics = score_bindings(snapshot, reference)
    except ValidationError as error:
        fields = ", ".join(
            ".".join(map(str, entry["loc"])) for entry in error.errors(include_input=False)
        )
        print(f"Не удалось провести замер. Проверьте поля: {fields}.", file=sys.stderr)
        return 1
    except (OSError, ValueError, yaml.YAMLError) as error:
        print(f"Не удалось провести замер. {error}", file=sys.stderr)
        return 1

    print(f"Материалов в разметке: {metrics.files}")
    precision = _rate(metrics.correct_links, metrics.offered_links)
    print(f"Верные подсказки среди предложенных: {precision}")
    print(f"Найденные привязки по разметке: {_rate(metrics.correct_links, metrics.expected_links)}")
    print(f"Верные начальные решения: {_rate(metrics.correct_defaults, metrics.files)}")
    seconds = f"{review_seconds:.1f} с" if review_seconds is not None else "н/д"
    print(f"Время ручной проверки: {seconds}")
    return 0
