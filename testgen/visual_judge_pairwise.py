"""Pair-wise VLM-as-Judge — compare TWO implementations against a reference.

Alternative to the point-wise :mod:`testgen.visual_judge`. Given a reference
(design) screenshot and two candidate implementations A, B, the judge picks
which one is closer to the reference. Ties are allowed.

Designed as a research module for the judge-method study; not wired into the
evaluator pipeline.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

Verdict = Literal["A", "B", "TIE"]

_PAIRWISE_PROMPT = """You are a professional UI design evaluator. Compare TWO candidate implementations (A and B) against the SAME reference design. Decide which candidate is closer to the reference.

Judge only **fidelity to the reference** — do NOT reward a candidate for looking aesthetically nicer if it drifts from the reference. Consider layout, style (colors/typography/decorations), content (text/images present), and consistency across the design.

Output ONLY a JSON object (no prose outside the JSON):
{
  "winner": "A" | "B" | "TIE",
  "confidence": "high" | "medium" | "low",
  "margin": "large" | "small" | "tie",
  "key_differences": ["<brief difference 1>", "<brief difference 2>"]
}

- Use "TIE" only when A and B are genuinely indistinguishable in fidelity.
- "margin" reflects how much closer the winner is: "large" = clearly better, "small" = marginal, "tie" = indistinguishable."""


def judge_pair_ab(
    reference_path: str | Path,
    impl_a_path: str | Path,
    impl_b_path: str | Path,
    model: str,
) -> dict[str, Any] | None:
    """Ask the VLM which of A or B is closer to the reference.

    Returns a dict with keys: winner ("A"/"B"/"TIE"), confidence, margin,
    key_differences. Returns None on unrecoverable failure.
    """
    try:
        from designforge.llm_client import completion
        from designforge.agents.base_agent import encode_image_for_message, extract_json

        ref_block = encode_image_for_message(str(reference_path))
        a_block = encode_image_for_message(str(impl_a_path))
        b_block = encode_image_for_message(str(impl_b_path))

        resp = completion(
            model=model,
            messages=[
                {"role": "system", "content": "You are a professional UI design evaluator."},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "REFERENCE (target design):"},
                        ref_block,
                        {"type": "text", "text": "CANDIDATE A:"},
                        a_block,
                        {"type": "text", "text": "CANDIDATE B:"},
                        b_block,
                        {"type": "text", "text": _PAIRWISE_PROMPT},
                    ],
                },
            ],
            max_tokens=512,
        )
        text = resp.choices[0].message.content
        logger.debug("Pairwise VLM raw response: %s", text)

        parsed = _parse_pairwise(text, extract_json)
        parsed["winner"] = _normalize_winner(parsed.get("winner"))
        return parsed

    except Exception as exc:
        logger.warning("Pairwise VLM call failed: %s", exc)
        return None


def judge_pair_with_swap(
    reference_path: str | Path,
    impl_x_path: str | Path,
    impl_y_path: str | Path,
    model: str,
    x_label: str = "X",
    y_label: str = "Y",
) -> dict[str, Any]:
    """Run judge_pair_ab twice — once with (X as A, Y as B), once swapped.

    Returns:
        {
          "x_label": "<model_x>",
          "y_label": "<model_y>",
          "forward":  {winner_in_ab, ...},    # X=A, Y=B
          "backward": {winner_in_ab, ...},    # Y=A, X=B
          "forward_winner": "<label>" | "TIE" | None,   # mapped to real labels
          "backward_winner": "<label>" | "TIE" | None,
          "consistent": bool,    # True if both calls agree on the real label
          "final_winner": "<label>" | "TIE",  # aggregated verdict
          "final_margin": "large" | "small" | "tie",
        }
    """
    forward = judge_pair_ab(reference_path, impl_x_path, impl_y_path, model)
    backward = judge_pair_ab(reference_path, impl_y_path, impl_x_path, model)

    def _to_real_label(verdict: dict[str, Any] | None, a_is: str, b_is: str) -> str | None:
        if verdict is None:
            return None
        w = verdict.get("winner")
        if w == "A":
            return a_is
        if w == "B":
            return b_is
        if w == "TIE":
            return "TIE"
        return None

    fwd_winner = _to_real_label(forward, x_label, y_label)
    bwd_winner = _to_real_label(backward, y_label, x_label)

    consistent = fwd_winner is not None and bwd_winner is not None and fwd_winner == bwd_winner

    # Aggregate verdict: if agreed, use that; if disagreed, downgrade to TIE.
    if consistent and fwd_winner != "TIE":
        final_winner = fwd_winner
        final_margin = _aggregate_margin(forward, backward)
    elif fwd_winner == "TIE" and bwd_winner == "TIE":
        final_winner = "TIE"
        final_margin = "tie"
    elif fwd_winner == "TIE" and bwd_winner is not None:
        final_winner = bwd_winner
        final_margin = "small"
    elif bwd_winner == "TIE" and fwd_winner is not None:
        final_winner = fwd_winner
        final_margin = "small"
    else:
        final_winner = "TIE"
        final_margin = "tie"

    return {
        "x_label": x_label,
        "y_label": y_label,
        "forward": forward,
        "backward": backward,
        "forward_winner": fwd_winner,
        "backward_winner": bwd_winner,
        "consistent": consistent,
        "final_winner": final_winner,
        "final_margin": final_margin,
    }


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _normalize_winner(w: Any) -> Verdict:
    if not isinstance(w, str):
        return "TIE"
    w = w.strip().upper()
    if w in ("A", "B", "TIE"):
        return w  # type: ignore[return-value]
    if w in ("CANDIDATE A", "A IS CLOSER", "A IS BETTER"):
        return "A"
    if w in ("CANDIDATE B", "B IS CLOSER", "B IS BETTER"):
        return "B"
    if "TIE" in w or "EQUAL" in w or "SAME" in w:
        return "TIE"
    return "TIE"


def _aggregate_margin(fwd: dict[str, Any] | None, bwd: dict[str, Any] | None) -> str:
    margins = [m.get("margin") for m in (fwd, bwd) if m]
    if "large" in margins:
        return "large"
    if "small" in margins:
        return "small"
    return "tie"


def _parse_pairwise(text: str, extract_json_fn) -> dict[str, Any]:
    """Parse pair-wise response with fallbacks."""
    try:
        return extract_json_fn(text)
    except Exception:
        pass

    cleaned = text
    first = cleaned.find("{")
    last = cleaned.rfind("}")
    if first != -1 and last > first:
        cleaned = cleaned[first : last + 1]
    cleaned = cleaned.replace("'", '"')
    cleaned = re.sub(r',\s*([}\]])', r'\1', cleaned)
    try:
        return json.loads(cleaned)
    except Exception:
        pass

    # Last-resort regex: look for 'winner: X'
    m = re.search(r'winner["\']?\s*:\s*["\']?([ABab]|TIE|Tie|tie)', text)
    if m:
        return {"winner": m.group(1).upper(), "confidence": "low", "margin": "small", "key_differences": []}

    logger.warning("Cannot parse pair-wise response, defaulting to TIE: %s", text[:200])
    return {"winner": "TIE", "confidence": "low", "margin": "tie", "key_differences": []}
