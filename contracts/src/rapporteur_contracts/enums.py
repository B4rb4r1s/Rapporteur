"""Перечисления и допустимые переходы статусов (spec §7.2, ADR-008)."""

from collections.abc import Mapping
from enum import StrEnum


class TalkStatus(StrEnum):
    DRAFT = "draft"
    MATCHED = "matched"
    PROCESSED = "processed"
    REVIEWED = "reviewed"
    STALE = "stale"
    NOT_HELD = "not_held"
    INSUFFICIENT_DATA = "insufficient_data"


TALK_TRANSITIONS: Mapping[TalkStatus, frozenset[TalkStatus]] = {
    TalkStatus.DRAFT: frozenset({TalkStatus.MATCHED, TalkStatus.INSUFFICIENT_DATA}),
    TalkStatus.INSUFFICIENT_DATA: frozenset({TalkStatus.MATCHED}),
    TalkStatus.MATCHED: frozenset({TalkStatus.PROCESSED, TalkStatus.NOT_HELD}),
    TalkStatus.PROCESSED: frozenset({TalkStatus.REVIEWED, TalkStatus.STALE}),
    TalkStatus.REVIEWED: frozenset({TalkStatus.PROCESSED, TalkStatus.STALE}),
    TalkStatus.STALE: frozenset({TalkStatus.PROCESSED}),
    TalkStatus.NOT_HELD: frozenset({TalkStatus.MATCHED}),
}

# Доклады с этими статусами могут входить в справку и счётчики (spec §6.9).
REPORTABLE_TALK_STATUSES = frozenset({TalkStatus.PROCESSED, TalkStatus.REVIEWED, TalkStatus.STALE})


def can_transition(current: TalkStatus, target: TalkStatus) -> bool:
    return target in TALK_TRANSITIONS[current]


class SummaryStatus(StrEnum):
    READY = "ready"
    NEEDS_ATTENTION = "needs_attention"
    STALE = "stale"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RETRY_WAIT = "retry_wait"
    CANCEL_REQUESTED = "cancel_requested"


class FileKind(StrEnum):
    PRESENTATION = "presentation"
    AUDIO = "audio"
    VIDEO = "video"
    NOTES = "notes"
    PROGRAM = "program"


class SegmentKind(StrEnum):
    TALK = "talk"
    QA = "qa"
    SERVICE = "service"


class FragmentKind(StrEnum):
    SLIDE = "slide"
    TRANSCRIPT = "transcript"
    NOTE = "note"
    PROGRAM = "program"
    SLIDE_DESCRIPTION = "slide_description"


class TextOrigin(StrEnum):
    EXTRACTED = "extracted"
    ASR = "asr"
    VLM = "vlm"
    TERM_FIX = "term_fix"
    HUMAN = "human"


class PackBlock(StrEnum):
    MAIN = "main"
    QA = "qa"


class PersonRole(StrEnum):
    SPEAKER = "speaker"
    COAUTHOR = "coauthor"


class TalkType(StrEnum):
    RESEARCH = "исследовательский"
    REVIEW = "обзорный"
    APPLIED = "прикладной"
    OTHER = "иное"


class ClaimType(StrEnum):
    PROBLEM = "problem"
    METHOD = "method"
    RESULT = "result"
    CONCLUSION = "conclusion"
    LISTENER_REMARK = "listener_remark"


class ClaimStatus(StrEnum):
    """rejected — цитата не прошла сверку кодом; verified — прошла, человек ещё не смотрел;
    needs_human — нужна проверка человеком: текст правили или опора только на текст VLM;
    confirmed — подтверждено человеком."""

    REJECTED = "rejected"
    VERIFIED = "verified"
    NEEDS_HUMAN = "needs_human"
    CONFIRMED = "confirmed"


class EvidenceRejection(StrEnum):
    LABEL_NOT_FOUND = "label_not_found"
    QUOTE_NOT_FOUND = "quote_not_found"
    BELOW_THRESHOLD = "below_threshold"
    NUMBER_MISMATCH = "number_mismatch"
    NEGATION_MISMATCH = "negation_mismatch"
    TOO_SHORT = "too_short"


class CheckName(StrEnum):
    CLAIM_LINKS = "claim_links"
    SENTENCE_COUNT = "sentence_count"
    NUMBERS_IN_SOURCES = "numbers_in_sources"
    FORBIDDEN_PHRASES = "forbidden_phrases"
    CRITIC = "critic"


class ResourceKind(StrEnum):
    CPU = "cpu"
    GPU = "gpu"


class StageScope(StrEnum):
    CONFERENCE = "conference"
    FILE = "file"
    TALK = "talk"


class HumanGate(StrEnum):
    BINDING_CONFIRMED = "binding_confirmed"
    BOUNDARIES_CONFIRMED = "boundaries_confirmed"


class InferenceEngine(StrEnum):
    OLLAMA = "ollama"
    STUB = "stub"


class ReportFormat(StrEnum):
    MARKDOWN = "md"
    DOCX = "docx"
    PDF = "pdf"


class ApiErrorCode(StrEnum):
    UNAUTHENTICATED = "unauthenticated"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    VERSION_CONFLICT = "version_conflict"
    PAYLOAD_TOO_LARGE = "payload_too_large"
    UNSUPPORTED_MEDIA_TYPE = "unsupported_media_type"
    VALIDATION_ERROR = "validation_error"
    RATE_LIMITED = "rate_limited"
    INTERNAL_ERROR = "internal_error"


API_ERROR_STATUS: Mapping[ApiErrorCode, int] = {
    ApiErrorCode.UNAUTHENTICATED: 401,
    ApiErrorCode.FORBIDDEN: 403,
    ApiErrorCode.NOT_FOUND: 404,
    ApiErrorCode.VERSION_CONFLICT: 409,
    ApiErrorCode.PAYLOAD_TOO_LARGE: 413,
    ApiErrorCode.UNSUPPORTED_MEDIA_TYPE: 415,
    ApiErrorCode.VALIDATION_ERROR: 422,
    ApiErrorCode.RATE_LIMITED: 429,
    ApiErrorCode.INTERNAL_ERROR: 500,
}
