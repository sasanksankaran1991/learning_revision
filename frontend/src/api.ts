const TOKEN_KEY = "lr_access";

type NativeWindow = Window & {
  Capacitor?: { isNativePlatform?: () => boolean };
};

export function isNative(): boolean {
  return Boolean((window as NativeWindow).Capacitor?.isNativePlatform?.());
}

export const API_ORIGIN = (import.meta.env.VITE_API_BASE_URL || "https://learn.sasanksankaran.in").replace(/\/$/, "");

let serverUrl = API_ORIGIN;
let refreshToken: string | null = null;

export function getServerUrl(): string {
  return serverUrl;
}

export async function setServerUrl(value: string): Promise<void> {
  serverUrl = value.trim().replace(/\/$/, "");
  if (!isNative()) return;
  const { Preferences } = await import("@capacitor/preferences");
  await Preferences.set({ key: "lr_server", value: serverUrl });
}

async function saveRefresh(value: string | null): Promise<void> {
  refreshToken = value;
  if (!isNative()) return;
  const { Preferences } = await import("@capacitor/preferences");
  if (value) await Preferences.set({ key: "lr_refresh", value });
  else await Preferences.remove({ key: "lr_refresh" });
}

export async function loadNativeSession(): Promise<void> {
  if (!isNative()) return;
  const { Preferences } = await import("@capacitor/preferences");
  const server = await Preferences.get({ key: "lr_server" });
  const refresh = await Preferences.get({ key: "lr_refresh" });
  if (server.value) serverUrl = server.value.replace(/\/$/, "");
  refreshToken = refresh.value;
}

export async function storeRefreshToken(value: string | null): Promise<void> {
  await saveRefresh(value);
}

function apiUrl(path: string): string {
  return isNative() ? `${serverUrl}${path}` : path;
}

export function getToken(): string | null {
  return sessionStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string | null): void {
  if (token) sessionStorage.setItem(TOKEN_KEY, token);
  else sessionStorage.removeItem(TOKEN_KEY);
}

function errorMessage(data: { detail?: unknown }): string {
  const detail = data.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const parts = detail.map((item) => (item && typeof item === "object" && "msg" in item ? String(item.msg) : "")).filter(Boolean);
    if (parts.length) return parts.join(" ");
  }
  return "Request failed";
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

let refreshTask: Promise<boolean> | null = null;

export async function refreshSession(): Promise<boolean> {
  if (!refreshTask) {
    refreshTask = (async () => {
      const headers = new Headers();
      const init: RequestInit = { method: "POST", credentials: "include", headers };
      if (isNative()) {
        if (!refreshToken || !serverUrl) return false;
        headers.set("Content-Type", "application/json");
        init.body = JSON.stringify({ refresh_token: refreshToken });
      }
      let response: Response;
      try {
        response = await fetch(apiUrl("/api/auth/refresh"), init);
      } catch {
        return false;
      }
      if (!response.ok) {
        setToken(null);
        if (response.status === 401) await saveRefresh(null);
        return false;
      }
      const data = await response.json();
      setToken(data.access_token);
      if (typeof data.refresh_token === "string") await saveRefresh(data.refresh_token);
      return true;
    })().finally(() => {
      refreshTask = null;
    });
  }
  return refreshTask;
}

export async function endSession(): Promise<void> {
  const headers = new Headers();
  const init: RequestInit = { method: "POST", credentials: "include", headers };
  if (isNative() && refreshToken) {
    headers.set("Content-Type", "application/json");
    init.body = JSON.stringify({ refresh_token: refreshToken });
  }
  await fetch(apiUrl("/api/auth/logout"), init).catch(() => undefined);
  setToken(null);
  await saveRefresh(null);
}

export async function api<T>(path: string, options: RequestInit = {}, retry = true): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body) headers.set("Content-Type", "application/json");
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(apiUrl(path), { ...options, headers, credentials: "include" });
  if (response.status === 401 && retry && !path.endsWith("/auth/login")) {
    const ok = await refreshSession();
    if (ok) return api<T>(path, options, false);
  }
  if (response.status === 204) return undefined as T;
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new ApiError(response.status, errorMessage(data));
  }
  return data as T;
}

export type Me = { id: number; username: string; role: string; timezone: string; must_change_password?: boolean };

export type Group = {
  id: number;
  name: string;
  guidance_prompt: string;
  archived: boolean;
  created_at: string;
  topic_count?: number;
  subtopic_count?: number;
  unanswered_count?: number;
  average_score?: number | null;
  session_count?: number;
  active_session_id?: number | null;
};

export type Topic = {
  id: number;
  group_id: number;
  name: string;
  notes: string;
  status: string;
  mastery_level: string;
  ewma_score: number | null;
  sessions_completed: number;
  active: boolean;
  next_review_at: string | null;
  map_status?: string;
  subtopic_count?: number;
  unanswered_count?: number;
};

export type Subtopic = {
  id: number;
  name: string;
  description: string;
  importance: string;
  kind: string;
  source: string;
  ewma_score: number | null;
  attempts: number;
  open_misconceptions: number;
  retest_next: boolean;
  weakness: number | null;
  exploration: number | null;
  ucb: number | null;
  untested: boolean;
};

export type TopicDetail = Topic & { subtopics: Subtopic[] };

export type Question = {
  idx: number;
  subtopic_id: number | null;
  subtopic_name: string | null;
  qtype: string;
  is_retest: boolean;
  text: string;
};

export type Evaluation = {
  score: number;
  correct_claims: string[];
  incomplete_claims: string[];
  incorrect_claims: string[];
  misconception: string | null;
  correction: string;
};

export type SessionState = {
  outcome: string;
  session_id?: number;
  group_id?: number;
  status?: string;
  topic_id?: number;
  topic_name?: string | null;
  question: Question | null;
  session_score?: number | null;
  answered?: number;
  question_count?: number;
};

export type AnswerResult = {
  evaluation: Evaluation | null;
  next_question: Question | null;
  completed: boolean;
  session_score: number | null;
  mastery_level: string;
  next_review_at: string | null;
  summary?: {
    strengths?: string[];
    weak_subtopics?: string[];
    misconceptions?: string[];
    recommendation?: string;
    session_score?: number;
  };
};

export type TelegramConfig = {
  configured: boolean;
  chat_id: string;
  enabled: boolean;
  token_last4: string;
  last_sent_on: string | null;
};

export type Settings = {
  questions_per_session: number;
  pending_ratio: number;
  w_error: number;
  w_recency: number;
  w_attempts: number;
  min_weight_floor: number;
  ucb_c: number;
  max_retests_per_session: number;
  daily_token_budget: number | null;
  telegram_summary_time: string;
  usd_inr_rate: number;
  weekly_enrich_enabled: boolean;
  weekly_enrich_weekday: number;
  weekly_enrich_time: string;
  intensify_below_score: number;
  max_new_subtopics: number;
};

export type UsageBucket = {
  calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cost_usd: number;
  cost_inr: number;
};

export type UsageDay = UsageBucket & {
  date: string;
  by_category: Record<string, UsageBucket>;
};

export type Usage = {
  timezone: string;
  start: string;
  end: string;
  today: { total_tokens: number; cost_usd: number; cost_inr: number };
  period: UsageBucket;
  by_category: Record<string, UsageBucket>;
  by_day: UsageDay[];
};

export type Credential = {
  id: number;
  provider: string;
  label: string;
  base_url: string | null;
  key_last4: string;
  question_model: string;
  eval_model: string | null;
  is_valid: boolean | null;
};

export type UserRow = {
  id: number;
  username: string;
  role: string;
  is_active: boolean;
  timezone: string;
  must_change_password: boolean;
};
