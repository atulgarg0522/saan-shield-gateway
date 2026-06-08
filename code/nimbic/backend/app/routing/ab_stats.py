import numpy as np
from typing import List, Dict, Any, Union
from decimal import Decimal
from dataclasses import dataclass

@dataclass
class StatResult:
    significant: bool
    p_value: float
    confidence: float
    requests_needed: int
    winner: str
    effect_size: float

def calculate_significance(results_a: List[Union[float, Decimal]], results_b: List[Union[float, Decimal]]) -> StatResult:
    # Convert Decimals to floats
    a = np.array([float(x) for x in results_a])
    b = np.array([float(x) for x in results_b])

    n_a = len(a)
    n_b = len(b)

    # Handle edge case: empty or small samples
    if n_a < 3 or n_b < 3:
        return StatResult(
            significant=False,
            p_value=1.0,
            confidence=0.0,
            requests_needed=max(30 - (n_a + n_b), 10),
            winner="inconclusive",
            effect_size=0.0
        )

    mean_a = np.mean(a)
    mean_b = np.mean(b)
    var_a = np.var(a, ddof=1)
    var_b = np.var(b, ddof=1)

    # Ensure variance is non-zero
    if var_a == 0 and var_b == 0:
        var_a = 1e-9
        var_b = 1e-9

    # Welch's t-test calculation
    try:
        from scipy import stats
        t_stat, p_val = stats.ttest_ind(a, b, equal_var=False)
        # Handle NaN p_value
        if np.isnan(p_val):
            p_val = 1.0
    except Exception:
        # Fallback to manual Welch's t-test with Normal approximation if Scipy fails
        t_stat = (mean_a - mean_b) / np.sqrt((var_a / n_a) + (var_b / n_b))
        # Simple normal approximation for p-value (two-tailed)
        import math
        # Standard Normal CDF approximation
        def phi(x):
            return (1.0 + math.erf(x / math.sqrt(2.0))) / 2.0
        p_val = 2.0 * (1.0 - phi(abs(t_stat)))

    significant = p_val < 0.05
    confidence = 1.0 - p_val

    # Winner: cheaper model is better
    if significant:
        winner = "A" if mean_a < mean_b else "B"
    else:
        winner = "inconclusive"

    # Effect size: % difference relative to A
    diff = mean_b - mean_a
    denom = mean_a if mean_a > 0 else 1.0
    effect_size = (diff / denom) * 100.0

    # Power analysis: estimate requests needed for 80% power (alpha=0.05, beta=0.20 => standard multiplier is ~15.7)
    # n = 2 * (1.96 + 0.84)^2 * s^2 / delta^2 = 15.68 * s^2 / delta^2
    effect_diff = abs(mean_a - mean_b)
    pooled_var = (var_a + var_b) / 2.0

    if effect_diff > 1e-7:
        # Total requests needed per variant
        n_needed_per_variant = (15.68 * pooled_var) / (effect_diff ** 2)
        total_needed = int(n_needed_per_variant * 2)
        requests_needed = max(0, total_needed - (n_a + n_b))
    else:
        requests_needed = 200

    # Cap requests needed at 10,000 to keep UI stats sane
    requests_needed = min(requests_needed, 10000)
    if significant:
        requests_needed = 0

    return StatResult(
        significant=bool(significant),
        p_value=float(p_val),
        confidence=float(confidence),
        requests_needed=int(requests_needed),
        winner=winner,
        effect_size=float(effect_size)
    )
