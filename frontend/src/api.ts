// Typed client for the FastAPI backend (/api). In dev, Vite proxies /api to localhost:8000.

export type PaymentStyle = "pay_now" | "hold_exact" | "deposit_balance";
export type Pricing = "fixed" | "per_weight" | "quote";

export interface Meta {
  app_name: string;
  ai_available: boolean;
  business_kinds: string[];
  pricing: Record<Pricing, string>;
  payment_styles: Record<PaymentStyle, string>;
  csv_columns: string[];
}

export interface Business {
  slug: string;
  name: string;
  kind: string;
  description: string;
  paypal_email: string;
  currency: string;
}

export interface Product {
  id: number;
  name: string;
  category: string;
  description: string;
  price_cents: number;
  pricing: Pricing;
  unit: string;
  tags: string;
  lead_time_hours: number;
  active: boolean;
}

/** A product row as plain strings, for review/editing before saving. */
export interface ProductFields {
  name: string;
  category: string;
  price: string;
  pricing: Pricing;
  unit: string;
  tags: string;
  lead_time_hours: string;
  description: string;
}

export interface ProductDraft {
  fields: ProductFields;
  errors: string[];
}

export interface RuleData {
  kind: string;
  applies_to: string;
  params: Record<string, unknown>;
  source_text: string;
}

export interface Rule extends RuleData {
  id: number;
  summary: string;
  /** The setting without its scope, e.g. "50% deposit when ordering, ...". */
  setting: string;
}

export interface RuleDraft {
  rule: RuleData | null;
  summary: string | null;
  error: string | null;
  source_text: string;
}

export interface PaymentByCategory {
  category: string;
  style: PaymentStyle;
  deposit_percent?: number;
  buffer_percent?: number;
}

export interface BusinessDetail {
  business: Business;
  products: Product[];
  rules: Rule[];
  categories: string[];
  payment_by_category: PaymentByCategory[];
}

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
  const init: RequestInit = { method };
  if (body instanceof FormData) {
    init.body = body;
  } else if (body !== undefined) {
    init.body = JSON.stringify(body);
    init.headers = { "Content-Type": "application/json" };
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
  businesses: () => request<Business[]>("GET", "/businesses"),
  createBusiness: (data: Pick<Business, "name" | "kind" | "description" | "paypal_email">) =>
    request<Business>("POST", "/businesses", data),
  business: (slug: string) => request<BusinessDetail>("GET", `/businesses/${slug}`),

  importCsv: (slug: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<{ drafts: ProductDraft[] }>("POST", `/businesses/${slug}/products/import/csv`, form);
  },
  importText: (slug: string, text: string) =>
    request<{ drafts: ProductDraft[] }>("POST", `/businesses/${slug}/products/import/text`, { text }),
  saveProducts: (slug: string, products: ProductFields[]) =>
    request<{ saved: number }>("POST", `/businesses/${slug}/products/bulk`, { products }),
  addProduct: (slug: string, product: ProductFields) =>
    request<Product>("POST", `/businesses/${slug}/products`, product),
  deleteProduct: (slug: string, id: number) => request<void>("DELETE", `/businesses/${slug}/products/${id}`),

  draftRules: (slug: string, text: string) =>
    request<{ drafts: RuleDraft[] }>("POST", `/businesses/${slug}/rules/draft`, { text }),
  saveRules: (slug: string, rules: RuleData[]) =>
    request<{ saved: number; rejected: string[] }>("POST", `/businesses/${slug}/rules/bulk`, { rules }),
  setRule: (slug: string, rule: Omit<RuleData, "source_text">) =>
    request<Rule>("POST", `/businesses/${slug}/rules`, rule),
  resetRules: (slug: string) => request<{ rules: Rule[] }>("POST", `/businesses/${slug}/rules/reset`),
  deleteRule: (slug: string, id: number) => request<void>("DELETE", `/businesses/${slug}/rules/${id}`),
};

export const money = (cents: number) =>
  `$${(cents / 100).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

export const emptyProduct = (): ProductFields => ({
  name: "", category: "", price: "", pricing: "fixed", unit: "each", tags: "", lead_time_hours: "0", description: "",
});
