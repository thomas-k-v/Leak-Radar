"""₹ formatting helpers (Indian digit grouping, lakh/crore short forms)."""
from __future__ import annotations


def indian_group(n: int) -> str:
    """1384200 -> '13,84,200'."""
    s = str(abs(int(n)))
    if len(s) <= 3:
        out = s
    else:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        out = ",".join(parts) + "," + tail
    return ("-" if n < 0 else "") + out


def fmt_inr(n: float | int | None) -> str:
    """Full rupee figure with Indian grouping: ₹13,84,200."""
    if n is None:
        return "–"
    return "₹" + indian_group(round(n))


def fmt_inr_short(n: float | int | None) -> str:
    """Short form: ₹13.84L, ₹2.10Cr, ₹48,250 (below one lakh stays full)."""
    if n is None:
        return "–"
    v = float(n)
    if abs(v) >= 1e7:
        return f"₹{v / 1e7:.2f}Cr"
    if abs(v) >= 1e5:
        return f"₹{v / 1e5:.2f}L"
    return fmt_inr(v)


def fmt_pct(rate: float | None, decimals: int = 1) -> str:
    """0.1412 -> '14.1%'."""
    if rate is None:
        return "–"
    return f"{rate * 100:.{decimals}f}%"
