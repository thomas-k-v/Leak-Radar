import { useApi } from "./api/client";
import type { EvalSummary, Meta } from "./api/types";
import { ChatPane } from "./chat/ChatPane";
import { fmtPct } from "./format";

const DISCLAIMER =
  "Independent concept project. Not affiliated with or endorsed by Razorpay. Product names are used only to illustrate fit.";

export default function App() {
  const meta = useApi<Meta>("/api/meta").data;
  const ev = useApi<EvalSummary>("/api/eval").data;
  return (
    <div className="app">
      <header className="header">
        <div className="brand"><span className="brand-mark" aria-hidden>LR</span>Leak Radar</div>
        <span className="badge badge-synthetic" title="All merchants, payments and banks are fictional">Synthetic demo data</span>
        <span className="spacer" />
        {meta && (
          <span className="meta">
            {meta.start_date} → {meta.end_date} · seed {meta.seed} · {meta.llm_provider === "mock" ? "offline mock LLM" : "live LLM"}
          </span>
        )}
      </header>
      <ChatPane />
      <footer className="footer">
        {ev ? (
          <span title={`Evaluated against planted ground truth (seed ${ev.seed}); see docs/eval-report.md`}>
            <strong>{ev.headline}</strong>
            {ev.overall.median_abs_pct_error != null && <> · median ₹ error {fmtPct(ev.overall.median_abs_pct_error)}</>}
          </span>
        ) : (
          <span>Detector evaluation not available — run <code>make eval</code></span>
        )}
        <span className="disclaimer">{DISCLAIMER}</span>
      </footer>
    </div>
  );
}
