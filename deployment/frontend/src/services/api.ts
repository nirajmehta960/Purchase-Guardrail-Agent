/**
 * SavVio backend API client (proxied via Vite `/api` → FastAPI).
 */

const API_BASE = import.meta.env.VITE_API_BASE ?? "/api";

export interface PredictRequestBody {
  user_query: string;
  user_id: string;
  product_id?: string | null;
}

/** Financial inputs: user profile ratios + affordability features. */
export interface FinancialFeaturesView {
  discretionary_income?: number | null;
  debt_to_income_ratio?: number | null;
  saving_to_income_ratio?: number | null;
  monthly_expense_burden_ratio?: number | null;
  emergency_fund_months?: number | null;
  affordability_score?: number | null;
  price_to_income_ratio?: number | null;
  residual_utility_score?: number | null;
  savings_to_price_ratio?: number | null;
  net_worth_indicator?: number | null;
  credit_risk_indicator?: number | null;
}

export interface PredictResponse {
  recommendation: string;
  /** ML model confidence; null when the model is not loaded or did not score. */
  confidence: number | null;
  /** When confidence is null: no_model | no_pipeline | scoring_error */
  ml_unavailable_reason?: string | null;
  explanation: string;
  product_name?: string | null;
  product_price?: number | null;
  /** "catalog" | "hypothetical" | "none" */
  evaluation_mode?: string;
  /** Discretionary income minus price; may be clamped when unreliable. */
  affordability_score?: number | null;
  affordability_score_unreliable?: boolean;
  /** Months of expenses covered by liquid savings. */
  emergency_fund_months?: number | null;
  /** Debt payments / income, 0-1. */
  debt_to_income_ratio?: number | null;
  financial_features?: FinancialFeaturesView | null;
  /** ML classifier predicted label (GREEN/YELLOW/RED). */
  ml_predicted_label?: string | null;
  /** Display name for ML model. */
  ml_model_name?: string;
}

export interface UserProfileResponse {
  user_id: string;
  monthly_income: number | null;
  monthly_expenses: number | null;
  savings_balance: number | null;
  has_loan: number | null;
  loan_amount: number | null;
  monthly_emi: number | null;
  loan_interest_rate: number | null;
  loan_term_months: number | null;
  credit_score: number | null;
  employment_status: string | null;
  region: string | null;
  liquid_savings: number | null;
  discretionary_income: number | null;
  debt_to_income_ratio: number | null;
  saving_to_income_ratio: number | null;
  monthly_expense_burden_ratio: number | null;
  emergency_fund_months: number | null;
}

export interface HealthResponse {
  status: string;
  model_loaded: boolean;
  db_connected: boolean;
  llm_provider: string;
  version: string;
}

export interface ProductListItem {
  product_id: string;
  product_name: string;
  price: number | null;
  average_rating: number | null;
  rating_number: number | null;
}

export interface ProductListResponse {
  items: ProductListItem[];
  total: number;
  price_min_applied: number;
  price_max_applied: number;
  limit: number;
  offset: number;
}

export interface FetchProductsParams {
  q?: string;
  price_min?: number;
  price_max?: number;
  limit?: number;
  offset?: number;
}

async function parseError(res: Response): Promise<string> {
  try {
    const j = (await res.json()) as { detail?: string | Array<{ msg?: string }> };
    if (typeof j.detail === "string") return j.detail;
    if (Array.isArray(j.detail)) {
      return j.detail.map((x) => x.msg ?? JSON.stringify(x)).join("; ");
    }
  } catch {
    /* ignore */
  }
  return res.statusText || `HTTP ${res.status}`;
}

export async function checkHealth(): Promise<HealthResponse> {
  const res = await fetch(`${API_BASE}/health`);
  if (!res.ok) throw new Error(await parseError(res));
  return res.json() as Promise<HealthResponse>;
}

export async function fetchUserProfile(userId: string): Promise<UserProfileResponse> {
  const id = encodeURIComponent(userId.trim());
  const res = await fetch(`${API_BASE}/user/${id}/profile`);
  if (!res.ok) throw new Error(await parseError(res));
  return res.json() as Promise<UserProfileResponse>;
}

export async function fetchProducts(params?: FetchProductsParams): Promise<ProductListResponse> {
  const sp = new URLSearchParams();
  if (params?.q != null && params.q.trim()) sp.set("q", params.q.trim());
  if (params?.price_min != null) sp.set("price_min", String(params.price_min));
  if (params?.price_max != null) sp.set("price_max", String(params.price_max));
  if (params?.limit != null) sp.set("limit", String(params.limit));
  if (params?.offset != null) sp.set("offset", String(params.offset));
  const qs = sp.toString();
  const url = qs ? `${API_BASE}/products?${qs}` : `${API_BASE}/products`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(await parseError(res));
  return res.json() as Promise<ProductListResponse>;
}

export async function sendPredict(body: PredictRequestBody): Promise<PredictResponse> {
  const res = await fetch(`${API_BASE}/predict`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      user_query: body.user_query,
      user_id: body.user_id,
      product_id: body.product_id ?? undefined,
    }),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json() as Promise<PredictResponse>;
}
