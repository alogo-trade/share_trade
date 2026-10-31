from __future__ import annotations


def rank(records, limit=50):
    rows = [r for r in records if r.get("ranking_score") is not None]
    rows.sort(key=lambda r: (-float(r["ranking_score"]), -float(r.get("data_confidence") or 0), str(r.get("symbol", ""))))
    for i, row in enumerate(rows[:limit], 1):
        row["local_rank"] = i
    return rows[:limit]


def partition_ranked(rows):
    """
    Separate quantitative rank from decision-gate eligibility.

    Ranking answers "how strong is the quantitative score?".
    Eligibility answers "may this row be treated as actionable?".
    Only deterministic PASS rows are actionable; HOLD and REJECT remain
    visible in their own diagnostic buckets.
    """
    actionable, held, rejected, unevaluated = [], [], [], []
    for row in rows:
        verdict = str(row.get("decision_verdict") or "").upper()
        if verdict == "PASS" and row.get("eligible_for_llm", True) is not False:
            actionable.append(row)
        elif verdict == "HOLD":
            held.append(row)
        elif verdict == "REJECT":
            rejected.append(row)
        else:
            unevaluated.append(row)
    return {
        "actionable": actionable,
        "held": held,
        "rejected": rejected,
        "unevaluated": unevaluated,
    }


def diversify(rows, limit=20):
    out, sectors, types = [], {}, {}
    for row in rows:
        key = row.get("sector") or row.get("category") or "Unknown"
        kind = row.get("instrument_type", "STOCK")
        if sectors.get(key, 0) >= 4:
            continue
        if kind == "ETF" and types.get("ETF", 0) >= 8:
            continue
        out.append(row)
        sectors[key] = sectors.get(key, 0) + 1
        types[kind] = types.get(kind, 0) + 1
        if len(out) >= limit:
            break
    if len(out) < limit:
        seen = {row["symbol"] for row in out}
        for row in rows:
            if row["symbol"] not in seen:
                out.append(row)
            if len(out) >= limit:
                break
    return out
