"""Контракты данных «Репортёра» (ADR-003, ADR-007, ADR-014): Pydantic-модели сущностей,
фрагментов, утверждений, сводок и справки. Модели ответов LLM (суффикс Draft) передаются
модели как JSON Schema."""

from rapporteur.contracts.artifacts import (
    FactsArtifact,
    InventoryItem,
    NotesArtifact,
    SegmentsArtifact,
    SlidesArtifact,
    TitleCandidates,
    TranscriptArtifact,
)
from rapporteur.contracts.base import ContractModel
from rapporteur.contracts.claims import (
    Claim,
    Evidence,
    EvidenceDraft,
    FactDraft,
    FactsDraft,
    RejectedEvidence,
    Summary,
    SummaryDraft,
    SummaryLine,
    TalkSummaryBundle,
)
from rapporteur.contracts.entities import (
    Conference,
    ModelProfile,
    Section,
    Segment,
    SourceFile,
    Speaker,
    Talk,
)
from rapporteur.contracts.enums import (
    REPORTABLE_TALK_STATUSES,
    TALK_TRANSITIONS,
    can_transition,
)
from rapporteur.contracts.fragments import (
    FragmentDraft,
    FragmentRevision,
    PackedFragment,
    SourceFragment,
    TalkFragmentPack,
)
from rapporteur.contracts.ids import source_file_id
from rapporteur.contracts.registry import ConferenceRegistry
from rapporteur.contracts.reports import ReportInput, ReportSelection, ReportVersion

__all__ = [
    "REPORTABLE_TALK_STATUSES",
    "TALK_TRANSITIONS",
    "Claim",
    "Conference",
    "ConferenceRegistry",
    "ContractModel",
    "Evidence",
    "EvidenceDraft",
    "FactDraft",
    "FactsArtifact",
    "FactsDraft",
    "FragmentDraft",
    "FragmentRevision",
    "InventoryItem",
    "ModelProfile",
    "NotesArtifact",
    "PackedFragment",
    "RejectedEvidence",
    "ReportInput",
    "ReportSelection",
    "ReportVersion",
    "Section",
    "Segment",
    "SegmentsArtifact",
    "SlidesArtifact",
    "SourceFile",
    "SourceFragment",
    "Speaker",
    "Summary",
    "SummaryDraft",
    "SummaryLine",
    "Talk",
    "TalkFragmentPack",
    "TalkSummaryBundle",
    "TitleCandidates",
    "TranscriptArtifact",
    "can_transition",
    "source_file_id",
]
