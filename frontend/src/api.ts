export type Session = {
  access_token: string;
  role: "borrower" | "operator";
  borrower_id: string | null;
  expires_at: string;
};
export type Workflow = {
  id: string;
  state: string;
  version: number;
  policy_version: string;
  consent_grant_id: string;
  consent_status?: string;
  created_at?: string;
  updated_at?: string;
};
export type Reference = { kind: string; identifier: string };
export type Evidence = {
  tool: string;
  facts: Record<string, any>;
  references: Reference[];
  uncertainty_flags: string[];
};
export type LabRun = {
  id: string;
  implementation: "safe" | "unsafe";
  scenario: string;
  seed: number;
  trials: number;
  run_status: string;
  attempt_count: number;
  effects_created: number;
  confirmed_effects: number;
  duplicate_effects: number;
  unauthorized_effects: number;
  unverified_confirmations: number;
  recovered_workflows: number;
  unresolved_workflows: number;
  invariant_violations: Record<string, number>;
  violation_trials: Record<string, number>;
  trial_outcomes?: any[];
};
export type Consent = {
  purpose: string;
  version: string;
  consent_text: string;
  grant_id: string | null;
  status: string;
};
export type Explanation = {
  answer: string;
  tool_identifier: string;
  explanation_mode: string;
  fallback_reason: string | null;
  uncertainty_flags: string[];
  evidence_references: Reference[];
  simulated_data_disclaimer: string;
};

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}
export async function api<T>(
  path: string,
  token?: string,
  method = "GET",
  body?: unknown,
): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
  });
  const raw = await res.text();
  const data = raw
    ? (() => {
        try {
          return JSON.parse(raw);
        } catch {
          return { detail: raw };
        }
      })()
    : {};
  if (!res.ok) {
    const detail = data?.detail;
    throw new ApiError(
      res.status,
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail.map((v: any) => v.msg || "Invalid input").join("; ")
          : `Request failed (${res.status})`,
    );
  }
  return data as T;
}
export const currency = (minor: number | undefined) =>
  typeof minor === "number"
    ? new Intl.NumberFormat("en-IN", {
        style: "currency",
        currency: "INR",
        maximumFractionDigits: 0,
      }).format(minor / 100)
    : "—";
export const date = (s?: string) =>
  s
    ? new Date(s).toLocaleString("en-IN", {
        dateStyle: "medium",
        timeStyle: "short",
      })
    : "—";
export const title = (s: string) =>
  s.replaceAll("_", " ").replace(/\b\w/g, (c) => c.toUpperCase());
export const short = (s?: string) => (s ? s.slice(0, 8) : "—");
