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
    const message = typeof detail === "string" ? detail : `Request failed (${res.status})`;
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

export const ROLE_LABEL: Record<Role, string> = { admin: "treasurer", approver: "PIC", member: "member" };
