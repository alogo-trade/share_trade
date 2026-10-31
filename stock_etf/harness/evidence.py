from __future__ import annotations

from typing import Any


def validate_evidence_references(
    report: dict[str, Any],
    evidence: list[dict[str, Any]],
) -> list[str]:
    """Return validation errors for unsupported evidence references.

    Expected report shape:
      {"claims": [{"text": "...", "evidence_ids": ["E1"]}]}
    Expected evidence shape:
      [{"evidence_id": "E1", "source": "...", "published_at": "...",
        "url": "https://...", "text": "..."}]

    This checks reference integrity, not whether a claim is semantically true.
    Semantic verification requires a separate evaluator or human review.
    """
    errors: list[str] = []
    known = {
        str(item.get("evidence_id"))
        for item in evidence
        if item.get("evidence_id")
    }
    claims = report.get("claims")
    if not isinstance(claims, list):
        return ["report.claims must be a list"]

    for index, claim in enumerate(claims):
        if not isinstance(claim, dict):
            errors.append(f"claim[{index}] must be an object")
            continue
        if not str(claim.get("text", "")).strip():
            errors.append(f"claim[{index}] has empty text")
        ids = claim.get("evidence_ids")
        if not isinstance(ids, list) or not ids:
            errors.append(f"claim[{index}] has no evidence_ids")
            continue
        for evidence_id in ids:
            if evidence_id not in known:
                errors.append(
                    f"claim[{index}] references unknown evidence_id {evidence_id!r}"
                )
    return errors
