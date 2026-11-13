import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useApi } from "../api/client";
import type { LayerSplit, RootCause } from "../api/types";
import { fmtPct } from "../format";
import { ChartTooltip, Failed, Loading } from "./common";

// Fixed order + fixed colour per layer (colour follows the entity, never its rank).
// Customer-caused failures are neutral grey: they are not a leak.
const LAYERS: { key: keyof LayerSplit; label: string; color: string }[] = [
  { key: "bank", label: "Bank", color: "var(--s1)" },
  { key: "merchant", label: "Merchant integration", color: "var(--s2)" },
  { key: "network", label: "Network", color: "var(--s3)" },
  { key: "gateway", label: "Gateway", color: "var(--s4)" },
  { key: "customer", label: "Customer (not a leak)", color: "var(--s-neutral)" },
];

export function RootCauseCard({ leak_id }: { leak_id: string }) {
  const { data, error } = useApi<RootCause>(`/api/leaks/${leak_id}/root-cause`);
  if (error) return <Failed what="root cause" error={error} />;
  if (!data) return <Loading />;
  const rows = [{ name: data.cell_label ?? "This leak", ...data.cell }];
  if (data.baseline) rows.push({ name: data.baseline_label ?? "Baseline", ...data.baseline });
  const present = LAYERS.filter((l) => rows.some((r) => (r as unknown as LayerSplit)[l.key] > 0));
  return (
    <section className="card" aria-label="Root cause">
      <h3 className="card-title">Root cause · share of failures by layer</h3>
      <div style={{ height: 40 + rows.length * 44 }}>
        <ResponsiveContainer>
          <BarChart data={rows} layout="vertical" margin={{ top: 4, right: 8, bottom: 4, left: 8 }} barCategoryGap={10}>
            <XAxis type="number" domain={[0, 1]} tickFormatter={(v) => fmtPct(v, 0)} tick={{ fontSize: 11, fill: "var(--muted)" }}
              axisLine={{ stroke: "var(--axis)" }} tickLine={false} />
            <YAxis type="category" dataKey="name" width={110} tick={{ fontSize: 12, fill: "var(--ink-2)" }} axisLine={false} tickLine={false} />
            <Tooltip
              cursor={{ fill: "var(--surface-2)" }}
              content={({ active, payload, label }) =>
                active && payload?.length ? (
                  <ChartTooltip
                    title={String(label)}
                    rows={present.map((l) => ({
                      label: l.label, color: l.color,
                      value: fmtPct(Number((payload[0].payload as Record<string, number>)[l.key] ?? 0)),
                    }))}
                  />
                ) : null
              }
            />
            {present.map((l, i) => (
              <Bar key={l.key} dataKey={l.key} stackId="s" fill={l.color} stroke="var(--surface)" strokeWidth={2}
                radius={i === present.length - 1 ? [0, 4, 4, 0] : i === 0 ? [4, 0, 0, 4] : 0} isAnimationActive={false} />
            ))}
          </BarChart>
        </ResponsiveContainer>
      </div>
      {/* Legend doubles as the table view: every share is a visible label, so colour is never the only cue. */}
      <div className="legend" role="list">
        {present.map((l) => (
          <span key={l.key} className="legend-item" role="listitem">
            <span className="swatch" style={{ background: l.color }} />
            {l.label}: <strong>{fmtPct(data.cell[l.key])}</strong>
            {data.baseline && <span className="sub">(vs {fmtPct(data.baseline[l.key])})</span>}
          </span>
        ))}
      </div>
      {Object.keys(data.top_codes).length > 0 && (
        <div className="assumptions">
          {Object.entries(data.top_codes).map(([code, n]) => (
            <span key={code} className="badge mono">{code} × {n}</span>
          ))}
        </div>
      )}
      <p className="sub" style={{ marginBottom: 0 }}>Error codes are from a synthetic taxonomy.</p>
    </section>
  );
}
