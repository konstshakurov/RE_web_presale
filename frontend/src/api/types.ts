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
  contract_type?: string | null;
  rate: Rate;
  author?: string | null;
  assignee?: string | null;
  jira_url?: string | null;
  jira_updated_at: string;
}

export interface NeedsPage {
  items: NeedOut[];
  total: number;
  limit: number;
  offset: number;
  sync: SyncStamp;
}

export interface NeedsQuery {
  group: string; // key группы: id | other | all
  q?: string;
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
