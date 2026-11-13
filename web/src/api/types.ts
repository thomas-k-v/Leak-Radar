// Response shapes of the Leak Radar REST API. All numbers are computed server-side by detectors.

export type Detector = "d1_bank_spike" | "d2_upi_collect" | "d3_subscription_churn";
export type RecommendationType = "cross_sell" | "config_review";

export interface LeakSummary {
  leak_id: string;
  merchant_id: string;
  merchant_name: string;
  detector: Detector;
  detector_label: string;
  severity: "high" | "medium" | "low";
  monthly_inr_at_risk: number;
  recoverable_inr_monthly: number;
  recommended_product: string;
  recommended_product_name: string;
  fit_product: string;
  fit_product_name: string;
  recommendation_type: RecommendationType;
  headline: string;
}

export interface ProductInfo {
  key: string;
  name: string;
  one_line_value: string;
  verified: boolean;
}

export type LayerSplit = Record<"merchant" | "gateway" | "bank" | "network" | "customer", number>;

export interface LeakDetail {
  leak_id: string;
  merchant_id: string;
  merchant_name: string;
  am_owner: string;
  detector: Detector;
  detector_label: string;
  headline: string;
  severity: "high" | "medium" | "low";
  monthly_inr_at_risk: number;
  recoverable_inr_monthly: number;
  annual_churn_inr: number | null;
  assumptions_used: Record<string, unknown>;
  evidence_query: Record<string, unknown>;
  dimension: Record<string, unknown>;
  stats: Record<string, unknown>;
  recommendation_type: RecommendationType;
  fit_product_info: ProductInfo;
  recommended_product_info: ProductInfo;
  calculation: { steps: { label: string; value: string }[]; assumptions: Record<string, unknown> };
}

export interface RootCause {
  leak_id: string;
  detector: Detector;
  cell: LayerSplit;
  baseline?: LayerSplit;
  top_codes: Record<string, number>;
  cell_label?: string;
  baseline_label?: string;
}

export interface TrendPoint {
  date: string;
  value: number | null;
  baseline: number | null;
  n: number;
}

export interface Trend {
  leak_id: string;
  metric: string;
  label: string;
  baseline_label: string;
  highlight_dates: string[];
  series: TrendPoint[];
}

export interface EvidenceRow {
  payment_id: string;
  ts: string;
  amount_inr: number;
  method: string;
  issuer_bank: string;
  device: string;
  status: "success" | "failed";
  error_code: string | null;
  error_layer: string | null;
  attempt_no: number;
}

export interface Evidence {
  leak_id: string;
  filter: Record<string, unknown>;
  total: number;
  failed: number;
  page: number;
  page_size: number;
  rows: EvidenceRow[];
}

export interface Pitch {
  pitch_id: string;
  leak_id: string;
  tone: string;
  draft_text: string;
  status: "draft" | "approved" | "rejected";
  edited_text: string | null;
  merchant_id: string;
  merchant_name: string;
  recommended_product_name: string;
  updated_at: string;
}

export interface EvalSummary {
  headline: string;
  seed: number;
  overall: { found: number; planted: number; false_positives: number; median_abs_pct_error: number | null };
  holdout_seeds?: number[];
}

export interface Meta {
  seed: number;
  start_date: string;
  end_date: string;
  synthetic: boolean;
  llm_provider: string;
  disclaimer: string;
}
