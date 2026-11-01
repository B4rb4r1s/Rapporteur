// Сгенерировано из schema/all.schema.json командой make generate. Не редактировать.
// Версия контрактов: 0.1.0

/**
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "ApiErrorCode".
 */
export type ApiErrorCode =
  | "unauthenticated"
  | "forbidden"
  | "not_found"
  | "version_conflict"
  | "payload_too_large"
  | "unsupported_media_type"
  | "validation_error"
  | "rate_limited"
  | "internal_error";
/**
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "CheckName".
 */
export type CheckName =
  "claim_links" | "sentence_count" | "numbers_in_sources" | "forbidden_phrases" | "critic";
/**
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "EvidenceRejection".
 */
export type EvidenceRejection =
  | "label_not_found"
  | "quote_not_found"
  | "below_threshold"
  | "number_mismatch"
  | "negation_mismatch"
  | "too_short";
/**
 * rejected — цитата не прошла сверку кодом; verified — прошла, человек ещё не смотрел;
 * needs_human — нужна проверка человеком: текст правили или опора только на текст VLM;
 * confirmed — подтверждено человеком.
 *
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "ClaimStatus".
 */
export type ClaimStatus = "rejected" | "verified" | "needs_human" | "confirmed";
/**
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "ClaimType".
 */
export type ClaimType = "problem" | "method" | "result" | "conclusion" | "listener_remark";
/**
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "TalkStatus".
 */
export type TalkStatus =
  "draft" | "matched" | "processed" | "reviewed" | "stale" | "not_held" | "insufficient_data";
/**
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "TalkType".
 */
export type TalkType = "исследовательский" | "обзорный" | "прикладной" | "иное";
/**
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "FileKind".
 */
export type FileKind = "presentation" | "audio" | "video" | "notes" | "program";
/**
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "FragmentKind".
 */
export type FragmentKind = "slide" | "transcript" | "note" | "program" | "slide_description";
/**
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "TextOrigin".
 */
export type TextOrigin = "extracted" | "asr" | "vlm" | "term_fix" | "human";
/**
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "HumanGate".
 */
export type HumanGate = "binding_confirmed" | "boundaries_confirmed";
/**
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "InferenceEngine".
 */
export type InferenceEngine = "ollama" | "stub";
/**
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "ResourceKind".
 */
export type ResourceKind = "cpu" | "gpu";
/**
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "JobStatus".
 */
export type JobStatus =
  "queued" | "running" | "succeeded" | "failed" | "cancelled" | "retry_wait" | "cancel_requested";
/**
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "PackBlock".
 */
export type PackBlock = "main" | "qa";
/**
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "PersonRole".
 */
export type PersonRole = "speaker" | "coauthor";
/**
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "StageScope".
 */
export type StageScope = "conference" | "file" | "talk";
/**
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "ReportFormat".
 */
export type ReportFormat = "md" | "docx" | "pdf";
/**
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "SegmentKind".
 */
export type SegmentKind = "talk" | "qa" | "service";
/**
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "SummaryStatus".
 */
export type SummaryStatus = "ready" | "needs_attention" | "stale";

export interface RapporteurContracts {
  [k: string]: unknown;
}
/**
 * Единый формат ошибки.
 *
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "ApiError".
 */
export interface ApiError {
  code: ApiErrorCode;
  details: {
    [k: string]: unknown;
  } | null;
  message: string;
  request_id: string;
}
/**
 * Служебная часть каждого артефакта. stub явно помечает результат заглушки (spec §17).
 *
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "ArtifactMeta".
 */
export interface ArtifactMeta {
  cache_key: string;
  config_hash: string;
  contract_version: string;
  input_hashes: string[];
  produced_at: string;
  stage: string;
  stage_version: string;
  stub: boolean;
}
/**
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "ArtifactRef".
 */
export interface ArtifactRef {
  name: string;
  sha256: string;
  storage_key: string;
}
/**
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "CheckResult".
 */
export interface CheckResult {
  attempt: number;
  check: CheckName;
  details: string | null;
  passed: boolean;
}
/**
 * Утверждение. key адресует его внутри сводки, id — в БД и API.
 *
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "Claim".
 */
export interface Claim {
  evidence: Evidence[];
  id: string;
  key: string;
  rejected_evidence: RejectedEvidence[];
  status: ClaimStatus;
  text: string;
  type: ClaimType;
}
/**
 * Подтверждённая ссылка. label и quote даёт модель, остальное вычисляет код (ADR-003).
 *
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "Evidence".
 */
export interface Evidence {
  fragment_revision_id: string;
  label: string;
  match_score: number;
  quote: string;
  /**
   * @minItems 2
   * @maxItems 2
   */
  span: [number, number];
}
/**
 * Цитата, не прошедшая сверку, с причиной отказа.
 *
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "RejectedEvidence".
 */
export interface RejectedEvidence {
  best_score: number | null;
  label: string;
  quote: string;
  reason: EvidenceRejection;
}
/**
 * Конференция. Неизвестные сведения остаются пустыми (spec §4.2).
 *
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "Conference".
 */
export interface Conference {
  end_date: string | null;
  id: string;
  organizer: string | null;
  owner_id: string;
  place: string | null;
  start_date: string | null;
  title: string | null;
  version: number;
}
/**
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "ConferenceRegistry".
 */
export interface ConferenceRegistry {
  conference: RegistryConference;
  sections: RegistrySection[];
  talks: RegistryTalk[];
}
/**
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "RegistryConference".
 */
export interface RegistryConference {
  /**
   * @maxItems 2
   */
  dates?: [] | [string] | [string, string];
  place?: string | null;
  title?: string | null;
}
/**
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "RegistrySection".
 */
export interface RegistrySection {
  date?: string | null;
  id: string;
  title: string;
}
/**
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "RegistryTalk".
 */
export interface RegistryTalk {
  affiliations?: string[];
  coauthors?: string[];
  confidence?: number | null;
  date?: string | null;
  id: string;
  order: number;
  section: string;
  sources: RegistrySources;
  speakers?: string[];
  status: TalkStatus;
  title?: string | null;
}
/**
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "RegistrySources".
 */
export interface RegistrySources {
  audio?: RegistryAudio[];
  notes?: string[];
  slides?: string[];
}
/**
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "RegistryAudio".
 */
export interface RegistryAudio {
  end: string;
  file: string;
  start: string;
}
/**
 * Ссылка от модели: метка и дословная цитата.
 *
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "EvidenceDraft".
 */
export interface EvidenceDraft {
  label: string;
  quote: string;
}
/**
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "FactDraft".
 */
export interface FactDraft {
  evidence: EvidenceDraft[];
  text: string;
}
/**
 * facts.json, шаг А.
 *
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "FactsArtifact".
 */
export interface FactsArtifact {
  facts: FactsDraft;
  meta: ArtifactMeta;
  talk_id: string;
}
/**
 * Ответ шага А. title, speakers и affiliations — подсказка для сверки с реестром.
 *
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "FactsDraft".
 */
export interface FactsDraft {
  affiliations: string[];
  conclusions: FactDraft[];
  conclusions_absent_reason: string | null;
  listener_remarks: FactDraft[];
  methods: FactDraft[];
  problem: FactDraft | null;
  results: FactDraft[];
  speakers: string[];
  talk_type: TalkType;
  title: string | null;
  uncertain: string[];
}
/**
 * Привязка файла. talk_id пуст, если файл относится к конференции целиком.
 *
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "FileBinding".
 */
export interface FileBinding {
  confidence: number | null;
  confirmed: boolean;
  id: string;
  source_file_id: string;
  talk_id: string | null;
}
/**
 * Ошибка одного файла не скрывает результаты остальных (spec §6).
 *
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "FileFailure".
 */
export interface FileFailure {
  code: string;
  message: string;
  original_name: string;
}
/**
 * Кэшируемый фрагмент: только файл и локатор, без UUID и меток, поэтому артефакт
 * переиспользуется между конференциями. Текст слайда включает заметки докладчика.
 *
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "FragmentDraft".
 */
export interface FragmentDraft {
  engine_confidence: number | null;
  kind: FragmentKind;
  locator: PageLocator | TimeLocator | LinesLocator;
  normalized_text: string;
  origin: TextOrigin;
  raw_text: string;
  source_sha256: string;
}
/**
 * Слайд презентации или страница документа.
 *
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "PageLocator".
 */
export interface PageLocator {
  kind: "page";
  page_no: number;
}
/**
 * Отрезок записи, таймкоды от начала файла.
 *
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "TimeLocator".
 */
export interface TimeLocator {
  end_ms: number;
  kind: "time";
  start_ms: number;
}
/**
 * Строки текстового файла, нумерация с единицы, конец включён.
 *
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "LinesLocator".
 */
export interface LinesLocator {
  kind: "lines";
  line_end: number;
  line_start: number;
}
/**
 * Неизменяемая версия текста. normalized_text — отображаемый текст: регистр, пунктуация,
 * числа и ё сохранены; span считается по нему.
 *
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "FragmentRevision".
 */
export interface FragmentRevision {
  created_at: string;
  edited_by: string | null;
  fragment_id: string;
  id: string;
  normalized_text: string;
  origin: TextOrigin;
  raw_text: string;
  revision: number;
}
/**
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "GenerationParams".
 */
export interface GenerationParams {
  context_tokens: number;
  max_tokens: number;
  reasoning: boolean;
  temperature: number;
}
/**
 * inventory.json, этап 6.1.
 *
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "InventoryArtifact".
 */
export interface InventoryArtifact {
  failures: FileFailure[];
  files: InventoryItem[];
  meta: ArtifactMeta;
}
/**
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "InventoryItem".
 */
export interface InventoryItem {
  duration_ms: number | null;
  encoding: string | null;
  kind: FileKind;
  media_type: string;
  original_name: string;
  sha256: string;
  size_bytes: number;
}
/**
 * Задача очереди (ADR-004). Ожидание проверки человеком статусом не является (ADR-009).
 *
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "Job".
 */
export interface Job {
  attempt: number;
  conference_id: string | null;
  created_at: string;
  current_step: string | null;
  error_code: string | null;
  heartbeat_at: string | null;
  id: string;
  lease_until: string | null;
  priority: number;
  progress: number | null;
  resource: ResourceKind;
  stage: string | null;
  status: JobStatus;
  talk_id: string | null;
  type: string;
}
/**
 * Ответ 202 на долгую операцию.
 *
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "JobAccepted".
 */
export interface JobAccepted {
  job_id: string;
  status_url: string;
}
/**
 * Профиль модели. Конвейер видит только профиль; движок — забота клиента (ADR-010).
 *
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "ModelProfile".
 */
export interface ModelProfile {
  engine: InferenceEngine;
  generation: GenerationParams;
  model: string;
  name: string;
  prompt_version: string;
  quantization: string;
  stages: string[];
  weights_revision: string;
}
/**
 * notes.json, один файл заметок. Сомнение в кодировке решает пользователь (spec §5.2).
 *
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "NotesArtifact".
 */
export interface NotesArtifact {
  encoding: string;
  encoding_confident: boolean;
  fragments: FragmentDraft[];
  meta: ArtifactMeta;
  source_sha256: string;
}
/**
 * Фрагмент в пакете доклада: метка и текст конкретной ревизии.
 *
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "PackedFragment".
 */
export interface PackedFragment {
  block: PackBlock;
  fragment_id: string;
  kind: FragmentKind;
  label: string;
  locator: PageLocator | TimeLocator | LinesLocator;
  low_quality: boolean;
  origin: TextOrigin;
  revision_id: string;
  source_file_id: string;
  text: string;
}
/**
 * Пагинация списков: тело ответа — {items: [...], page: PageMeta}.
 *
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "PageMeta".
 */
export interface PageMeta {
  limit: number;
  offset: number;
  total: number;
}
/**
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "Person".
 */
export interface Person {
  affiliation: string | null;
  full_name: string;
  id: string;
}
/**
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "PipelineGraph".
 */
export interface PipelineGraph {
  stages: StageSpec[];
  version: number;
}
/**
 * Этап конвейера. Этап с областью talk разворачивается в задачу на каждый доклад.
 *
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "StageSpec".
 */
export interface StageSpec {
  gates: HumanGate[];
  name: string;
  needs: string[];
  output: string;
  resource: ResourceKind;
  scope: StageScope;
  uses_llm: boolean;
}
/**
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "Report".
 */
export interface Report {
  author_id: string;
  conference_id: string;
  id: string;
  selection: ReportSelection;
  template_version: string;
  version: number;
}
/**
 * talk_ids — итоговый список докладов; days и section_ids хранят выбор пользователя.
 *
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "ReportSelection".
 */
export interface ReportSelection {
  days: string[];
  section_ids: string[];
  /**
   * @minItems 1
   */
  talk_ids: [string, ...string[]];
}
/**
 * Выдача сборки справки.
 *
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "ReportArtifact".
 */
export interface ReportArtifact {
  files: ReportFile[];
  meta: ArtifactMeta;
  report_id: string;
  variables: ReportVariables;
}
/**
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "ReportFile".
 */
export interface ReportFile {
  format: ReportFormat;
  sha256: string;
  size_bytes: number;
  storage_key: string;
}
/**
 * Значения, которые считает код, а не модель.
 *
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "ReportVariables".
 */
export interface ReportVariables {
  speakers_count: number;
  talks_count: number;
}
/**
 * Снимок входных данных сборки. Хранится в версии справки.
 *
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "ReportInput".
 */
export interface ReportInput {
  conference_title: string | null;
  organizer: string | null;
  period_end: string | null;
  period_start: string | null;
  place: string | null;
  selection: ReportSelection;
  skipped: SkippedTalk[];
  talks: ReportTalk[];
  template_version: string;
  unreviewed_confirmed: boolean;
}
/**
 * Выбранный доклад, который не вошёл в справку. Показывается пользователю.
 *
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "SkippedTalk".
 */
export interface SkippedTalk {
  status: TalkStatus;
  talk_id: string;
  title: string | null;
}
/**
 * Доклад в справке. Название и докладчики — из реестра, текст — из сводки.
 *
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "ReportTalk".
 */
export interface ReportTalk {
  affiliations: string[];
  conclusions: string[];
  section_title: string;
  speakers: string[];
  status: TalkStatus;
  summary: string;
  talk_id: string;
  title: string;
}
/**
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "ReportVersion".
 */
export interface ReportVersion {
  created_at: string;
  files: ReportFile[];
  id: string;
  input: ReportInput;
  number: number;
  report_id: string;
  variables: ReportVariables;
}
/**
 * Секция. Дата — значение по умолчанию для её докладов.
 *
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "Section".
 */
export interface Section {
  conference_id: string;
  date: string | null;
  hall: string | null;
  id: string;
  order: number;
  title: string;
}
/**
 * Отрезок записи. Таймкоды абсолютные, от начала файла.
 *
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "Segment".
 */
export interface Segment {
  confidence: number | null;
  confirmed: boolean;
  end_ms: number;
  id: string;
  kind: SegmentKind;
  source_file_id: string;
  start_ms: number;
  talk_id: string | null;
}
/**
 * segments.json, этап 6.5. unmatched_talk_ids — кандидаты в «не состоялся».
 *
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "SegmentsArtifact".
 */
export interface SegmentsArtifact {
  conference_id: string;
  meta: ArtifactMeta;
  segments: Segment[];
  unmatched_talk_ids: string[];
}
/**
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "SlideRecord".
 */
export interface SlideRecord {
  has_text: boolean;
  image_path: string;
  is_conclusion: boolean;
  notes: string | null;
  slide_no: number;
  tables: string[][][];
  text: string;
  title: string | null;
  vlm_description: string | null;
}
/**
 * slides.json, этап 6.2, один файл презентации.
 *
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "SlidesArtifact".
 */
export interface SlidesArtifact {
  dictionary: string[];
  fragments: FragmentDraft[];
  meta: ArtifactMeta;
  slides: SlideRecord[];
  source_sha256: string;
  title_candidates: TitleCandidates;
}
/**
 * Кандидаты с титульного слайда. Пустое поле означает «не найдено».
 *
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "TitleCandidates".
 */
export interface TitleCandidates {
  affiliations: string[];
  speakers: string[];
  title: string | null;
}
/**
 * Файл конференции. Оригинал на томе общий по SHA-256, эта запись — у каждой своя.
 *
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "SourceFile".
 */
export interface SourceFile {
  conference_id: string;
  duration_ms: number | null;
  encoding: string | null;
  id: string;
  kind: FileKind;
  media_type: string;
  original_name: string;
  sha256: string;
  size_bytes: number;
}
/**
 * Фрагмент конференции. Метки здесь нет: её назначает сборка пакета.
 *
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "SourceFragment".
 */
export interface SourceFragment {
  conference_id: string;
  id: string;
  kind: FragmentKind;
  locator: PageLocator | TimeLocator | LinesLocator;
  source_file_id: string;
}
/**
 * Вход Stage.run. Артефакты этап читает сам через ctx.storage.
 *
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "StageInputs".
 */
export interface StageInputs {
  artifacts: ArtifactRef[];
  conference_id: string | null;
  config_hash: string;
  talk_id: string | null;
}
/**
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "StageOutput".
 */
export interface StageOutput {
  artifact: ArtifactRef;
  meta: ArtifactMeta;
  warnings: string[];
}
/**
 * Сводка доклада: версия, утверждения, текст и результаты всех проверок.
 *
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "Summary".
 */
export interface Summary {
  checks: CheckResult[];
  claims: Claim[];
  conclusions: SummaryLine[];
  created_at: string;
  edited_by: string | null;
  facts: FactsDraft;
  id: string;
  input_revision_ids: string[];
  profile: string;
  prompt_versions: {
    [k: string]: string;
  };
  sentences: SummaryLine[];
  status: SummaryStatus;
  talk_id: string;
  version: number;
}
/**
 * Предложение сводки или пункт выводов со ссылками на утверждения.
 *
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "SummaryLine".
 */
export interface SummaryLine {
  /**
   * @minItems 1
   */
  claim_ids: [string, ...string[]];
  text: string;
}
/**
 * summary.json, шаги В и Г.
 *
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "SummaryArtifact".
 */
export interface SummaryArtifact {
  meta: ArtifactMeta;
  summary: Summary;
}
/**
 * Ответ шага В. Диапазон числа предложений настраивается и проверяется на шаге Г.
 *
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "SummaryDraft".
 */
export interface SummaryDraft {
  /**
   * @maxItems 3
   */
  conclusions:
    [] | [SummaryLine] | [SummaryLine, SummaryLine] | [SummaryLine, SummaryLine, SummaryLine];
  /**
   * @minItems 1
   */
  sentences: [SummaryLine, ...SummaryLine[]];
}
/**
 * Доклад реестра. Название и докладчики в справку идут отсюда, а не из ответа модели.
 *
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "Talk".
 */
export interface Talk {
  date: string | null;
  id: string;
  match_confidence: number | null;
  order: number;
  participants: TalkParticipant[];
  section_id: string;
  status: TalkStatus;
  talk_type: TalkType | null;
  title: string | null;
  version: number;
}
/**
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "TalkParticipant".
 */
export interface TalkParticipant {
  order: number;
  person_id: string;
  role: PersonRole;
}
/**
 * Все фрагменты ровно одного доклада: вход этапов сводки и единственное пространство
 * поиска цитат. Собирает воркер; цитата из другого доклада здесь не находится.
 *
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "TalkFragmentPack".
 */
export interface TalkFragmentPack {
  conference_id: string;
  fragments: PackedFragment[];
  talk_id: string;
}
/**
 * Сводка вместе с пакетом, по которому она собрана. Здесь проверяются правила,
 * которые JSON Schema не выражает. Воркер строит этот объект до публикации результата.
 *
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "TalkSummaryBundle".
 */
export interface TalkSummaryBundle {
  pack: TalkFragmentPack;
  summary: Summary;
}
/**
 * transcript.json, этап 6.4. Таймкоды абсолютные, от начала исходного файла.
 *
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "TranscriptArtifact".
 */
export interface TranscriptArtifact {
  engine: string;
  fragments: FragmentDraft[];
  meta: ArtifactMeta;
  segments: TranscriptSegment[];
  source_sha256: string;
}
/**
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "TranscriptSegment".
 */
export interface TranscriptSegment {
  confidence: number | null;
  end_ms: number;
  low_quality: boolean;
  speaker: string | null;
  start_ms: number;
  text: string;
  words: TranscriptWord[];
}
/**
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "TranscriptWord".
 */
export interface TranscriptWord {
  confidence: number | null;
  end_ms: number;
  start_ms: number;
  text: string;
}
/**
 * Шаг Б: утверждения после сверки цитат, в том числе отклонённые с причиной.
 *
 * This interface was referenced by `RapporteurContracts`'s JSON-Schema
 * via the `definition` "VerifiedClaimsArtifact".
 */
export interface VerifiedClaimsArtifact {
  claims: Claim[];
  meta: ArtifactMeta;
  talk_id: string;
}
