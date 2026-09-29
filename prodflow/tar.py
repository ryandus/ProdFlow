"""TAR validation statistics: elusion testing, recall estimation, sample sizing.

Intervals are exact Clopper-Pearson binomial intervals, the conservative choice when a
methodology may be challenged. Stdlib only, so every number can be reproduced by hand.
"""
import math
from statistics import NormalDist


def _binom_cdf(k, n, p):
    if p <= 0:
        return 1.0
    if p >= 1:
        return 1.0 if k >= n else 0.0
    lp, lq = math.log(p), math.log1p(-p)
    total = 0.0
    for i in range(k + 1):
        total += math.exp(math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1) + i * lp + (n - i) * lq)
    return min(total, 1.0)


def _bisect(fn, target, increasing):
    lo, hi = 0.0, 1.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if (fn(mid) < target) == increasing:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def clopper_pearson(k, n, confidence=0.95):
    if not 0 <= k <= n or n <= 0:
        raise ValueError("Require 0 <= k <= n and n > 0")
    a = (1 - confidence) / 2
    lower = 0.0 if k == 0 else _bisect(lambda p: 1 - _binom_cdf(k - 1, n, p), a, increasing=True)
    upper = 1.0 if k == n else _bisect(lambda p: _binom_cdf(k, n, p), a, increasing=False)
    return lower, upper


def sample_size(confidence=0.95, margin=0.02, population=None, p=0.5):
    """Simple random sample size for estimating a proportion, with optional finite-population correction."""
    if not (0 < confidence < 1 and 0 < margin < 1):
        raise ValueError("confidence and margin must be between 0 and 1")
    z = NormalDist().inv_cdf(1 - (1 - confidence) / 2)
    n0 = z * z * p * (1 - p) / (margin * margin)
    n = n0 / (1 + (n0 - 1) / population) if population else n0
    return math.ceil(n)


def elusion(null_set_size, sample_n, sample_responsive, responsive_found, confidence=0.95):
    """
    null_set_size      documents NOT produced/reviewed (the discard pile)
    sample_n           random sample drawn from the null set
    sample_responsive  responsive documents found in that sample
    responsive_found   responsive documents identified by the review
    """
    for name, v in (("null_set_size", null_set_size), ("sample_n", sample_n),
                    ("sample_responsive", sample_responsive), ("responsive_found", responsive_found)):
        if not isinstance(v, int) or v < 0:
            raise ValueError(f"{name} must be a non-negative integer")
    if sample_n > null_set_size:
        raise ValueError("sample_n cannot exceed null_set_size")
    lo, hi = clopper_pearson(sample_responsive, sample_n, confidence)
    rate = sample_responsive / sample_n
    missed = [rate * null_set_size, lo * null_set_size, hi * null_set_size]
    recall = lambda m: responsive_found / (responsive_found + m) if responsive_found + m else 0.0
    r6 = lambda x: round(x, 6)
    return {
        "inputs": {"null_set_size": null_set_size, "sample_n": sample_n, "sample_responsive": sample_responsive,
                   "responsive_found": responsive_found, "confidence": confidence},
        "elusion_rate": r6(rate), "elusion_ci": [r6(lo), r6(hi)],
        "est_missed": r6(missed[0]), "est_missed_ci": [r6(missed[1]), r6(missed[2])],
        "recall": r6(recall(missed[0])), "recall_ci": [r6(recall(missed[2])), r6(recall(missed[1]))],
        "margin_achieved": r6(max(rate - lo, hi - rate)),
    }


def memo(result, matter="[Matter]"):
    i, pct = result["inputs"], lambda x: f"{x * 100:.2f}%"
    c = f"{i['confidence'] * 100:g}%"
    return f"""# Elusion Test and Recall Estimate — {matter}

## Result
Estimated recall is **{pct(result['recall'])}** ({c} CI: {pct(result['recall_ci'][0])} – {pct(result['recall_ci'][1])}).
An estimated **{result['est_missed']:,.0f}** responsive documents remain in the null set
({c} CI: {result['est_missed_ci'][0]:,.0f} – {result['est_missed_ci'][1]:,.0f}).

## Inputs
| Input | Value |
|---|---|
| Null set (documents not produced) | {i['null_set_size']:,} |
| Simple random sample drawn from null set | {i['sample_n']:,} |
| Responsive documents found in sample | {i['sample_responsive']:,} |
| Responsive documents identified by review | {i['responsive_found']:,} |
| Confidence level | {c} |

## Method
1. A simple random sample of {i['sample_n']:,} documents was drawn from the null set.
2. Sample documents were reviewed blind to their predicted classification.
3. Elusion rate = responsive in sample / sample size = {i['sample_responsive']} / {i['sample_n']} = {pct(result['elusion_rate'])}.
   The interval ({pct(result['elusion_ci'][0])} – {pct(result['elusion_ci'][1])}) is an exact Clopper-Pearson binomial interval.
4. Estimated missed = elusion rate × null set size.
5. Recall = responsive identified / (responsive identified + estimated missed). The recall interval is obtained by
   substituting the elusion interval bounds.

## Limitations
- Assumes the sample was drawn uniformly at random from the entire null set.
- Treats the count of responsive documents identified by review as exact; reviewer error in that
  population is not modeled.
- No finite-population correction is applied to the interval, which makes it slightly conservative.
- Achieved margin of error on the elusion rate: ±{pct(result['margin_achieved'])}.

*Generated by prodflow. All figures are reproducible from the inputs above.*
"""
