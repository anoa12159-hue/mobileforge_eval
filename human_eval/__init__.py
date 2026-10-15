"""Human evaluation web platform for VLM consistency review.

The dashboard supports two parallel evaluation modes, switchable via tabs in
the UI. Saved files use distinct filename prefixes so the two reviews never
collide in ``<run_dir>/human-eval/`` — even when both happen to use the same
underlying VLM model name.

Click-model consistency (``vlm_click_model__<vlm>.json``)
    Lets a human re-judge what the ``vlm_click_model`` (the VLM that picks
    click targets during ``navigation_test``) got right or wrong on each test
    case, and computes the human↔click-model agreement rate.

Visual-judge consistency (``visual_judge_model__<judge>.json``)
    Lets a human re-rate each (design reference, implementation screenshot)
    pair on the same four dimensions used by the auto judge — layout (30%),
    style (25%), content (25%), consistency (20%) — and computes
    per-dimension MAE and exact-match rate against the auto judge's scores.

Provides a single-page web UI that:
  1. Lists all runs in an experiment directory.
  2. Auto-launches the run's built `dist/` as a local SPA preview server.
  3. Shows test cases with relevant context (VLM verdict + screenshots in
     click mode; design vs impl image pairs in visual mode) side by side
     with a live iframe of the running app.
  4. Lets a human label each case and save the result back into
     ``<run_dir>/human-eval/<prefix><vlm>.json``.

Entry point:
    python -m human_eval <experiment_dir> [--port 8090] [--host 0.0.0.0]
"""
