// Typed client for the FastAPI backend (/api). In dev, Vite proxies /api to localhost:8000.

export interface Meta {
  app_name: string;
  ai_available: boolean;
}

export type Role = "member" | "approver" | "admin";

export interface MemberInfo {
  id: number;
  name: string;
  role: Role;
}

export interface CatalogItem {
  id: number;
  name: string;
  aliases: string[];
  cap_cents: number;      // the most one purchase may cost
  restock_days: number;   // how long it usually lasts
  proof: Proof;
  active: boolean;        // false = switched off
}

export type Proof = "receipt" | "receipt_photo";

export interface CatalogCategory {
  id: number;
  name: string;
  monthly_budget_cents: number;  // 0 = no budget
  items: CatalogItem[];
}

export interface Catalog {
  monthly_budget_cents: number;  // the group's; 0 = none
  hard_ceiling_cents: number;    // the most any item's limit can be
  categories: CatalogCategory[];
}

export interface ItemChange {
  category_id?: number;
  name?: string;
  aliases?: string[];
  cap_cents?: number;
  restock_days?: number;
  proof?: Proof;
  active?: boolean;
}

export interface CategoryChange {
  name?: string;
  monthly_budget_cents?: number;
}

export type SettingValue = number | boolean | string;

export interface SettingView {
  key: string;
  label: string;
  kind: "cents" | "int" | "bool" | "choice";
  value: SettingValue;
  default: SettingValue;
  low: number;
  high: number;
  choices: string[];
}

export interface GroupSettings {
  name: string;
  currency: string;
  monthly_budget_cents: number;
  settings: SettingView[];
}

export interface SettingsChange {
  name?: string;
  monthly_budget_cents?: number;
  values?: Record<string, SettingValue>;
}

export interface Claimable {
  request_id: number;
  item: string;
  cap_cents: number;       // the most paid back without a PIC
  status: "on_it" | "expired";
  until: string | null;
  needs_photo: boolean;    // the item asks for a photo of where it's kept
}

export interface ClaimInfo {
  id: number;
  item: string;
  member: string;
  status: string;          // review | waiting | held | paid | rejected | ...
  store: string;
  receipt_date: string | null;
  total_cents: number;
  base_cents: number;      // paid after the hold
  extra_cents: number;     // over the limit: waits for a PIC
  extra_reason: string;
  extra_status: string;
  reasons: string[];       // review: why a PIC has to look at it
  payout_due_at: string | null;
  receipt_url: string;
  photo_url: string;
}

/** A request waiting for a PIC's yes or no. */
export interface WaitingRequest {
  id: number;
  member: string;
  item: string;
  quantity: number;
  estimate_cents: number | null;
  cap_cents: number;       // what the rules suggest paying back up to; the PIC can change it
  reason: string;          // the member's own words
  reasons: string[];       // why the rules sent it to a PIC
  hours: number | null;    // how long they said they need once approved; null = the group's usual time
  created_at: string;
}

/** An amount paid over a request's limit: paid only if a PIC approves it. */
export interface WaitingExtra {
  claim_id: number;
  member: string;
  item: string;
  total_cents: number;
  base_cents: number;      // paid after the hold anyway
  extra_cents: number;
  extra_reason: string;
  receipt_url: string;
}

export interface Approvals {
  requests: WaitingRequest[];
  extras: WaitingExtra[];
}

export interface Submitted {
  outcome: "accepted" | "review";
  claim: ClaimInfo;
}

export interface OpenRequest {
  request_id: number;
  item: string;
  status: "on_it" | "needs_approval";
  cap_cents: number;
  created_at: string;
  until: string | null;
}

export interface WaitingClaim {
  claim_id: number;
  item: string;
  total_cents: number;
  status: "review" | "waiting" | "held";
  payout_due_at: string | null;
}

export interface Budget {
  name: string;
  budget_cents: number;     // 0 = no budget
  committed_cents: number;  // spent this month + confirmed but not bought yet
}

export interface Question {
  question_id: number;
  item: string;
  asked_by: string;
  text: string;
  when: string;
}

export interface NotificationItem {
  id: number;
  kind: string;
  text: string;
  created_at: string;
  read: boolean;
  payload: Record<string, number>;
}

export interface Home {
  me: {
    name: string;
    role: Role;
    monthly_limit_cents: number;
    committed_cents: number;
    open_requests: OpenRequest[];
    claims_waiting: WaitingClaim[];
  };
  paypal_email_set: boolean;
  questions_for_me: Question[];
  group: { overall: Budget; categories: Budget[] };
  notifications: NotificationItem[];
  unread: number;
}

export interface FeedItem {
  post_id: number;
  kind: "on_it" | "got_it";
  text: string;
  member: string;
  created_at: string;
  status: string;           // on_it: on_it | claimed | expired | released; got_it: the claim's status
  until: string | null;
  likes: number;
  liked: boolean;
  claim_id: number | null;
  can_flag: boolean;        // still in its hold, and not your own
}

export interface ChatMessage {
  role: "user" | "assistant";
  text: string;
  created_at: string;
}

export type ChatEvent =
  | { type: "step"; text: string }
  | { type: "text"; text: string }
  | { type: "done"; text: string; failed: boolean };

// Who the app is being used as ("View as", docs/SPEC.md §13): sent with every call.
let memberId: number | null = null;
export const setMemberId = (id: number | null) => { memberId = id; };

export class ApiError extends Error {
  status: number;
  data: unknown;
  constructor(status: number, message: string, data: unknown) {
    super(message);
    this.status = status;
    this.data = data;
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = memberId === null ? {} : { "X-Member-Id": String(memberId) };
  const init: RequestInit = { method, headers };
  if (body instanceof FormData) {
    init.body = body;
  } else if (body !== undefined) {
    init.body = JSON.stringify(body);
    headers["Content-Type"] = "application/json";
  }
  const res = await fetch(`/api${path}`, init);
  if (res.status === 204) return undefined as T;
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = (data as { detail?: unknown } | null)?.detail;
    // FastAPI's own validation errors come as a list: show the first one plainly
    const first = Array.isArray(detail) ? (detail[0] as { msg?: string; loc?: unknown[] } | undefined) : undefined;
    const message = typeof detail === "string" ? detail
      : first?.msg ? `${String(first.loc?.at(-1) ?? "input")}: ${first.msg}` : `Request failed (${res.status})`;
    throw new ApiError(res.status, message, data);
  }
  return data as T;
}

export const api = {
  meta: () => request<Meta>("GET", "/meta"),
  members: () => request<MemberInfo[]>("GET", "/members"),

  catalog: () => request<Catalog>("GET", "/catalog"),
  addCategory: (body: Required<CategoryChange>) => request<Catalog>("POST", "/categories", body),
  updateCategory: (id: number, body: CategoryChange) => request<Catalog>("PATCH", `/categories/${id}`, body),
  addItem: (body: Required<Omit<ItemChange, "active">>) => request<Catalog>("POST", "/items", body),
  updateItem: (id: number, body: ItemChange) => request<Catalog>("PATCH", `/items/${id}`, body),

  claimable: () => request<Claimable[]>("GET", "/claimable"),
  submitClaim: (form: FormData) => request<Submitted>("POST", "/claims", form),
  claimsForReview: () => request<ClaimInfo[]>("GET", "/claims/review"),
  acceptClaim: (id: number) => request<ClaimInfo>("POST", `/claims/${id}/accept`),
  rejectClaim: (id: number, note: string) => request<ClaimInfo>("POST", `/claims/${id}/reject`, { note }),
  approvals: () => request<Approvals>("GET", "/approvals"),
  approveRequest: (id: number, cap: string) => request<WaitingRequest>("POST", `/requests/${id}/approve`, { cap }),
  rejectRequest: (id: number, note: string) => request<WaitingRequest>("POST", `/requests/${id}/reject`, { note }),
  approveExtra: (claimId: number) => request<WaitingExtra>("POST", `/claims/${claimId}/extra/approve`),
  rejectExtra: (claimId: number, note: string) =>
    request<WaitingExtra>("POST", `/claims/${claimId}/extra/reject`, { note }),

  home: () => request<Home>("GET", "/home"),
  markRead: (ids?: number[]) => request<{ marked: number }>("POST", "/notifications/read", { ids: ids ?? null }),
  release: (requestId: number) => request<{ released: number }>("POST", `/requests/${requestId}/release`),
  answer: (questionId: number, answer: string) =>
    request<{ answered: number }>("POST", `/questions/${questionId}/answer`, { answer }),

  feed: () => request<FeedItem[]>("GET", "/feed"),
  like: (postId: number) => request<{ likes: number }>("POST", `/feed/${postId}/like`),
  askBuyer: (claimId: number, text: string) => request<{ question_id: number }>("POST", `/claims/${claimId}/ask`, { text }),
  report: (claimId: number, text: string, checked: string) =>
    request<{ report_id: number }>("POST", `/claims/${claimId}/report`, { text, checked }),

  chat: () => request<{ ai_available: boolean; messages: ChatMessage[] }>("GET", "/chat"),

  settings: () => request<GroupSettings>("GET", "/settings"),
  saveSettings: (body: SettingsChange) => request<GroupSettings>("PATCH", "/settings", body),
};

export const money = (cents: number) =>
  `$${(cents / 100).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

/** "25", "$25.50", "1,200" → cents; null if it isn't an amount. */
export function toCents(text: string): number | null {
  const clean = text.trim().replace(/[$,]/g, "");
  if (!/^\d+(\.\d{1,2})?$/.test(clean)) return null;
  return Math.round(Number(clean) * 100);
}

/** Cents as an editable amount: 2500 → "25", 2550 → "25.50". */
export const dollars = (cents: number) => (cents % 100 ? (cents / 100).toFixed(2) : String(cents / 100));

/** Send a chat message; `onEvent` gets each piece of the reply as it's written (see app/api/chat.py). */
export async function say(text: string, onEvent: (event: ChatEvent) => void): Promise<void> {
  const res = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(memberId === null ? {} : { "X-Member-Id": String(memberId) }) },
    body: JSON.stringify({ text }),
  });
  if (!res.ok || !res.body) {
    const data = await res.json().catch(() => null);
    const detail = (data as { detail?: unknown } | null)?.detail;
    throw new ApiError(res.status, typeof detail === "string" ? detail : `Request failed (${res.status})`, data);
  }
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += value;
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    for (const line of lines) if (line.trim()) onEvent(JSON.parse(line) as ChatEvent);
  }
  if (buffer.trim()) onEvent(JSON.parse(buffer) as ChatEvent);
}

/** "5 minutes ago", "yesterday", "3 days ago". */
export function ago(iso: string, now = Date.now()): string {
  const minutes = Math.floor((now - new Date(iso).getTime()) / 60000);
  if (minutes < 2) return "just now";
  if (minutes < 60) return `${minutes} minutes ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return hours === 1 ? "an hour ago" : `${hours} hours ago`;
  const days = Math.floor(hours / 24);
  return days === 1 ? "yesterday" : `${days} days ago`;
}

/** A file the API only gives to signed-in members (receipts): fetched with the member header, as a local URL. */
export async function privateFileUrl(url: string): Promise<string> {
  const res = await fetch(url, { headers: memberId === null ? {} : { "X-Member-Id": String(memberId) } });
  if (!res.ok) throw new ApiError(res.status, "Couldn't load the file", null);
  return URL.createObjectURL(await res.blob());
}

/** "Mon 19 Oct, 08:40" from an API time (UTC, as the app shows times for now). */
export const when = (iso: string) => new Date(iso).toLocaleString("en-GB", {
  weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "UTC",
}) + " UTC";

export const ROLE_LABEL: Record<Role, string> = { admin: "treasurer", approver: "PIC", member: "member" };
