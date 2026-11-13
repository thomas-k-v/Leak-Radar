export function Loading() {
  return <div className="card" aria-busy="true"><div className="skeleton" /></div>;
}

export function Failed({ what, error }: { what: string; error: string }) {
  return <div className="msg-error">Couldn't load {what}: {error}</div>;
}

export function ChartTooltip({ title, rows }: { title: string; rows: { label: string; value: string; color?: string; dashed?: boolean }[] }) {
  return (
    <div className="chart-tooltip">
      <div className="t">{title}</div>
      {rows.map((r) => (
        <div key={r.label} className="legend-item">
          {r.color && (
            <span className={r.dashed ? "swatch-line swatch-dash" : "swatch"} style={r.dashed ? { borderColor: r.color } : { background: r.color }} />
          )}
          <span>{r.label}</span>
          <strong style={{ marginLeft: "auto", paddingLeft: 12 }}>{r.value}</strong>
        </div>
      ))}
    </div>
  );
}
