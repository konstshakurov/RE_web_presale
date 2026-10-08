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
  role: "user" | "admin" | string;
  jira_status: JiraStatus;
  sections: Section[];
  csrf_token: string;
}

export interface LoginRequest {
  login: string;
  password: string;
}
