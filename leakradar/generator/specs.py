"""Merchant specs + planted-leak / decoy assignment.

A MerchantSpec holds everything needed to generate one merchant's payments deterministically.
Hidden fields (plants, decoys) never reach the database; they only feed ground_truth.json.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass
class D1Plant:
    method: str
    bank: str
    excess: float                    # added system-failure probability (0.10 = 10 pts)
    window_kind: str                 # "hours" | "sale_days"
    hours: tuple[int, int] | None    # [start, end) when window_kind == "hours"


@dataclass
class MerchantSpec:
    merchant_id: str
    name: str
    category: str
    city_tier: str
    am_owner: str
    has_subscriptions: bool
    products_active: list[str]
    rows: int                        # target non-recurring first attempts
    n_subs: int
    bank_weights: np.ndarray
    upi_share: float
    collect_share: float
    collect_mobile_share: float
    intl_share: float
    card_share_of_rest: float
    mobile_share: float
    ticket: float
    sr_noise: dict[str, float]       # per-method system failure jitter (probability units)
    sub_system_fail: float
    insf_prone_share: float
    d1: D1Plant | None = None
    d2_collect_share: float | None = None
    d3_excess: float | None = None
    decoy: str | None = None
    decoy_params: dict[str, Any] = field(default_factory=dict)


METHODS = ["upi_intent", "upi_collect", "card", "netbanking", "intl_card"]


def _lognormal_int(rng: np.random.Generator, median: float, sigma: float, lo: int, hi: int) -> int:
    return int(np.clip(rng.lognormal(np.log(median), sigma), lo, hi))


def build_specs(cfg: dict, n: int, rng: np.random.Generator) -> list[MerchantSpec]:
    cats = list(cfg["categories"])
    cat_w = np.array([cfg["categories"][c]["weight"] for c in cats])
    tiers = list(cfg["city_tier_weights"])
    tier_w = np.array(list(cfg["city_tier_weights"].values()))
    used_names: set[str] = set()
    specs: list[MerchantSpec] = []
    for i in range(n):
        cat = cats[rng.choice(len(cats), p=cat_w / cat_w.sum())]
        c = cfg["categories"][cat]
        while True:
            name = f"{rng.choice(cfg['name_parts']['prefix'])} {rng.choice(cfg['name_parts']['suffix'][cat])}"
            if name not in used_names:
                used_names.add(name)
                break
        has_subs = bool(rng.random() < cfg["subscriptions"]["categories"].get(cat, 0.0))
        products = ["payment_gateway"] + (["subscriptions"] if has_subs else [])
        sv = cfg["volume"]
        ss = cfg["subscriptions"]["n_subs"]
        bw = rng.dirichlet(np.array(cfg["bank_weights"]) * cfg["bank_dirichlet_conc"])
        noise_sd = cfg["merchant_sr_noise_pts"] / 100
        specs.append(MerchantSpec(
            merchant_id=f"M{i + 1:03d}",
            name=name,
            category=cat,
            city_tier=tiers[rng.choice(len(tiers), p=tier_w / tier_w.sum())],
            am_owner=cfg["am_owners"][i % len(cfg["am_owners"])],
            has_subscriptions=has_subs,
            products_active=products,
            rows=_lognormal_int(rng, sv["median"], sv["sigma"], sv["min"], sv["max"]),
            n_subs=_lognormal_int(rng, ss["median"], ss["sigma"], ss["min"], ss["max"]) if has_subs else 0,
            bank_weights=bw,
            upi_share=float(np.clip(c["upi_share"] + rng.normal(0, 0.05), 0.15, 0.8)),
            collect_share=float(rng.uniform(*cfg["collect_share_of_upi"])),
            collect_mobile_share=cfg["collect_mobile_share"],
            intl_share=float(c["intl_share"]),
            card_share_of_rest=float(np.clip(cfg["card_share_of_rest"] + rng.normal(0, 0.05), 0.4, 0.85)),
            mobile_share=float(c["mobile_share"]),
            ticket=float(c["ticket"] * rng.lognormal(0, 0.25)),
            sr_noise={m: float(rng.normal(0, noise_sd)) for m in METHODS},
            sub_system_fail=float(max(0.01, cfg["subscriptions"]["system_fail_prob"]
                                      + rng.normal(0, cfg["subscriptions"]["system_fail_noise_pts"] / 100))),
            insf_prone_share=cfg["subscriptions"]["insf_prone_share"],
        ))
    return specs


def assign_plants(specs: list[MerchantSpec], cfg: dict, rng: np.random.Generator) -> None:
    """Mutates specs in place: plants, decoys, forced volumes, products_active."""
    pc = cfg["plants"]
    idx = np.arange(len(specs))
    rng.shuffle(idx)
    sub_idx = [i for i in idx if specs[i].has_subscriptions]
    non_sub_idx = [i for i in idx if not specs[i].has_subscriptions]

    # D3 plants + high-INSF decoys come from subscription merchants.
    d3 = sub_idx[: pc["d3"]["count"]]
    insf_decoys = sub_idx[pc["d3"]["count"]: pc["d3"]["count"] + pc["decoys"]["high_insufficient_funds"]]
    taken = set(d3) | set(insf_decoys)

    # Overlaps: a few D3 merchants also get a D2 plant.
    n_ov = pc["overlap_merchants"]
    d2 = list(d3[:n_ov])
    pool = [i for i in non_sub_idx if i not in taken]
    d2 += pool[: pc["d2"]["count"] - len(d2)]
    pool = pool[pc["d2"]["count"] - n_ov:]
    taken |= set(d2)
    d1 = pool[: pc["d1"]["count"]]
    pool = pool[pc["d1"]["count"]:]
    taken |= set(d1)
    nd = pc["decoys"]
    low_vol = pool[: nd["low_volume_noisy"]]
    bad_days = pool[nd["low_volume_noisy"]: nd["low_volume_noisy"] + nd["random_bad_days"]]
    pool = pool[nd["low_volume_noisy"] + nd["random_bad_days"]:]
    extra_opt = pool[: pc["extra_optimizer_merchants"]]

    gen_banks = cfg["banks"]
    for k, i in enumerate(d1):
        s = specs[i]
        s.rows = max(s.rows, int(cfg["d1_plant_min_rows"] * rng.uniform(1.0, 1.3)))
        top3 = np.argsort(-s.bank_weights)[:3]
        bank = gen_banks[int(rng.choice(top3))]
        kinds = list(pc["d1"]["window_kinds"])
        kw = np.array(list(pc["d1"]["window_kinds"].values()))
        kind = kinds[rng.choice(len(kinds), p=kw / kw.sum())]
        # Sale days are only ~4 days of traffic: plant them on high-volume methods so the planted
        # cell clears the detector's min_attempts gate (sub-gate leaks are out of scope by design).
        methods = pc["d1"]["methods"] if kind == "hours" else pc["d1"]["sale_day_methods"]
        method = methods[rng.choice(len(methods))]
        hours = None
        if kind == "hours":
            length = int(rng.choice(pc["d1"]["window_len_h"]))
            start = int(rng.choice(np.arange(8, 24 - length + 1, 2)))
            hours = (start, start + length)
        s.d1 = D1Plant(method=method, bank=bank,
                       excess=float(rng.uniform(*pc["d1"]["excess_pts"]) / 100),
                       window_kind=kind, hours=hours)
        if k < pc["d1"]["already_has_product"]:
            s.products_active.append("optimizer")
    for k, i in enumerate(d2):
        s = specs[i]
        s.rows = max(s.rows, int(cfg["d2_plant_min_rows"] * rng.uniform(1.0, 1.5)))
        s.d2_collect_share = float(rng.uniform(*pc["d2"]["collect_share"]))
        s.collect_mobile_share = pc["d2"]["mobile_share"]
        if k >= len(d2) - pc["d2"]["already_has_product"]:
            s.products_active.append("checkout_upi_intent")
    for k, i in enumerate(d3):
        s = specs[i]
        s.n_subs = max(s.n_subs, int(cfg["subscriptions"]["d3_plant_min_subs"] * rng.uniform(1.0, 1.6)))
        s.d3_excess = float(rng.uniform(*pc["d3"]["excess_pts"]) / 100)
        if k >= len(d3) - pc["d3"]["already_has_product"]:
            s.products_active.append("subscriptions_autopay_retries")
    for i in low_vol:
        s = specs[i]
        s.rows = int(rng.integers(450, 800))
        s.decoy = "low_volume_noisy"
        s.decoy_params = {"day_noise_sd": 0.06}
    for i in bad_days:
        s = specs[i]
        s.rows = max(s.rows, int(rng.integers(6000, 12000)))
        s.decoy = "random_bad_days"
        method = ["card", "upi_intent", "netbanking"][int(rng.integers(0, 3))]
        bank = gen_banks[int(np.argmax(s.bank_weights))]
        days = sorted(int(d) for d in rng.choice(90, size=3, replace=False))
        s.decoy_params = {"method": method, "bank": bank, "day_idx": days, "excess": 0.12}
    for i in insf_decoys:
        s = specs[i]
        s.n_subs = max(s.n_subs, 300)
        s.decoy = "high_insufficient_funds"
        s.insf_prone_share = 0.5
        s.decoy_params = {"insf_prone_share": 0.5}
    for i in extra_opt:
        specs[i].products_active.append("optimizer")
