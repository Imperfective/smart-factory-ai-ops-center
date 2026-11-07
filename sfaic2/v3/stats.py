"""Pre-registered statistics for the v3 paired design (docs 01 §7).

* Pairs: (scenario, load, seed) cells, identical fault sequences across arms.
* Continuous metrics: mean paired difference with a seed-cluster bootstrap 95 % CI (resample seeds with replacement;
  each seed carries all its scenario×load pairs), Cohen's dz.
* Binary target attainment: exact McNemar test on discordant pairs.
* Unsafe authorisation rate: pooled ratio (allowed/forbidden) per arm with cluster-bootstrap CI; episodes with no
  forbidden proposals are NA and excluded, as specified.
* Secondary metrics: Holm correction within each contrast.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

PRIMARY = ["par", "target_met", "unsafe_authorization_rate", "cost_balanced"]
SECONDARY = ["raw_attainment", "shortfall", "good_end", "defects_shipped", "unnecessary_stop_min", "action_delay_min", "transport_errors", "energy_kwh",
             "kwh_per_good", "max_kw", "overloads", "mttr_main_s", "production_recovery_s", "detection_recall", "detection_precision", "false_alarms_per_h",
             "mttd_s", "escalations", "stale_decisions", "cost_quality", "cost_energy", "duplicate_executions", "wrong_pallet", "premature_dispatch", "ack_timeouts"]
CONTRASTS = [("R2", "R1", "H2: Jev increment"), ("R4", "R3", "H3: electrical/network context"), ("R4", "R1", "H1: full system vs ML+rules"),
             ("R3", "R2", "evidence validation + safety gate"), ("R1", "R0", "ML detection increment"), ("R5", "R4", "planner reference vs R4")]
KEYS = ["scenario", "load", "seed"]


def _pairs(df: pd.DataFrame, a: str, b: str, metric: str) -> pd.DataFrame:
    x = df[df.arm == a].set_index(KEYS)[metric]
    y = df[df.arm == b].set_index(KEYS)[metric]
    j = pd.concat([x.rename("a"), y.rename("b")], axis=1, join="inner").dropna()
    return j.reset_index()


def cluster_boot(d: pd.DataFrame, col: str = "d", n: int = 2000, seed: int = 2026) -> tuple[float, float]:
    seeds = d["seed"].unique()
    if len(seeds) < 2:
        return (float("nan"), float("nan"))
    g = {s: d.loc[d.seed == s, col].to_numpy(float) for s in seeds}
    rng = np.random.default_rng(seed)
    means = []
    for _ in range(n):
        pick = rng.choice(seeds, len(seeds), replace=True)
        v = np.concatenate([g[s] for s in pick])
        means.append(v.mean() if len(v) else np.nan)
    return float(np.nanquantile(means, 0.025)), float(np.nanquantile(means, 0.975))


def mcnemar_exact(b: int, c: int) -> float:
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    p = sum(math.comb(n, i) for i in range(0, k + 1)) / 2 ** n
    return min(1.0, 2 * p)


def paired(df: pd.DataFrame, a: str, b: str, metric: str) -> dict:
    if metric == "unsafe_authorization_rate":
        return unsafe_contrast(df, a, b)
    p = _pairs(df, a, b, metric)
    if p.empty:
        return dict(metric=metric, n_pairs=0)
    p["d"] = p["a"] - p["b"]
    lo, hi = cluster_boot(p)
    sd = p["d"].std(ddof=1)
    out = dict(metric=metric, n_pairs=len(p), mean_a=p["a"].mean(), mean_b=p["b"].mean(), mean_diff=p["d"].mean(), ci_low=lo, ci_high=hi,
               excludes_zero=bool(lo > 0 or hi < 0) if not math.isnan(lo) else None, cohens_dz=(p["d"].mean() / sd) if sd and sd > 0 else None)
    if metric == "target_met":
        bb = int(((p["a"] == 1) & (p["b"] == 0)).sum())
        cc = int(((p["a"] == 0) & (p["b"] == 1)).sum())
        out.update(discordant_a_only=bb, discordant_b_only=cc, mcnemar_p=mcnemar_exact(bb, cc))
    return out


def unsafe_contrast(df: pd.DataFrame, a: str, b: str) -> dict:
    def pooled(sub):
        f = sub["forbidden_proposed"].sum()
        return (sub["forbidden_allowed"].sum() / f) if f else float("nan")
    da, db = df[df.arm == a], df[df.arm == b]
    ra, rb = pooled(da), pooled(db)
    seeds = sorted(set(da.seed) & set(db.seed))
    rng = np.random.default_rng(2026)
    diffs = []
    ga = {s: da[da.seed == s] for s in seeds}
    gb = {s: db[db.seed == s] for s in seeds}
    for _ in range(2000):
        pick = rng.choice(seeds, len(seeds), replace=True) if seeds else []
        sa = pd.concat([ga[s] for s in pick]) if len(pick) else da.iloc[:0]
        sb = pd.concat([gb[s] for s in pick]) if len(pick) else db.iloc[:0]
        diffs.append(pooled(sa) - pooled(sb))
    lo, hi = (float(np.nanquantile(diffs, 0.025)), float(np.nanquantile(diffs, 0.975))) if seeds else (float("nan"), float("nan"))
    return dict(metric="unsafe_authorization_rate", n_pairs=len(seeds), mean_a=ra, mean_b=rb, mean_diff=ra - rb, ci_low=lo, ci_high=hi,
                excludes_zero=bool(lo > 0 or hi < 0) if not math.isnan(lo) else None, forbidden_a=int(da.forbidden_proposed.sum()), allowed_a=int(da.forbidden_allowed.sum()),
                forbidden_b=int(db.forbidden_proposed.sum()), allowed_b=int(db.forbidden_allowed.sum()), note="pooled allowed/forbidden; episodes without forbidden proposals contribute nothing")


def holm(ps: list[float]) -> list[float]:
    idx = sorted(range(len(ps)), key=lambda i: ps[i])
    adj, run = [0.0] * len(ps), 0.0
    for r, i in enumerate(idx):
        run = max(run, min(1.0, (len(ps) - r) * ps[i]))
        adj[i] = run
    return adj


def boot_p(p: pd.DataFrame, n: int = 2000, seed: int = 7) -> float:
    """Two-sided cluster-bootstrap p-value for mean(d)=0 (sign test of the bootstrap distribution, shifted)."""
    seeds = p["seed"].unique()
    if len(seeds) < 2:
        return float("nan")
    g = {s: p.loc[p.seed == s, "d"].to_numpy(float) for s in seeds}
    obs = p["d"].mean()
    rng = np.random.default_rng(seed)
    cnt = 0
    for _ in range(n):
        v = np.concatenate([g[s] for s in rng.choice(seeds, len(seeds), replace=True)]) - obs
        cnt += abs(v.mean()) >= abs(obs)
    return (cnt + 1) / (n + 1)


def contrasts(df: pd.DataFrame, contrasts=CONTRASTS) -> pd.DataFrame:
    rows = []
    for a, b, label in contrasts:
        if not ((df.arm == a).any() and (df.arm == b).any()):
            continue
        for m in PRIMARY:
            rows.append(dict(contrast=f"{a}-{b}", hypothesis=label, family="primary", **paired(df, a, b, m)))
        sec = []
        for m in SECONDARY:
            if m not in df.columns:
                continue
            r = paired(df, a, b, m)
            if r.get("n_pairs"):
                p = _pairs(df, a, b, m)
                p["d"] = p["a"] - p["b"]
                r["p_boot"] = boot_p(p) if p["d"].abs().sum() > 0 else 1.0
            sec.append(dict(contrast=f"{a}-{b}", hypothesis=label, family="secondary", **r))
        adj = holm([s.get("p_boot", 1.0) if s.get("p_boot") == s.get("p_boot") else 1.0 for s in sec])
        for s, h in zip(sec, adj):
            s["p_holm"] = h
        rows += sec
    return pd.DataFrame(rows)


def by_arm(df: pd.DataFrame, metrics=None) -> pd.DataFrame:
    metrics = metrics or PRIMARY + SECONDARY
    g = df.groupby("arm")
    out = g[[m for m in metrics if m in df.columns and m != "unsafe_authorization_rate"]].mean(numeric_only=True)
    out["unsafe_authorization_rate_pooled"] = g.apply(lambda s: (s.forbidden_allowed.sum() / s.forbidden_proposed.sum()) if s.forbidden_proposed.sum() else np.nan)
    out["forbidden_proposed"] = g.forbidden_proposed.sum()
    out["forbidden_allowed"] = g.forbidden_allowed.sum()
    out["n"] = g.size()
    return out


def cause_macro_f1(df: pd.DataFrame) -> pd.DataFrame:
    import json
    rows = []
    for arm, sub in df.groupby("arm"):
        pairs = [tuple(x) for v in sub.cause_pairs.dropna() for x in json.loads(v)]
        labels = sorted({t for t, _ in pairs})
        f1s = []
        for lab in labels:
            tp = sum(1 for t, p in pairs if t == lab and p == lab)
            fp = sum(1 for t, p in pairs if t != lab and p == lab)
            fn = sum(1 for t, p in pairs if t == lab and p != lab)
            pr = tp / (tp + fp) if tp + fp else 0.0
            rc = tp / (tp + fn) if tp + fn else 0.0
            f1s.append(2 * pr * rc / (pr + rc) if pr + rc else 0.0)
        abstain = sum(1 for _, p in pairs if p in ("UNKNOWN", "NO_DECISION")) / len(pairs) if pairs else None
        rows.append(dict(arm=arm, n_events=len(pairs), macro_f1=float(np.mean(f1s)) if f1s else None, unknown_or_no_decision_rate=abstain))
    return pd.DataFrame(rows)


def regret(df: pd.DataFrame, oracle: pd.DataFrame, cost: str = "cost_balanced") -> pd.DataFrame:
    """Replay-oracle regret: for each (scenario, load, seed) the first incident decision is forced to each candidate
    action that was actually allowed at that decision (R5 decides afterwards); oracle cost = min over those runs.
    regret = policy cost − oracle cost. The oracle optimises the first decision only, so negative regret is possible
    and reported. Agreement counts a policy's first action as optimal when it is any action tied at the minimum."""
    if oracle.empty:
        return pd.DataFrame()
    o = oracle[oracle.get("forced_applied", 1) == 1] if "forced_applied" in oracle else oracle
    if o.empty:
        return pd.DataFrame()
    best = o.groupby(KEYS)[cost].min().rename("oracle_cost")
    tied = o.join(best, on=KEYS)
    tied = tied[tied[cost] <= tied["oracle_cost"] + 1e-6].groupby(KEYS)["forced_first"].apply(lambda s: set(s)).rename("oracle_actions")
    j = df.set_index(KEYS).join(best, how="inner").join(tied, how="left").reset_index()
    j["regret"] = j[cost] - j["oracle_cost"]
    j["agree"] = [float(fa in (acts or set())) for fa, acts in zip(j["first_action"], j["oracle_actions"])]
    return j.groupby("arm").agg(n=("regret", "size"), mean_regret=("regret", "mean"), median_regret=("regret", "median"),
                                share_nonneg=("regret", lambda s: float((s >= -1e-9).mean())), oracle_action_agreement=("agree", "mean")).reset_index()
