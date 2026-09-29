"""Matter volume and cost estimator. Every assumption is explicit and overridable; none are vendor quotes."""
import json

# Illustrative planning ranges. Replace with your vendor's pricing and your own historical metrics.
DEFAULTS = {
    "docs_per_gb":            {"low": 4000,  "high": 6000},
    "dedupe_reduction":       {"low": 0.30,  "high": 0.20},
    "culling_reduction":      {"low": 0.65,  "high": 0.50},
    "review_docs_per_hour":   {"low": 55,    "high": 45},
    "reviewer_rate_per_hour": {"low": 60,    "high": 80},
    "qc_overhead":            {"low": 0.10,  "high": 0.20},
    "processing_per_gb":      {"low": 30,    "high": 75},
    "hosting_per_gb_month":   {"low": 6,     "high": 12},
}


def load_assumptions(path=None):
    a = {k: dict(v) for k, v in DEFAULTS.items()}
    if path:
        with open(path, encoding="utf-8") as f:
            override = json.load(f)
        unknown = set(override) - set(DEFAULTS)
        if unknown:
            raise ValueError(f"Unknown assumptions: {sorted(unknown)}")
        for k, v in override.items():
            if set(v) != {"low", "high"}:
                raise ValueError(f"{k} must have exactly 'low' and 'high'")
            a[k] = v
    return a


def estimate(gb, months=6, assumptions=None):
    if gb <= 0 or months < 0:
        raise ValueError("gb must be > 0 and months >= 0")
    a = assumptions or load_assumptions()
    out = {}
    for s in ("low", "high"):
        v = {k: a[k][s] for k in a}
        collected = gb * v["docs_per_gb"]
        after_dedupe = collected * (1 - v["dedupe_reduction"])
        review_set = after_dedupe * (1 - v["culling_reduction"])
        hours = review_set / v["review_docs_per_hour"] * (1 + v["qc_overhead"])
        hosted_gb = gb * (1 - v["dedupe_reduction"])
        cost = {
            "processing": gb * v["processing_per_gb"],
            "hosting": hosted_gb * v["hosting_per_gb_month"] * months,
            "review": hours * v["reviewer_rate_per_hour"],
        }
        cost["total"] = sum(cost.values())
        out[s] = {"docs_collected": round(collected), "docs_after_dedupe": round(after_dedupe),
                  "docs_to_review": round(review_set), "review_hours": round(hours),
                  "cost": {k: round(x) for k, x in cost.items()}}
    return {"inputs": {"gb": gb, "months": months}, "assumptions": a, "scenarios": out}


def render_markdown(r):
    lo, hi = r["scenarios"]["low"], r["scenarios"]["high"]
    rows = [("Documents collected", "docs_collected"), ("After de-duplication", "docs_after_dedupe"),
            ("Review set after culling", "docs_to_review"), ("Review hours (incl. QC)", "review_hours")]
    out = [f"# Matter Estimate — {r['inputs']['gb']:,} GB, {r['inputs']['months']} months hosting", "",
           "| Measure | Low | High |", "|---|---:|---:|"]
    out += [f"| {label} | {lo[k]:,} | {hi[k]:,} |" for label, k in rows]
    out += [f"| {k.title()} cost | ${lo['cost'][k]:,} | ${hi['cost'][k]:,} |" for k in ("processing", "hosting", "review")]
    out += [f"| **Total** | **${lo['cost']['total']:,}** | **${hi['cost']['total']:,}** |", "",
            "## Assumptions", "", "| Assumption | Low | High |", "|---|---:|---:|"]
    out += [f"| {k} | {v['low']} | {v['high']} |" for k, v in r["assumptions"].items()]
    out += ["", "Planning ranges only; not a vendor quote. Low and High apply every assumption's favorable or unfavorable end together, "
            "so the spread is a bounding range, not a confidence interval. Override with `--assumptions file.json`."]
    return "\n".join(out) + "\n"
