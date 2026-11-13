import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useApi } from "../api/client";
import type { Trend } from "../api/types";
import { fmtPct } from "../format";
import { ChartTooltip, Failed, Loading } from "./common";

const SERIES = "var(--s1)";
const BASE = "var(--baseline)";
const shortDate = (d: string) => new Date(d + "T00:00:00").toLocaleDateString("en-IN", { day: "numeric", month: "short" });

export function TrendChart({ leak_id, metric }: { leak_id: string; metric: string }) {
  const { data, error } = useApi<Trend>(`/api/leaks/${leak_id}/trend?metric=${metric}`);
  if (error) return <Failed what="trend" error={error} />;
  if (!data) return <Loading />;
  return (
    <section className="card" aria-label="Trend">
      <h3 className="card-title">Daily trend · {data.label} vs {data.baseline_label}</h3>
      <div style={{ height: 220 }}>
        <ResponsiveContainer>
          <LineChart data={data.series} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
            <CartesianGrid stroke="var(--grid)" vertical={false} />
            <XAxis dataKey="date" tickFormatter={shortDate} minTickGap={28} tick={{ fontSize: 11, fill: "var(--muted)" }}
              axisLine={{ stroke: "var(--axis)" }} tickLine={false} />
            <YAxis tickFormatter={(v) => fmtPct(v, 0)} width={44} tick={{ fontSize: 11, fill: "var(--muted)" }}
              axisLine={false} tickLine={false} domain={[0, "auto"]} />
            {data.highlight_dates.map((d) => (
              <ReferenceLine key={d} x={d} stroke="var(--axis)" strokeDasharray="2 3" />
            ))}
            <Tooltip
              cursor={{ stroke: "var(--axis)", strokeWidth: 1 }}
              content={({ active, payload, label }) =>
                active && payload?.length ? (
                  <ChartTooltip
                    title={`${shortDate(String(label))}${data.highlight_dates.includes(String(label)) ? " · sale day" : ""}`}
                    rows={[
                      { label: data.label, value: fmtPct(payload[0].payload.value), color: SERIES },
                      { label: data.baseline_label, value: fmtPct(payload[0].payload.baseline), color: BASE, dashed: true },
                      { label: "attempts", value: String(payload[0].payload.n) },
                    ]}
                  />
                ) : null
              }
            />
            <Line type="monotone" dataKey="baseline" stroke={BASE} strokeWidth={2} strokeDasharray="5 4" dot={false}
              isAnimationActive={false} connectNulls />
            <Line type="monotone" dataKey="value" stroke={SERIES} strokeWidth={2} dot={false}
              activeDot={{ r: 4, stroke: "var(--surface)", strokeWidth: 2 }} isAnimationActive={false} connectNulls />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <div className="legend">
        <span className="legend-item"><span className="swatch-line" style={{ borderColor: SERIES }} />{data.label}</span>
        <span className="legend-item"><span className="swatch-line swatch-dash" style={{ borderColor: BASE }} />{data.baseline_label}</span>
        {data.highlight_dates.length > 0 && (
          <span className="legend-item"><span className="swatch-line swatch-dash" style={{ borderColor: "var(--axis)" }} />sale days</span>
        )}
      </div>
    </section>
  );
}
