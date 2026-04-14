from __future__ import annotations

from dataclasses import dataclass
from typing import Any


_CATEGORY_SCORE_BASELINES = {
    "real_estate": 72.0,
    "commodity": 65.0,
    "invoice": 69.0,
    "art": 58.0,
    "other": 55.0,
}

_CATEGORY_ROI_BASELINES = {
    "real_estate": 6.4,
    "commodity": 7.1,
    "invoice": 8.8,
    "art": 5.2,
    "other": 6.0,
}

_POSITIVE_KEYWORDS = (
    "audited",
    "insured",
    "leased",
    "occupied",
    "verified",
    "recurring revenue",
)

_NEGATIVE_KEYWORDS = (
    "default",
    "dispute",
    "lawsuit",
    "vacant",
    "volatile",
    "delinquent",
)


@dataclass(frozen=True)
class AssetEvaluationResult:
    ai_score: float
    ai_analysis: dict[str, Any]
    projected_roi: float
    status: str


def _row_value(row: object, key: str, default: Any = None) -> Any:
    if row is None:
        return default

    mapping = getattr(row, "_mapping", None)
    if mapping is not None and key in mapping:
        return mapping[key]

    return getattr(row, key, default)


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _valuation_modifier(valuation_sat: int) -> float:
    if valuation_sat <= 5_000_000:
        return 6.0
    if valuation_sat <= 25_000_000:
        return 3.0
    if valuation_sat <= 150_000_000:
        return 0.0
    if valuation_sat <= 500_000_000:
        return -4.0
    return -8.0


def _risk_level(score: float) -> str:
    if score >= 82:
        return "low"
    if score >= 65:
        return "moderate"
    return "high"


def _market_timing(score: float) -> str:
    if score >= 75:
        return "favorable"
    if score >= 60:
        return "watchlist"
    return "cautious"


def _summary(
    *,
    category: str,
    score: float,
    risk_level: str,
    market_timing: str,
    projected_roi: float,
) -> str:
    category_label = category.replace("_", " ")
    return (
        f"The {category_label} submission scored {score:.2f}/100 with {risk_level} risk, "
        f"{market_timing} market timing, and an estimated annual ROI of {projected_roi:.2f}%."
    )


def evaluate_asset_submission(row: object) -> AssetEvaluationResult:
    name = str(_row_value(row, "name", "")).strip()
    description = str(_row_value(row, "description", "")).strip()
    category = str(_row_value(row, "category", "other"))
    valuation_sat = int(_row_value(row, "valuation_sat", 0))
    documents_url = _row_value(row, "documents_url")

    text = f"{name} {description}".lower()
    positive_hits = sorted({keyword for keyword in _POSITIVE_KEYWORDS if keyword in text})
    negative_hits = sorted({keyword for keyword in _NEGATIVE_KEYWORDS if keyword in text})

    description_word_count = len(description.split())
    documentation_score = 8.0 if documents_url else -12.0
    description_score = min(description_word_count * 0.55, 12.0)
    title_score = min(len(name) / 18.0, 4.0)
    positive_modifier = min(len(positive_hits) * 2.5, 8.0)
    negative_modifier = min(len(negative_hits) * 3.5, 14.0)

    raw_score = (
        _CATEGORY_SCORE_BASELINES.get(category, _CATEGORY_SCORE_BASELINES["other"])
        + documentation_score
        + description_score
        + title_score
        + positive_modifier
        - negative_modifier
        + _valuation_modifier(valuation_sat)
    )
    ai_score = round(_clamp(raw_score, 0.0, 100.0), 2)

    projected_roi = round(
        _clamp(
            _CATEGORY_ROI_BASELINES.get(category, _CATEGORY_ROI_BASELINES["other"])
            + ((ai_score - 60.0) / 7.5)
            + (len(positive_hits) * 0.25)
            - (len(negative_hits) * 0.35),
            1.5,
            24.0,
        ),
        2,
    )

    risk_level = _risk_level(ai_score)
    market_timing = _market_timing(ai_score)
    status = "approved" if ai_score >= 70.0 else "rejected"

    strengths: list[str] = []
    concerns: list[str] = []

    if documents_url:
        strengths.append("Documentation link is available for diligence review.")
    else:
        concerns.append("Submission is missing a supporting documentation link.")

    if description_word_count >= 25:
        strengths.append("Submission includes enough detail for automated screening.")
    else:
        concerns.append("Submission description is thin and increases diligence risk.")

    if positive_hits:
        strengths.append(
            "Positive quality indicators detected: " + ", ".join(positive_hits) + "."
        )

    if negative_hits:
        concerns.append(
            "Risk indicators detected: " + ", ".join(negative_hits) + "."
        )

    if valuation_sat >= 500_000_000:
        concerns.append("Large valuation introduces additional concentration risk.")
    elif valuation_sat <= 25_000_000:
        strengths.append("Valuation range supports a more conservative entry profile.")

    ai_analysis: dict[str, Any] = {
        "model_version": "heuristic-v1",
        "risk_level": risk_level,
        "market_timing": market_timing,
        "projected_roi_annual": projected_roi,
        "summary": _summary(
            category=category,
            score=ai_score,
            risk_level=risk_level,
            market_timing=market_timing,
            projected_roi=projected_roi,
        ),
        "strengths": strengths,
        "concerns": concerns,
        "score_breakdown": {
            "category_baseline": _CATEGORY_SCORE_BASELINES.get(
                category,
                _CATEGORY_SCORE_BASELINES["other"],
            ),
            "documentation": documentation_score,
            "description_depth": round(description_score, 2),
            "title_quality": round(title_score, 2),
            "valuation_risk": _valuation_modifier(valuation_sat),
            "positive_indicators": positive_modifier,
            "negative_indicators": -negative_modifier,
        },
    }

    return AssetEvaluationResult(
        ai_score=ai_score,
        ai_analysis=ai_analysis,
        projected_roi=projected_roi,
        status=status,
    )
