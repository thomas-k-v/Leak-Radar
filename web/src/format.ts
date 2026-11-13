// Display formatting only — values themselves always come from the API.

export function indianGroup(n: number): string {
  const neg = n < 0;
  const s = String(Math.round(Math.abs(n)));
  if (s.length <= 3) return (neg ? "-" : "") + s;
  let head = s.slice(0, -3);
  const tail = s.slice(-3);
  const parts: string[] = [];
  while (head.length > 2) {
    parts.unshift(head.slice(-2));
    head = head.slice(0, -2);
  }
  if (head) parts.unshift(head);
  return (neg ? "-" : "") + parts.join(",") + "," + tail;
}

export const fmtInr = (n: number | null | undefined) => (n == null ? "–" : `₹${indianGroup(n)}`);

export function fmtInrShort(n: number | null | undefined): string {
  if (n == null) return "–";
  if (Math.abs(n) >= 1e7) return `₹${(n / 1e7).toFixed(2)}Cr`;
  if (Math.abs(n) >= 1e5) return `₹${(n / 1e5).toFixed(2)}L`;
  return fmtInr(n);
}

export const fmtPct = (r: number | null | undefined, d = 1) => (r == null ? "–" : `${(r * 100).toFixed(d)}%`);

export const fmtCount = (n: number) => indianGroup(n);

export const humanize = (s: string) => s.replace(/_/g, " ");
