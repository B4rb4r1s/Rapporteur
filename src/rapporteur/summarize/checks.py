"""Проверки текста сводки кодом: шаг Г spec §6.7 без критика (критик — Ф4).

Каждая проверка возвращает CheckResult; details перечисляет конкретные нарушения, чтобы их
можно было показать модели при перегенерации и человеку в редакторе.
"""

from collections.abc import Iterable, Sequence

from pydantic import BaseModel, ConfigDict, PositiveInt, model_validator
from razdel import sentenize

from rapporteur.contracts import Claim, SummaryDraft, SummaryLine, TalkFragmentPack
from rapporteur.contracts.claims import CheckResult
from rapporteur.contracts.enums import CheckName, ClaimStatus, ClaimType
from rapporteur.summarize.quotes import search_form

# Слова целиком; «*» в конце — любое окончание. Оценочные эпитеты и первое лицо (spec §6.7).
DEFAULT_FORBIDDEN: tuple[str, ...] = (
    "уникальн*",
    "блестящ*",
    "великолепн*",
    "потрясающ*",
    "революционн*",
    "гениальн*",
    "выдающ*",
    "впечатляющ*",
    "замечательн*",
    "прекрасн*",
    "интересн*",
    "несомненн*",
    "безусловн*",
    "мы",
    "нас",
    "нам",
    "нами",
    "наш",
    "наша",
    "наше",
    "наши",
    "нашего",
    "нашей",
    "нашему",
    "нашим",
    "наших",
    "нашими",
    "нашу",
)


class SummaryChecksConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    min_sentences: PositiveInt = 4
    max_sentences: PositiveInt = 5
    forbidden: tuple[str, ...] = DEFAULT_FORBIDDEN

    @model_validator(mode="after")
    def _ordered(self) -> "SummaryChecksConfig":
        if self.min_sentences > self.max_sentences:
            raise ValueError("min_sentences больше max_sentences")
        return self


def usable_claims(claims: Iterable[Claim]) -> list[Claim]:
    """Утверждения, на которые может опираться текст: прошли сверку и не реплики слушателей."""
    return [
        claim
        for claim in claims
        if claim.status is not ClaimStatus.REJECTED and claim.type is not ClaimType.LISTENER_REMARK
    ]


def sentence_range(config: SummaryChecksConfig, claims: Sequence[Claim]) -> tuple[int, int]:
    """Нижняя граница не выше числа утверждений: иначе модели пришлось бы досочинять."""
    available = len(usable_claims(claims))
    return max(1, min(config.min_sentences, available)), config.max_sentences


def check_summary(
    draft: SummaryDraft,
    claims: Sequence[Claim],
    pack: TalkFragmentPack,
    config: SummaryChecksConfig,
    attempt: int,
) -> list[CheckResult]:
    checks = {
        CheckName.CLAIM_LINKS: claim_link_problems(draft, claims),
        CheckName.SENTENCE_COUNT: sentence_count_problems(draft, claims, config),
        CheckName.NUMBERS_IN_SOURCES: number_problems(draft, pack),
        CheckName.FORBIDDEN_PHRASES: forbidden_problems(draft, config.forbidden),
    }
    return [
        CheckResult(
            check=name,
            passed=not problems,
            attempt=attempt,
            details="; ".join(problems) if problems else None,
        )
        for name, problems in checks.items()
    ]


def claim_link_problems(draft: SummaryDraft, claims: Sequence[Claim]) -> list[str]:
    by_key = {claim.key: claim for claim in claims}
    problems: list[str] = []
    for where, lines in (("предложение", draft.sentences), ("вывод", draft.conclusions)):
        for number, line in enumerate(lines, 1):
            problems.extend(
                f"{where} {number}: {problem}" for problem in _line_problems(line, by_key, where)
            )
    has_conclusions = any(claim.type is ClaimType.CONCLUSION for claim in usable_claims(claims))
    if has_conclusions and not draft.conclusions:
        problems.append("у докладчика есть выводы, а блок выводов пуст")
    return problems


def _line_problems(line: SummaryLine, by_key: dict[str, Claim], where: str) -> Iterable[str]:
    for key in line.claim_ids:
        claim = by_key.get(key)
        if claim is None:
            yield f"неизвестное утверждение {key}"
        elif claim.status is ClaimStatus.REJECTED:
            yield f"утверждение {key} не подтверждено цитатой"
        elif claim.type is ClaimType.LISTENER_REMARK:
            yield f"{key} — реплика слушателя, в текст сводки она не входит"
        elif where == "вывод" and claim.type is not ClaimType.CONCLUSION:
            yield f"{key} не вывод докладчика"


def sentence_count_problems(
    draft: SummaryDraft, claims: Sequence[Claim], config: SummaryChecksConfig
) -> list[str]:
    low, high = sentence_range(config, claims)
    count = sum(len(list(sentenize(line.text))) for line in draft.sentences)
    if low <= count <= high:
        return []
    expected = f"{low}–{high}" if low < high else str(low)
    return [f"в тексте {count} предложений, нужно {expected}"]


def number_problems(draft: SummaryDraft, pack: TalkFragmentPack) -> list[str]:
    """Каждое число сводки есть хотя бы в одном фрагменте доклада (форма записи не важна:
    «1 000» и «1000», «94,5» и «94.5» совпадают)."""
    available = {token for fragment in pack.fragments for token in _number_tokens(fragment.text)}
    missing: list[str] = []
    for line in [*draft.sentences, *draft.conclusions]:
        missing.extend(
            token
            for token in _number_tokens(line.text)
            if token not in available and token not in missing
        )
    return [f"числа {', '.join(missing)} нет в материалах доклада"] if missing else []


def _number_tokens(text: str) -> list[str]:
    return [token for token in search_form(text).text.split(" ") if any(c.isdigit() for c in token)]


def forbidden_problems(draft: SummaryDraft, forbidden: Sequence[str]) -> list[str]:
    found: list[str] = []
    for line in [*draft.sentences, *draft.conclusions]:
        for word in search_form(line.text).text.split(" "):
            for pattern in forbidden:
                if _matches(word, pattern) and word not in found:
                    found.append(word)
    return [f"запрещённые слова: {', '.join(found)}"] if found else []


def _matches(word: str, pattern: str) -> bool:
    pattern = pattern.lower().replace("ё", "е")
    if pattern.endswith("*"):
        return word.startswith(pattern[:-1])
    return word == pattern
