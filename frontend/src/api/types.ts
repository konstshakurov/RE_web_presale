// Типы по backend/openapi.json (схемы MeResponse, JiraStatus, Section, LoginRequest).
export interface JiraStatus {
  state: "ok" | "blocked" | "unavailable" | "not_checked";
  code?: string;
  message?: string;
}

export interface Section {
  key: string;
  title: string;
  admin_only: boolean;
}

export interface MeResponse {
  login: string;
  display_name?: string | null; // ФИО из LDAP; нет - показываем login
  role: "user" | "admin" | string;
  jira_status: JiraStatus;
  sections: Section[];
  csrf_token: string;
}

export interface LoginRequest {
  login: string;
  password: string;
}

// Типы /sync по backend/openapi.json (SyncStamp, NeedOut, NeedsPage, NeedGroups, RefreshResult, EpicOut).
export type SyncScope = "needs" | "employees";

export interface SyncStamp {
  scope: SyncScope;
  state: string; // ok | never_synced | paused | needs_reauthorization | no_permission | jira_unavailable | ...
  stale: boolean;
  last_success_at?: string | null; // «актуально на»
  last_attempt_at?: string | null;
  paused?: boolean;
  code?: string;
  message?: string; // текст отказа Jira как есть
  interval_sec: number;
}

export interface Rate {
  amount?: number | null;
  currency?: string | null;
  includes_vat?: boolean | null;
  proposer?: string | null;
  unit?: string | null;
}

export interface NeedOut {
  jira_key: string;
  title: string;
  status: string;
  epic_key?: string | null;
  epic_name?: string | null;
  employee_key?: string | null; // карточка исполнителя в Jira, если определена однозначно
  contract_type?: string | null;
  rate: Rate;
  author?: string | null;
  assignee?: string | null;
  jira_url?: string | null;
  jira_created_at: string;
  jira_updated_at: string;
}

export interface NeedsPage {
  items: NeedOut[];
  total: number;
  limit: number;
  offset: number;
  sync: SyncStamp;
}

export type NeedSort = "created" | "updated";
export type SortOrder = "asc" | "desc";

export interface NeedsQuery {
  group: string; // key группы: id | other | all
  q?: string;
  createdFrom?: string; // YYYY-MM-DD, включительно
  createdTo?: string; // YYYY-MM-DD, включительно
  sort?: NeedSort;
  order?: SortOrder;
  limit: number;
  offset: number;
}

export interface StatusGroup {
  key: string;
  id?: number | null;
  name: string;
  count: number;
}

export interface NeedGroups {
  groups: StatusGroup[];
  statuses: { status: string; count: number }[];
  sync: SyncStamp;
}

export interface RefreshResult {
  scope: SyncScope;
  ok: boolean;
  busy?: boolean;
  fetched?: number;
  upserted?: number;
  sync: SyncStamp;
}

export interface SyncErrorBody {
  state: string; // needs_reauthorization | no_permission | unavailable | blocked | not_enabled | error
  code: string;
  message: string;
}

export interface RuleOut {
  id: string;
  name: string;
  value: string;
}

export interface EpicOut {
  epic_key: string;
  name: string;
  summary?: string | null;
  rules: RuleOut[]; // только заполненные
  stale: boolean;
  fetched_at?: string | null;
  needs_total: number;
  error?: SyncErrorBody | null;
}

// Типы /admin/users по backend/openapi.json (UserOut).
export interface AdminUser {
  login: string;
  role: "user" | "admin";
  active: boolean;
  created_at: string;
  updated_at: string;
}

// Типы /admin/status-groups по backend/openapi.json (KON-64).
export interface GroupOut {
  id: number;
  name: string;
  sort_order: number;
  statuses: string[];
  need_count: number;
}

export interface StatusInfo {
  status: string;
  count: number;
  group_ids: number[]; // пусто - статус попадает в «Прочие»
}

export interface StatusGroupsData {
  groups: GroupOut[];
  statuses: StatusInfo[];
  other: StatusInfo[];
  other_count: number;
  reserved: string[];
}

// Типы /sync/employees и /cv по backend/openapi.json (EmployeeOut, EmployeesPage, FileOut, CvLimits; KON-35, KON-36).
export interface EmployeeOut {
  jira_key: string;
  jira_url?: string | null;
  full_name: string;
  status: string;
  assignable: boolean;
  extra_experience?: string | null; // «Подробнее об опыте»
  vacation_plan?: string | null; // поле Jira «Отпуск» как есть
  note?: string | null;
  note_author?: string | null;
  note_updated_at?: string | null;
  synced_at: string;
}

export interface EmployeesPage {
  items: EmployeeOut[];
  total: number;
  limit: number;
  offset: number;
  statuses: { status: string; count: number }[];
  sync: SyncStamp;
}

export type FileKind = "main_cv" | "extra_cv" | "materials" | "client_template_cv";

export interface FileOut {
  id: number;
  employee_key: string;
  kind: FileKind | string;
  kind_title: string;
  client_epic_key?: string | null;
  file_name: string;
  mime_type: string;
  size_bytes: number;
  is_archived: boolean;
  archived_at?: string | null;
  uploaded_by: string;
  uploaded_at: string;
  extraction_status: "ok" | "error" | "none" | string;
  extraction_message?: string | null;
  can_delete: boolean; // false - актуальное основное CV
  can_restore: boolean; // true - архивная версия основного CV
}

export interface EmployeeFiles {
  employee_key: string;
  files: FileOut[];
  archive: FileOut[];
}

export interface UploadResult {
  file: FileOut;
  replaced_file_id?: number | null;
}

export interface CvText {
  file: FileOut | null;
  extraction_status?: string; // только у main-cv: ok | error | none
  text: string | null;
  message?: string | null;
}

export interface CvLimits {
  max_file_mb: number;
  max_file_bytes: number;
  upload_kinds: Record<string, string>; // тип -> название
  extensions_by_kind: Record<string, string[]>; // допустимые расширения по типу файла
}

// Типы сопоставления, поиска по смыслу и шлюза LLM по backend/openapi.json (MatchStateOut, SearchStateOut, RunOut,
// ProviderOut, ProfileOut и др.; KON-37, KON-38).
export type RunStatus = "none" | "running" | "awaiting_confirmation" | "done" | "partial" | "failed" | "unavailable" | string;

export interface MatchItem {
  employee_key: string;
  full_name: string;
  status: string;
  percent: number;
  rationale: string;
  note?: string | null;
  note_author?: string | null;
  note_updated_at?: string | null;
  cv_file_id: number;
  calculated_at: string;
  stale: boolean;
  skills: string[];
}

export interface MatchTask {
  task_id: string;
  status: string; // статус задачи шлюза: awaiting_confirmation | queued | running | done | failed | timeout | cancelled
  requires_confirmation: boolean;
  batch_status: string;
  candidates: number;
  run_id?: string;
  stage?: string;
}

// Общая часть состояния сопоставления и поиска по смыслу.
export interface RunState {
  status: RunStatus;
  requested_by: string | null;
  model: string | null;
  started_at: string | null;
  calculated_at: string | null;
  stale: boolean;
  candidates_total: number;
  evaluated: number;
  total: number;
  can_evaluate_next?: boolean;
  deep_size?: number;
  remaining?: number;
  screened?: number;
  screen_total?: number;
  stage?: string | null;
  run_id?: string | null;
  tasks: MatchTask[];
  batches_total: number;
  batches_done: number;
  batches_failed: number;
  errors: string[];
}

export interface MatchState extends RunState {
  need_key: string;
  items: MatchItem[];
}

export type SearchSource = "llm" | "screening" | "fallback" | string;

export interface SearchItem {
  employee_key: string;
  full_name: string;
  status: string;
  source: SearchSource;
  percent?: number | null;
  screen_percent?: number | null;
  rationale: string;
  score: number;
  matched_skills: string[];
  matched_terms: string[];
  note?: string | null;
  note_author?: string | null;
  note_updated_at?: string | null;
  cv_file_id: number;
  calculated_at?: string | null;
  stale: boolean;
  skills: string[];
}

export interface SearchState extends RunState {
  query: string;
  mode: "llm" | "fallback" | string;
  notice?: string | null;
  items: SearchItem[];
}

export interface SearchParams {
  query: string;
  provider_id?: number | null;
  force?: boolean;
  status?: string | null;
  technology?: string[];
  match?: "all" | "any";
  q?: string | null;
  limit?: number;
}

export interface RunTask {
  task_id: string;
  status: string;
  candidates: number;
  requires_confirmation: boolean;
  replacements: Record<string, number>;
  ner_used: boolean;
  prompt?: string | null;
  system?: string | null;
  error_message?: string | null;
}

export interface RunOut {
  run_id: string;
  kind: "match" | "search" | string;
  need_key?: string | null;
  stage: string;
  status: RunStatus;
  requires_confirmation: boolean;
  is_external: boolean;
  provider_name: string;
  candidates: number;
  tasks: RunTask[];
  replacements: Record<string, number>;
}

export interface LlmTask {
  id: string;
  purpose: string;
  ref?: string | null;
  status: string;
  provider_id: number;
  provider_name: string;
  is_external: boolean;
  requires_confirmation: boolean;
  timeout_sec: number;
  attempts: number;
  error_code?: string | null;
  error_message?: string | null;
}

export interface ProviderPublic {
  id: number;
  name: string;
  is_external: boolean;
  is_default: boolean;
}

export interface ProviderOut extends ProviderPublic {
  url: string;
  timeout_sec: number;
  model: string;
  has_key: boolean; // значение ключа API не возвращается никогда
  updated_at: string;
}

export interface ProviderCreate {
  name: string;
  url: string;
  is_external: boolean;
  is_default: boolean;
  model: string;
  timeout_sec: number;
  api_key?: string | null;
}

export interface ProviderPatch {
  name?: string;
  url?: string;
  is_external?: boolean;
  is_default?: boolean;
  model?: string;
  timeout_sec?: number;
  api_key?: string | null;
  remove_key?: boolean;
}

export interface Dictionary {
  companies: string[];
  locations: string[];
  persons: string[];
  projects: string[];
}

export interface MatchSettings {
  deep_size: number;
  deep_size_min: number;
  deep_size_max: number;
  screen_batch: number;
  screen_batch_min: number;
  screen_batch_max: number;
}

export interface Technology {
  tag: string;
  employees: number;
}

export interface ResumeItem {
  employee_key: string;
  full_name: string;
  status: string;
  main_cv_file_id: number | null;
  skills: string[];
  skills_status: string | null; // pending | ok | failed; null - не извлекались
}

export interface FileLink {
  id: number;
  kind: string;
  file_name: string;
}

export interface EmployeeProfileData {
  employee_key: string;
  full_name: string;
  status: string;
  assignable: boolean;
  description?: string | null;
  vacation_plan?: string | null;
  experience_years?: number | null; // только если CV называет годы явно
  main_cv?: FileLink | null;
  files: FileLink[];
  skills: string[];
  skills_status?: string | null;
  sections: Record<string, string>; // BR-007: profile, stack, industries, skills, languages, education, projects
  note?: string | null;
  synced_at: string;
}
