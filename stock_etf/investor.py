from __future__ import annotations


def build_profile(
    age: int,
    capital_inr: float,
    horizon: str,
) -> dict:
    """
    Build the investor profile used by the research engine.

    The profile controls position-sizing context only.
    It does not alter the underlying market score.
    """

    age = int(age)
    capital_inr = float(
        capital_inr
    )

    if not 18 <= age <= 100:
        raise ValueError(
            "Age must be between 18 and 100."
        )

    if capital_inr <= 0:
        raise ValueError(
            "Capital must be greater than zero."
        )

    horizon_share = {
        "SHORT_TERM": 0.15,
        "MID_TERM": 0.40,
        "LONG_TERM": 0.70,
    }.get(
        horizon,
        0.40,
    )

    strategy_capital = (
        capital_inr
        * horizon_share
    )

    return {
        "age": age,

        "capital_inr": round(
            capital_inr,
            2,
        ),

        "horizon_capital_inr": round(
            strategy_capital,
            2,
        ),

        "horizon_capital_share_pct": round(
            horizon_share * 100,
            1,
        ),

        "note": (
            "Capital is used for position-sizing context. "
            "It does not guarantee returns or change the "
            "underlying market score."
        ),
    }


def add_position_context(
    rows: list[dict],
    profile: dict,
) -> None:
    """
    Add suggested portfolio allocation context.

    The allocation is proportional to the positive ranking score
    of the eligible instruments.

    Because the engine applies portfolio-aware scoring before this
    function is called, the suggested capital naturally reflects
    portfolio-adjusted ranking when Zerodha holdings are available.

    The original market score remains available separately as
    `market_ranking_score`.
    """

    eligible = [
        row
        for row in rows
        if row.get("ranking_score") is not None
        and row.get("decision_verdict") == "PASS"
        and row.get("signal") in {"SETUP", "ACCUMULATE", "QUALITY_CANDIDATE"}
    ]

    weights = []

    for row in eligible:
        try:
            score = max(
                0.0,
                float(
                    row.get(
                        "ranking_score"
                    )
                    or 0.0
                ),
            )
        except (
            TypeError,
            ValueError,
        ):
            score = 0.0

        weights.append(
            score
        )

    total = sum(
        weights
    )

    for row in rows:
        try:
            score = max(
                0.0,
                float(
                    row.get(
                        "ranking_score"
                    )
                    or 0.0
                ),
            )
        except (
            TypeError,
            ValueError,
        ):
            score = 0.0

        is_eligible = (
            row.get("decision_verdict") == "PASS"
            and row.get("signal") in {"SETUP", "ACCUMULATE", "QUALITY_CANDIDATE"}
        )
        weight = score / total if total and is_eligible else 0.0

        row[
            "suggested_weight_pct"
        ] = round(
            weight * 100,
            2,
        )

        row[
            "capital_context_inr"
        ] = round(
            profile[
                "horizon_capital_inr"
            ]
            * weight,
            2,
        )

        # Preserve the raw market score explicitly.
        #
        # This is useful in the UI because a stock can have:
        #
        #     high market score
        #     but
        #     lower portfolio-adjusted score
        #
        # due to existing exposure.
        if row.get(
            "market_ranking_score"
        ) is None:
            row[
                "market_ranking_score"
            ] = row.get(
                "ranking_score"
            )

        if row.get(
            "portfolio_adjusted_score"
        ) is None:
            row[
                "portfolio_adjusted_score"
            ] = row.get(
                "ranking_score"
            )