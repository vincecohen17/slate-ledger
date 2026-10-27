"""Odds math. Pure functions, no I/O."""
import math


def implied_from_decimal(d):
    return 1.0 / float(d)


def american_from_prob(p):
    p = float(p)
    if p <= 0 or p >= 1:
        return None
    return round(100.0 * (1 - p) / p) if p < 0.5 else round(-100.0 * p / (1 - p))


def american_from_decimal(d):
    d = float(d)
    if d <= 1:
        return None
    return round((d - 1) * 100) if d >= 2 else round(-100 / (d - 1))


def decimal_from_prob(p):
    return 1.0 / p


def no_vig(probs):
    """Multiplicative de-vig of a full set of outcome probabilities."""
    s = sum(probs)
    return [p / s for p in probs] if s > 0 else probs


def kalshi_fee(p):
    """Kalshi taker fee per $1 contract at price p (0-1): ceil(0.07*p*(1-p)*100)/100."""
    return math.ceil(round(0.07 * p * (1 - p) * 100, 6)) / 100.0


def polymarket_fee(p):
    return 0.05 * p * (1 - p)


FEES = {"kalshi": kalshi_fee, "polymarket": polymarket_fee}


def all_in_prob(price_prob, fee_model=None):
    """Cost of one $1-payout contract including fees = break-even probability."""
    if not fee_model:
        return price_prob
    return min(0.999, price_prob + FEES[fee_model](price_prob))


def ev_per_dollar(fair_p, cost_p):
    """Expected profit per $1 staked when paying cost_p for a fair_p outcome."""
    return fair_p / cost_p - 1.0


def kelly_fraction(fair_p, cost_p):
    b = 1.0 / cost_p - 1.0
    if b <= 0:
        return 0.0
    f = (b * fair_p - (1 - fair_p)) / b
    return max(0.0, f)
