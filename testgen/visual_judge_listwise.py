"""List-wise VLM-as-Judge — rank N candidates against a reference.

Given a reference design screenshot and N candidate implementations, the judge
returns a ranking (best → worst) by fidelity to the reference. An optional
*anchor* (the reference image itself re-inserted as a candidate) is used as a
sanity check — if the VLM does not rank the anchor first, the ranking is
considered unreliable.
"""

from __future__ import annotations

import json
import logging
import random
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


_LISTWISE_PROMPT_TEMPLATE = """You are a professional UI design evaluator. You will see one REFERENCE design and {n_candidates} candidate implementations labeled {label_list}.

Rank the candidates from MOST to LEAST faithful to the reference design. Judge fidelity only — do NOT reward a candidate for looking aesthetically nicer if it drifts from the reference. Consider layout, style (colors/typography/decorations), content (text/images present), and consistency.

Output ONLY a JSON object (no prose outside):
{{
  "ranking": [<label>, <label>, ...],        // all {n_candidates} labels, best → worst
  "confidence": "high" | "medium" | "low",
  "rationale": "<1-2 sentences summarizing main differences>"
}}

Constraint: ranking MUST contain each label exactly once."""


def rank_candidates(
    reference_path: str | Path,
    candidates: dict[str, str | Path],
    model: str,
    *,
    shuffle_seed: int | None = None,
    insert_anchor: bool = True,
    anchor_label: str = "REF",
) -> dict[str, Any] | None:
    """Ask the VLM to rank candidates from best to worst by fidelity to reference.

    Args:
        reference_path: Path to the reference (design) screenshot.
        candidates: Mapping of label -> implementation screenshot path.
        model: Judge VLM model name (passed to designforge.llm_client.completion).
        shuffle_seed: If provided, deterministically shuffles the order in which
            candidates are presented to the VLM. None -> random.
        insert_anchor: If True, insert the reference image itself as a
            hidden candidate under ``anchor_label``. If the returned ranking
            does not place the anchor first, ``anchor_passed`` will be False
            and the ranking (without the anchor) is flagged as unreliable.
        anchor_label: Label used for the anchor in the presented list.

    Returns:
        {
          "presented_order": [labels as shown to VLM],
          "ranking_raw": [labels as returned],        # may include anchor
          "ranking": [labels best->worst, anchor removed],
          "anchor_label": "REF" | None,
          "anchor_position": int | None,              # 0-indexed position in ranking_raw
          "anchor_passed": bool,                      # True iff anchor_position == 0
          "confidence": str,
          "rationale": str,
          "raw_response": str,
        }
        Returns None on unrecoverable failure.
    """
    try:
        from designforge.llm_client import completion
        from designforge.agents.base_agent import encode_image_for_message, extract_json

        items: list[tuple[str, str]] = [(k, str(v)) for k, v in candidates.items()]
        if insert_anchor:
            if anchor_label in candidates:
                raise ValueError(
                    f"anchor_label={anchor_label!r} collides with a candidate label"
                )
            items.append((anchor_label, str(reference_path)))

        rng = random.Random(shuffle_seed)
        rng.shuffle(items)

        ref_block = encode_image_for_message(str(reference_path))
        content: list[dict[str, Any]] = [
            {"type": "text", "text": "REFERENCE (target design):"},
            ref_block,
        ]
        for label, path in items:
            content.append({"type": "text", "text": f"CANDIDATE {label}:"})
            content.append(encode_image_for_message(path))

        all_labels = [lbl for lbl, _ in items]
        prompt = _LISTWISE_PROMPT_TEMPLATE.format(
            n_candidates=len(items),
            label_list=", ".join(all_labels),
        )
        content.append({"type": "text", "text": prompt})

        resp = completion(
            model=model,
            messages=[
                {"role": "system", "content": "You are a professional UI design evaluator."},
                {"role": "user", "content": content},
            ],
            max_tokens=2048,
        )
        text = resp.choices[0].message.content
        logger.debug("Listwise VLM raw response: %s", text)

        parsed = _parse_listwise(text, extract_json, expected_labels=all_labels)
        ranking_raw: list[str] = parsed.get("ranking", [])

        if insert_anchor:
            try:
                anchor_pos: int | None = ranking_raw.index(anchor_label)
            except ValueError:
                anchor_pos = None
            anchor_passed = anchor_pos == 0
            ranking_clean = [lbl for lbl in ranking_raw if lbl != anchor_label]
        else:
            anchor_pos = None
            anchor_passed = True
            ranking_clean = list(ranking_raw)

        return {
            "presented_order": all_labels,
            "ranking_raw": ranking_raw,
            "ranking": ranking_clean,
            "anchor_label": anchor_label if insert_anchor else None,
            "anchor_position": anchor_pos,
            "anchor_passed": anchor_passed,
            "confidence": parsed.get("confidence", "unknown"),
            "rationale": parsed.get("rationale", ""),
            "raw_response": text,
        }

    except Exception as exc:
        logger.warning("Listwise VLM call failed: %s", exc, exc_info=True)
        return None


def rank_candidates_k_shuffle(
    reference_path: str | Path,
    candidates: dict[str, str | Path],
    model: str,
    *,
    k: int = 3,
    seeds: list[int] | None = None,
    insert_anchor: bool = True,
    anchor_label: str = "REF",
) -> dict[str, Any]:
    """Run list-wise ranking ``k`` times with different shuffles, then
    aggregate via Borda count.

    Returns:
        {
          "trials": [list of per-shuffle outputs from rank_candidates],
          "anchor_pass_rate": float,
          "borda_scores": {label: float},        # higher is better
          "final_ranking": [labels best -> worst, only among trials that passed anchor],
          "pairwise_stability": float,           # avg Kendall tau across trial pairs
        }
    """
    if seeds is None:
        seeds = list(range(k))
    else:
        assert len(seeds) == k, "seeds length must match k"

    trials = []
    for seed in seeds:
        out = rank_candidates(
            reference_path=reference_path,
            candidates=candidates,
            model=model,
            shuffle_seed=seed,
            insert_anchor=insert_anchor,
            anchor_label=anchor_label,
        )
        trials.append(out)

    valid_trials = [t for t in trials if t is not None and t.get("anchor_passed")]
    anchor_pass_rate = (
        sum(1 for t in trials if t is not None and t.get("anchor_passed")) / len(trials)
        if trials else 0.0
    )

    labels = list(candidates.keys())
    borda: dict[str, float] = {lbl: 0.0 for lbl in labels}
    for t in valid_trials:
        ranking = t["ranking"]
        n = len(ranking)
        for i, lbl in enumerate(ranking):
            if lbl in borda:
                borda[lbl] += (n - 1 - i)

    if valid_trials:
        final_ranking = sorted(labels, key=lambda lbl: -borda[lbl])
    else:
        final_ranking = []

    # Pairwise Kendall tau between every pair of trials' rankings (anchor-passed only)
    taus = []
    for i in range(len(valid_trials)):
        for j in range(i + 1, len(valid_trials)):
            tau = _kendall_tau(valid_trials[i]["ranking"], valid_trials[j]["ranking"])
            if tau is not None:
                taus.append(tau)
    pairwise_stability = sum(taus) / len(taus) if taus else None

    return {
        "trials": trials,
        "anchor_pass_rate": anchor_pass_rate,
        "borda_scores": borda,
        "final_ranking": final_ranking,
        "pairwise_stability": pairwise_stability,
    }


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _parse_listwise(
    text: str,
    extract_json_fn,
    expected_labels: list[str],
) -> dict[str, Any]:
    """Parse list-wise response, enforcing ranking completeness where possible."""
    obj: dict[str, Any] | None = None
    try:
        obj = extract_json_fn(text)
    except Exception:
        pass

    if obj is None:
        cleaned = text
        first = cleaned.find("{")
        last = cleaned.rfind("}")
        if first != -1 and last > first:
            cleaned = cleaned[first : last + 1]
        cleaned = cleaned.replace("'", '"')
        cleaned = re.sub(r',\s*([}\]])', r'\1', cleaned)
        try:
            obj = json.loads(cleaned)
        except Exception:
            obj = None

    if not isinstance(obj, dict):
        obj = {}

    ranking = obj.get("ranking")
    if not isinstance(ranking, list) or not ranking:
        # Regex fallback 1: closed array
        m = re.search(r'ranking["\']?\s*:\s*\[(.*?)\]', text, re.DOTALL)
        ranking = []
        if m:
            ranking = re.findall(r'["\']([^"\']+)["\']', m.group(1))
        # Regex fallback 2: open (truncated) array — salvage whatever labels
        # were emitted before the response got cut off.
        if not ranking:
            m2 = re.search(r'ranking["\']?\s*:\s*\[(.*)$', text, re.DOTALL)
            if m2:
                ranking = re.findall(r'["\']([^"\']+)["\']', m2.group(1))

    # Normalize: keep only known labels, de-dup preserving order.
    expected_set = set(expected_labels)
    seen: set[str] = set()
    norm_ranking: list[str] = []
    for lbl in ranking:
        if isinstance(lbl, str) and lbl in expected_set and lbl not in seen:
            norm_ranking.append(lbl)
            seen.add(lbl)

    # If some labels are missing, append them in original presentation order
    # (marks them as 'unranked', consumers can detect this via length).
    missing = [lbl for lbl in expected_labels if lbl not in seen]
    if missing:
        logger.warning(
            "List-wise response missing %d labels; appending at tail: %s",
            len(missing), missing,
        )
        norm_ranking.extend(missing)

    return {
        "ranking": norm_ranking,
        "confidence": obj.get("confidence", "unknown"),
        "rationale": obj.get("rationale", ""),
        "missing_count": len(missing),
    }


def _kendall_tau(ranking_a: list[str], ranking_b: list[str]) -> float | None:
    """Kendall rank correlation between two rankings over the same label set.

    Returns tau in [-1, 1], or None if rankings don't share labels.
    """
    common = [lbl for lbl in ranking_a if lbl in ranking_b]
    if len(common) < 2:
        return None
    idx_b = {lbl: i for i, lbl in enumerate(ranking_b)}
    concordant = 0
    discordant = 0
    for i in range(len(common)):
        for j in range(i + 1, len(common)):
            ai, aj = i, j  # ranking_a order by construction
            bi, bj = idx_b[common[i]], idx_b[common[j]]
            if (aj - ai) * (bj - bi) > 0:
                concordant += 1
            else:
                discordant += 1
    total = concordant + discordant
    if total == 0:
        return None
    return (concordant - discordant) / total
