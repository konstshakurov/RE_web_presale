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
