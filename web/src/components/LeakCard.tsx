import { useApi } from "../api/client";
import type { LeakDetail } from "../api/types";
import { fmtInr, fmtInrShort, humanize } from "../format";
import { Failed, Loading } from "./common";

const HIDDEN_ASSUMPTIONS = new Set(["system_codes"]);

export function LeakCard({ leak_id }: { leak_id: string }) {
  const { data: lk, error } = useApi<LeakDetail>(`/api/leaks/${leak_id}`);
  if (error) return <Failed what={`leak ${leak_id}`} error={error} />;
  if (!lk) return <Loading />;
  const rec = lk.recommended_product_info;
  return (
    <section className="card" aria-label={`Leak ${leak_id}`}>
      <h3 className="card-title">
        {lk.detector_label} <span className={`pill sev-${lk.severity}`}>{lk.severity}</span>
        <span className="sub mono" style={{ marginLeft: "auto" }}>{lk.leak_id} · {lk.merchant_id}</span>
      </h3>
      <p className="card-sub"><strong>{lk.merchant_name}</strong> — {lk.headline}.</p>
      <div className="tiles">
        <div className="tile">
          <div className="tile-label">At risk / month</div>
          <div className="tile-value">{fmtInrShort(lk.monthly_inr_at_risk)}</div>
          <div className="tile-note">{fmtInr(lk.monthly_inr_at_risk)}</div>
        </div>
        <div className="tile">
          <div className="tile-label">Recoverable / month (estimate)</div>
          <div className="tile-value">{fmtInrShort(lk.recoverable_inr_monthly)}</div>
          <div className="tile-note">{fmtInr(lk.recoverable_inr_monthly)}</div>
        </div>
        {lk.annual_churn_inr != null && (
          <div className="tile">
            <div className="tile-label">Annual churn exposure</div>
            <div className="tile-value">{fmtInrShort(lk.annual_churn_inr)}</div>
            <div className="tile-note">{fmtInr(lk.annual_churn_inr)}</div>
          </div>
        )}
      </div>
      <div>
        {lk.recommendation_type === "config_review" ? (
          <>
            <span className="pill rec-config">Config review</span>{" "}
            Already has <strong>{lk.fit_product_info.name}</strong> — check its configuration for this traffic.
          </>
        ) : (
          <>
            Recommended: <strong>{rec.name}</strong> — {rec.one_line_value}
          </>
        )}
        {!lk.fit_product_info.verified && (
          <span className="sub"> (product name illustrative, not verified)</span>
        )}
      </div>
      <details className="calc">
        <summary>How we calculated this</summary>
        <ol className="calc-steps">
          {lk.calculation.steps.map((s) => (
            <li key={s.label}>
              <span className="k">{s.label}</span>
              <span>{s.value}</span>
            </li>
          ))}
        </ol>
        <div className="assumptions">
          {Object.entries(lk.calculation.assumptions)
            .filter(([k]) => !HIDDEN_ASSUMPTIONS.has(k))
            .map(([k, v]) => (
              <span key={k} className="badge mono">{humanize(k)} = {String(v)}</span>
            ))}
        </div>
        <p className="sub">Assumptions live in config/assumptions.yaml and are stored with each leak.</p>
      </details>
    </section>
  );
}
