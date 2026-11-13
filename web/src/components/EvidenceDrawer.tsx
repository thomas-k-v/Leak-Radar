import { useState } from "react";
import { useApi } from "../api/client";
import type { Evidence } from "../api/types";
import { fmtCount, fmtInr, humanize } from "../format";
import { Failed, Loading } from "./common";

const PAGE = 15;

export function EvidenceDrawer({ leak_id }: { leak_id: string }) {
  const [page, setPage] = useState(1);
  const { data, error } = useApi<Evidence>(`/api/leaks/${leak_id}/evidence?page=${page}&page_size=${PAGE}`);
  if (error) return <Failed what="evidence" error={error} />;
  if (!data) return <Loading />;
  const pages = Math.max(1, Math.ceil(data.total / PAGE));
  return (
    <section className="card" aria-label="Evidence">
      <h3 className="card-title">Evidence · {fmtCount(data.failed)} failed of {fmtCount(data.total)} matching payments</h3>
      <div className="filters" aria-label="Filter that reproduces these rows">
        {Object.entries(data.filter).map(([k, v]) => (
          <span key={k} className="badge mono">{humanize(k)}: {Array.isArray(v) ? (k === "hours" ? `${v[0]}:00–${v[1]}:00` : v.length > 3 ? `${v.length} values` : v.join(", ")) : String(v)}</span>
        ))}
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Payment</th><th>Time (IST)</th><th className="num">Amount</th><th>Method</th><th>Bank</th>
              <th>Device</th><th>Status</th><th>Error code</th><th>Layer</th><th className="num">Attempt</th>
            </tr>
          </thead>
          <tbody>
            {data.rows.map((r) => (
              <tr key={r.payment_id} className={r.status === "failed" ? "failed" : undefined}>
                <td className="mono">{r.payment_id}</td>
                <td className="mono">{r.ts}</td>
                <td className="num">{fmtInr(r.amount_inr)}</td>
                <td>{humanize(r.method)}</td>
                <td className="mono">{r.issuer_bank}</td>
                <td>{r.device}</td>
                <td>{r.status}</td>
                <td className="mono">{r.error_code ?? "—"}</td>
                <td>{r.error_layer ?? "—"}</td>
                <td className="num">{r.attempt_no}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="pager">
        <span>Failed rows first · page {page} of {fmtCount(pages)}</span>
        <button className="btn" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>Previous</button>
        <button className="btn" disabled={page >= pages} onClick={() => setPage((p) => p + 1)}>Next</button>
      </div>
    </section>
  );
}
