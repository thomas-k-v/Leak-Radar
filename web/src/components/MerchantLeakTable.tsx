import { useApi } from "../api/client";
import type { LeakSummary } from "../api/types";
import { fmtInrShort } from "../format";
import { Loading, Failed } from "./common";

export function MerchantLeakTable({ leak_ids, onAsk }: { leak_ids: string[]; onAsk: (msg: string) => void }) {
  const { data, error } = useApi<LeakSummary[]>(`/api/leaks?ids=${leak_ids.join(",")}&limit=${leak_ids.length}`);
  if (error) return <Failed what="leaks" error={error} />;
  if (!data) return <Loading />;
  return (
    <section className="card" aria-label="Merchant leak table">
      <h3 className="card-title">Leaks · ranked by recoverable ₹/month</h3>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th className="num">#</th>
              <th>Merchant</th>
              <th>Leak</th>
              <th>Recommendation</th>
              <th>Severity</th>
              <th className="num">At risk / mo</th>
              <th className="num">Recoverable / mo</th>
            </tr>
          </thead>
          <tbody>
            {data.map((r, i) => (
              <tr
                key={r.leak_id}
                className="clickable"
                tabIndex={0}
                onClick={() => onAsk(`Show details for leak ${r.leak_id} (${r.merchant_name})`)}
                onKeyDown={(e) => e.key === "Enter" && onAsk(`Show details for leak ${r.leak_id} (${r.merchant_name})`)}
                title="Ask the copilot about this leak"
              >
                <td className="num sub">{i + 1}</td>
                <td>
                  <div>{r.merchant_name}</div>
                  <div className="sub mono">{r.merchant_id} · {r.leak_id}</div>
                </td>
                <td>{r.detector_label}</td>
                <td>
                  {r.recommendation_type === "config_review" ? (
                    <span className="pill rec-config" title={`Already has ${r.fit_product_name}`}>
                      Config review · {r.fit_product_name}
                    </span>
                  ) : (
                    r.recommended_product_name
                  )}
                </td>
                <td>
                  <span className={`pill sev-${r.severity}`}>{r.severity}</span>
                </td>
                <td className="num">{fmtInrShort(r.monthly_inr_at_risk)}</td>
                <td className="num"><strong>{fmtInrShort(r.recoverable_inr_monthly)}</strong></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
