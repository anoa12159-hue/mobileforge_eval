"""Resolve page_name → actual route via a two-tier strategy.

Tier 1 (preferred, when ``tab_structure`` is provided)
    Locate the tabbar component file under ``src/components/*.tsx``,
    extract its ordered ``path`` literals, and match against the
    ordered ``tab_structure`` from metadata.

    - ``|paths| == |tab_structure|``: direct positional alignment. The
      annotator's tab order and the tabbar source order are both
      authoritative, so this is near-100% correct.
    - ``|paths|  > |tab_structure|``: Ordered Subset DP (LCS-style)
      finds which ``M − N`` paths to skip while preserving order,
      guided by IDF-weighted Dice similarity as a tie-breaker. This is
      the common case: the model usually implements more tabs than
      the annotation (e.g. extra ``/cart`` or ``/create`` modal
      triggers that annotators treat as non-pages).
    - Tabbar file not found, zero paths extracted, or ``|paths| < |tab_structure|``:
      fall through to Tier 2.

Tier 2 (fallback, router.tsx global matching)
    Parse ``router.tsx`` for ``{component: route}`` pairs and run
    Hungarian assignment on IDF-weighted Dice similarity. This is
    also used for non-tab page_names (``parent_child`` transitions
    etc.) whose routes are not in the tabbar.

Design notes
    - Bijection is enforced in all tiers.
    - ``tab_structure`` order is the sole signal in Tier 1 for ties;
      semantic similarity is a tie-breaker, not the primary signal,
      because page_name tokens and tabbar path tokens can disagree
      substantially (e.g. ``ai_assistant_home`` vs ``/grok``).
"""

from __future__ import annotations

import logging
import math
import re
from difflib import SequenceMatcher
from pathlib import Path

logger = logging.getLogger(__name__)

_ROUTE_ENTRY_RE = re.compile(
    r"""path\s*:\s*['"]([^'"]+)['"]"""
    r""".*?"""
    r"""element\s*:\s*<\s*(\w+)""",
    re.DOTALL,
)


# ═══════════════════════════════════════════════════════════════════════════
# Router parsing
# ═══════════════════════════════════════════════════════════════════════════


def parse_route_map(router_path: Path) -> dict[str, str]:
    """Parse router.tsx and return {component_name: route_path}.

    Example return: {"ReadingDiscover": "/", "Bookshelf": "/bookshelf"}
    """
    if not router_path.is_file():
        return {}
    text = router_path.read_text(encoding="utf-8")
    result: dict[str, str] = {}
    for match in _ROUTE_ENTRY_RE.finditer(text):
        route_path, component_name = match.group(1), match.group(2)
        if not route_path.startswith("/"):
            route_path = f"/{route_path}"
        result[component_name] = route_path
    return result


# ═══════════════════════════════════════════════════════════════════════════
# Tokenization + IDF
# ═══════════════════════════════════════════════════════════════════════════


_PARAM_RE = re.compile(r":\w+|\*")
_CAMEL_LOW_UP_RE = re.compile(r"([a-z\d])([A-Z])")
_CAMEL_UP_UP_LOW_RE = re.compile(r"([A-Z]+)([A-Z][a-z])")
_SPLIT_RE = re.compile(r"[_\-/\s]+")

_STOPWORDS: frozenset[str] = frozenset({
    "a", "an", "and", "by", "for", "from", "in", "of", "on", "or",
    "the", "to", "with", "you", "your", "my", "me", "us", "our", "at",
    "is", "be",
    "page", "screen", "view", "component",
})
"""Semantic noise tokens that should not drive matches. Without this
filter ``NewsForYouPage`` contributes ``{news, for, you, page}`` which
inflates its token-set size and cause ``news_feed`` to lose to shorter
components like ``NewsMagazinesPage`` under Dice similarity. The list
deliberately covers both English stopwords and common React-component
suffixes (``Page``, ``Screen``, ``View``)."""

_MIN_CONFIDENCE = 0.15
"""Minimum similarity score required to accept an assignment. Below this,
the page falls back to a naive ``page_name → /page-name`` guess instead
of being forcibly married to an unrelated component."""


def _tokenize(name: str) -> list[str]:
    """Split an identifier into lowercase meaningful tokens.

    Handles snake_case, kebab-case, CamelCase, and slash-separated paths.
    Strips route parameter placeholders (``:id``, ``*``), 1-char junk,
    and semantic stopwords (``page``, ``for``, ``you``, …) that would
    otherwise dilute IDF.
    """
    if not name:
        return []
    s = _PARAM_RE.sub(" ", name)
    s = _CAMEL_LOW_UP_RE.sub(r"\1_\2", s)
    s = _CAMEL_UP_UP_LOW_RE.sub(r"\1_\2", s)
    s = s.lower()
    return [
        t for t in _SPLIT_RE.split(s)
        if len(t) >= 2 and t not in _STOPWORDS
    ]


def _compute_idf(docs: list[set[str]]) -> dict[str, float]:
    """Compute IDF per token over a document corpus.

    Uses ``log((N + 1) / (df + 1))``: universal tokens get IDF ~= 0
    (so they contribute nothing to Jaccard), rare tokens get IDF > 0.
    """
    if not docs:
        return {}
    n = len(docs)
    df: dict[str, int] = {}
    for doc in docs:
        for t in doc:
            df[t] = df.get(t, 0) + 1
    return {t: math.log((n + 1) / (c + 1)) for t, c in df.items()}


def _weighted_dice(a: set[str], b: set[str], idf: dict[str, float]) -> float:
    """IDF-weighted Dice coefficient: 2 * shared / (a + b).

    Symmetric and penalizes size mismatches: a page with 2 tokens matching
    a 1-token component (or vice versa) gets a lower score than when both
    sides match in size. This keeps ``profile ↔ Profile`` strictly higher
    than ``profile ↔ ProfileShow`` even when every page token is covered.

    Preferred over Jaccard because IDF-weighted Jaccard can be gamed by
    components whose only extra tokens are low-frequency rare words, and
    preferred over bare containment because containment rewards unfairly
    short components.
    """
    if not a or not b:
        return 0.0
    shared = a & b
    if not shared:
        return 0.0
    shared_w = sum(idf.get(t, 0.0) for t in shared)
    a_w = sum(idf.get(t, 0.0) for t in a)
    b_w = sum(idf.get(t, 0.0) for t in b)
    denom = a_w + b_w
    if denom <= 0:
        return 0.0
    return 2.0 * shared_w / denom


def _route_depth(route: str) -> int:
    """Count the number of non-parameter path segments (``/`` counts as 1)."""
    segments = [
        s for s in route.strip("/").split("/") if s and not s.startswith(":")
    ]
    return max(1, len(segments))


def _depth_factor(route: str, strength: float = 0.2) -> float:
    """Small monotonic penalty on deep routes: 1.0 for depth 1, 0.83 for 2, …

    Shallow routes (``/foo``) are usually main/tab pages; deep routes
    (``/foo/bar``) are usually sub-pages. Page names without sub-page
    markers tend to live at depth 1, so a gentle prior helps tie-break
    cases like ``news_feed`` between ``/news`` vs ``/news/magazines``.
    """
    return 1.0 / (1.0 + strength * (_route_depth(route) - 1))


# ═══════════════════════════════════════════════════════════════════════════
# Tier 1: tabbar source scan + ordered matching
# ═══════════════════════════════════════════════════════════════════════════


_TABBAR_NAME_RE = re.compile(
    r"(bottom|tabbar|tab[-_]bar|tabnav|tab[-_]nav|footernav|navbar|bottomnavigation|navigation|navmenu|nav[-_]menu)",
    re.IGNORECASE,
)
"""Candidate tabbar filenames. Permissive on ``nav``/``navigation``
because some models (e.g. ``Navigation.tsx`` for naver) drop the
``Bottom``/``Tab`` prefix entirely. False positives like
``TopNavigation.tsx`` are caught by ``_TABBAR_EXCLUDE_RE``."""

_TABBAR_EXCLUDE_RE = re.compile(
    r"(topnav|topbar|statusbar|searchbar|pageheader|modal|sheet|sidenav|side[-_]nav|drawer)",
    re.IGNORECASE,
)
_PATH_LITERAL_RE = re.compile(
    r"""(?:path|route|url|link|to|href)\s*[=:]\s*\{?\s*['"](/[^'"]*)['"]"""
    r"""|(?:navigate|push|replace)\s*\(\s*['"](/[^'"]*)['"]"""
)
"""Matches path literals in two common tabbar-file patterns:

1. As an attribute or object property value. Covers JSX attributes
   (``to="/foo"``, ``href="/foo"``), object entries (``path: "/foo"``,
   ``route: "/foo"``), and wrapped expressions (``to={"/foo"}``).
   The keyword list includes both standard React-Router props
   (``to``, ``path``) and ad-hoc property names models pick
   (``route``, ``url``, ``link``).

2. As the first argument to a navigation function. Covers
   ``navigate("/foo")``, ``push("/foo")``, ``replace("/foo")`` —
   which is how ``useNavigate``-based tabbars encode tab routes
   instead of using ``<Link>``.

The alternation yields two capture groups; callers must take
whichever is non-empty per match.
"""

_ANY_PATH_LITERAL_RE = re.compile(r"""['"](/[^'"\s]*)['"]""")
"""Fallback used only when the structured regex finds nothing.

Catches cases like ``item("Home", "/", "⌂")`` where paths are
positional arguments with no identifying keyword. Safe inside tabbar
files because they are small and rarely contain stray path literals
other than tab destinations; still a last-resort since it could pick
up a random route from a comment or aria-label."""


def _find_tabbar_file(run_dir: Path) -> Path | None:
    """Locate the tabbar component under ``<run_dir>/src/components/``.

    Filters ``*.tsx`` files by name (``Bottom*``, ``*TabBar*``, ``NavBar``,
    …) and excludes obvious non-tabbar components (top bars, search bars,
    page headers, modals). Among the survivors, picks the one with the
    most path-literal hits — a tabbar that renders tabs inline will win
    over a generic wrapper that forwards props. Returns ``None`` when
    no candidate exists.
    """
    components_dir = run_dir / "src" / "components"
    if not components_dir.is_dir():
        return None
    candidates = [
        f for f in components_dir.glob("*.tsx")
        if not _TABBAR_EXCLUDE_RE.search(f.stem)
        and _TABBAR_NAME_RE.search(f.stem)
    ]
    if not candidates:
        return None

    def _path_hits(f: Path) -> int:
        try:
            return len(_PATH_LITERAL_RE.findall(f.read_text("utf-8", errors="ignore")))
        except OSError:
            return 0

    candidates.sort(key=_path_hits, reverse=True)
    return candidates[0]


def _extract_tabbar_paths(tabbar_file: Path) -> list[str]:
    """Extract ordered unique path literals from a tabbar source file.

    Primary: the structured regex covers keyword patterns
    (``to="/x"``, ``route: "/x"``, …) and navigation calls
    (``navigate("/x")``). Because it uses alternation, each match
    yields a tuple where exactly one group is non-empty; we pick
    whichever is set.

    Fallback: if the structured regex finds nothing, run the
    permissive ``['"]/...['"]`` regex to catch models that embed
    paths as positional arguments (``item("Home", "/", icon)``).
    This is only safe inside tabbar files — which are small and
    typically contain only tab paths — and is intentionally the
    last resort to avoid false positives.
    """
    try:
        text = tabbar_file.read_text("utf-8", errors="ignore")
    except OSError:
        return []

    seen: set[str] = set()
    ordered: list[str] = []
    for groups in _PATH_LITERAL_RE.findall(text):
        p = next((g for g in groups if g), "") if isinstance(groups, tuple) else groups
        if p and p not in seen:
            seen.add(p)
            ordered.append(p)
    if ordered:
        return ordered

    for p in _ANY_PATH_LITERAL_RE.findall(text):
        if p not in seen:
            seen.add(p)
            ordered.append(p)
    return ordered


_FIRST_TAB_ROOT_BONUS = 0.01
"""Tiny prior for matching ``tab_structure[0] ↔ "/"``.

Empirically the first annotated tab is always the "home" tab, and the
root route ``/`` is present as a tabbar path in virtually every real
mobile app. When the DP faces an all-zero-similarity prefix (which
happens whenever page_name tokens and tabbar path tokens don't
lexically overlap — e.g. ``ai_assistant_home`` vs ``/grok``), this
bonus is the difference between "skip ``/`` and drift right" vs "skip
a middle path". Small enough to never override real semantic signal,
large enough to break ties correctly."""


def _ordered_subset_match(
    page_names: list[str],
    tabbar_paths: list[str],
    idf: dict[str, float],
) -> dict[str, str]:
    """Map N page_names to N of M ≥ N tabbar paths, preserving order.

    Dynamic programming over ``(i, j)`` = "matched first i page_names
    within first j paths". Two transitions:

      - skip path j:            ``dp[i][j] = dp[i][j-1]``
      - match page_i to path_j: ``dp[i][j] = dp[i-1][j-1] + sim(i, j)``

    ``sim`` is IDF-weighted Dice × depth_factor, plus a small bonus
    for aligning ``tab_structure[0]`` with ``/`` when that path exists
    (see ``_FIRST_TAB_ROOT_BONUS``). Order is a hard constraint, so
    similarity only breaks ties between skip candidates — and the
    root bonus fixes the common "first tab is ``/`` but has no
    matching tokens" edge case.

    Ties are broken by preferring to skip (strict ``match > skip``).
    This keeps matches contiguous with the earliest paths when scores
    are equal, which is empirically correct: mobile apps typically
    place modal-trigger tabs (``/create``, ``/compose``, ``/add``) in
    the middle of the tabbar rather than the tail, and the annotator
    tends to omit exactly those — so the "skip to be skipped" is in
    the middle, and left-drifting matches leaves it correctly unused.
    """
    n, m = len(page_names), len(tabbar_paths)
    if n == 0 or m < n:
        return {}

    page_tokens = [set(_tokenize(pn)) for pn in page_names]
    path_tokens = [set(_tokenize(p)) for p in tabbar_paths]

    root_idx = next(
        (k for k, p in enumerate(tabbar_paths) if p == "/"), None
    )

    neg_inf = float("-inf")
    dp = [[neg_inf] * (m + 1) for _ in range(n + 1)]
    parent: list[list[str | None]] = [[None] * (m + 1) for _ in range(n + 1)]
    for j in range(m + 1):
        dp[0][j] = 0.0

    for i in range(1, n + 1):
        for j in range(i, m + 1):
            skip = dp[i][j - 1]
            sim = _weighted_dice(page_tokens[i - 1], path_tokens[j - 1], idf)
            sim *= _depth_factor(tabbar_paths[j - 1])
            if i == 1 and root_idx is not None and j - 1 == root_idx:
                sim += _FIRST_TAB_ROOT_BONUS
            match = (
                dp[i - 1][j - 1] + sim if dp[i - 1][j - 1] != neg_inf else neg_inf
            )
            if match > skip:
                dp[i][j] = match
                parent[i][j] = "M"
            else:
                dp[i][j] = skip
                parent[i][j] = "S"

    resolved: dict[str, str] = {}
    i, j = n, m
    while i > 0 and j > 0:
        op = parent[i][j]
        if op == "M":
            resolved[page_names[i - 1]] = tabbar_paths[j - 1]
            i -= 1
            j -= 1
        else:
            j -= 1
    return resolved


def get_tabbar_paths(run_dir: Path) -> list[str]:
    """Return the ordered path literals from the generated project's
    TabBar source file, or ``[]`` if no tabbar file can be located or
    no path literals can be extracted.

    Consumers (currently the runner's ``tab_click`` injection) use this
    to compute the real visual tab index for a known target route:

        visual_pos = tabbar_paths.index(route) + 1

    This is the authoritative tab order in the rendered app, so it
    should override the ``tab_position`` baked into the generated
    test case (which comes from ``tab_structure`` and may differ when
    Tier 1 runs the Ordered-Subset DP branch).
    """
    tabbar_file = _find_tabbar_file(run_dir)
    if tabbar_file is None:
        return []
    return _extract_tabbar_paths(tabbar_file)


def _resolve_via_tabbar(
    run_dir: Path,
    tab_structure: list[str],
) -> dict[str, str] | None:
    """Tier 1 entry: return {tab_page_name: route} or ``None`` on failure.

    Preconditions for returning a dict:
      - Tabbar file is locatable under ``src/components/``.
      - At least one ``path`` literal was extracted.
      - ``|paths| >= |tab_structure|``.

    The caller merges Tier 1 results over Tier 2 results (Tier 1 wins
    for pages in ``tab_structure``).
    """
    if not tab_structure:
        return None
    tabbar_file = _find_tabbar_file(run_dir)
    if tabbar_file is None:
        logger.debug("Tier 1: no tabbar file under %s/src/components", run_dir)
        return None
    paths = _extract_tabbar_paths(tabbar_file)
    if not paths:
        logger.debug("Tier 1: zero paths in %s", tabbar_file.name)
        return None
    n, m = len(tab_structure), len(paths)
    if m < n:
        logger.debug(
            "Tier 1: tabbar has fewer paths (%d) than tab_structure (%d) in %s",
            m, n, tabbar_file.name,
        )
        return None

    if m == n:
        resolved = dict(zip(tab_structure, paths))
        logger.info(
            "Tier 1 (positional, N=M=%d) via %s: %s",
            n, tabbar_file.name, resolved,
        )
        return resolved

    page_tokens = [set(_tokenize(pn)) for pn in tab_structure]
    path_tokens = [set(_tokenize(p)) for p in paths]
    idf = _compute_idf(page_tokens + path_tokens)
    resolved = _ordered_subset_match(tab_structure, paths, idf)
    if len(resolved) != n:
        logger.warning(
            "Tier 1: ordered-subset DP only matched %d/%d tabs in %s",
            len(resolved), n, tabbar_file.name,
        )
        return None
    logger.info(
        "Tier 1 (ordered-subset DP, N=%d M=%d) via %s: %s",
        n, m, tabbar_file.name, resolved,
    )
    return resolved


# ═══════════════════════════════════════════════════════════════════════════
# Main resolver
# ═══════════════════════════════════════════════════════════════════════════


def resolve_routes(
    run_dir: Path,
    page_names: list[str],
    *,
    tab_structure: list[str] | None = None,
) -> dict[str, str]:
    """Map each page_name to its actual route in the generated project.

    Arguments
    ---------
    run_dir
        Root of the generated project (contains ``src/router.tsx`` and
        ``src/components/``). Both Tier 1 and Tier 2 derive their
        inputs from this directory.
    page_names
        All page_names that need resolution (tab pages plus
        ``parent_child`` and transition targets from test cases).
    tab_structure
        Ordered list of tab page_names from the annotator metadata.
        When provided, Tier 1 uses it to directly position-align
        against the tabbar source. Pages in ``page_names`` that are
        also in ``tab_structure`` get their Tier 1 answer; the rest
        fall through to Tier 2.

    Algorithm
    ---------
    1. If ``tab_structure`` is given, run Tier 1 (tabbar source scan +
       ordered matching). See ``_resolve_via_tabbar``.
    2. Run Tier 2 (router.tsx global Hungarian) on the remaining
       page_names, with tabbar paths optionally removed from the
       candidate pool to prevent non-tab pages from stealing tab
       routes.
    3. Merge: Tier 1 wins for tab pages; Tier 2 fills in the rest.
    4. When ``router.tsx`` cannot be parsed or scipy is unavailable,
       degrade to a greedy bijection matcher or a naive guess.
    """
    router_path = run_dir / "src" / "router.tsx"

    # Tier 1: resolve tab pages via tabbar source
    tier1_result: dict[str, str] = {}
    tabbar_paths_used: set[str] = set()
    if tab_structure:
        tier1_try = _resolve_via_tabbar(run_dir, tab_structure)
        if tier1_try:
            tier1_result = tier1_try
            tabbar_paths_used = set(tier1_try.values())

    # Tier 2: resolve the remainder via router.tsx Hungarian
    remaining = [pn for pn in page_names if pn not in tier1_result]
    tier2_result = _resolve_via_router(
        router_path, remaining, exclude_routes=tabbar_paths_used
    )

    merged = {**tier2_result, **tier1_result}
    logger.info(
        "Route resolution: %d tab via Tier 1, %d other via Tier 2",
        len(tier1_result), len(tier2_result),
    )
    return merged


def _resolve_via_router(
    router_path: Path,
    page_names: list[str],
    *,
    exclude_routes: set[str] | None = None,
) -> dict[str, str]:
    """Tier 2: router.tsx-wide IDF Dice + Hungarian bijection matching.

    When ``exclude_routes`` is given (typically the routes already
    claimed by Tier 1), the corresponding components are dropped from
    the candidate pool so a non-tab page_name cannot take a tab route
    that was supposed to belong to a tab page.
    """
    if not page_names:
        return {}
    route_map = parse_route_map(router_path)
    if not route_map:
        routes_from_file = _parse_routes_simple(router_path)
        resolved = {
            pn: _best_route_guess(pn, routes_from_file) for pn in page_names
        }
        logger.info(
            "Tier 2 (legacy, no router parsed): %s", resolved,
        )
        return resolved

    if exclude_routes:
        route_map = {
            c: r for c, r in route_map.items() if r not in exclude_routes
        }
        if not route_map:
            return {pn: _page_name_to_route(pn) for pn in page_names}

    components: list[str] = list(route_map.keys())
    routes: list[str] = [route_map[c] for c in components]

    page_tokens: list[set[str]] = [set(_tokenize(pn)) for pn in page_names]
    comp_tokens: list[set[str]] = [
        set(_tokenize(c)) | set(_tokenize(r))
        for c, r in zip(components, routes)
    ]

    idf = _compute_idf(page_tokens + comp_tokens)

    try:
        import numpy as np
        from scipy.optimize import linear_sum_assignment  # type: ignore[import-untyped]
    except ImportError:
        logger.warning(
            "scipy/numpy unavailable, falling back to greedy bijection matching"
        )
        return _resolve_routes_greedy(route_map, page_names)

    n = len(page_names)
    m = len(components)
    depth_priors = np.array([_depth_factor(r) for r in routes], dtype=float)
    sim = np.zeros((n, m), dtype=float)
    for i in range(n):
        for j in range(m):
            base = _weighted_dice(page_tokens[i], comp_tokens[j], idf)
            sim[i, j] = base * depth_priors[j]

    row_idx, col_idx = linear_sum_assignment(-sim)
    assigned: dict[int, int] = {int(i): int(j) for i, j in zip(row_idx, col_idx)}

    resolved: dict[str, str] = {}
    for i, page_name in enumerate(page_names):
        if i in assigned:
            j = assigned[i]
            score = float(sim[i, j])
            if score >= _MIN_CONFIDENCE:
                resolved[page_name] = routes[j]
                continue
        resolved[page_name] = _page_name_to_route(page_name)

    return resolved


# ═══════════════════════════════════════════════════════════════════════════
# Greedy fallback (scipy-free path)
# ═══════════════════════════════════════════════════════════════════════════


def _resolve_routes_greedy(
    route_map: dict[str, str],
    page_names: list[str],
) -> dict[str, str]:
    """Per-page fuzzy matching with a hard bijection constraint.

    Used only when scipy/numpy are unavailable. Unlike the original
    implementation, a component claimed by one page cannot be reused
    by another, preventing the "two pages silently land on the same
    route" failure.
    """
    norm_component = {_normalise(c): (c, r) for c, r in route_map.items()}
    resolved: dict[str, str] = {}
    used: set[str] = set()

    for page_name in page_names:
        norm_pn = _normalise(page_name)

        if norm_pn in norm_component and norm_component[norm_pn][1] not in used:
            r = norm_component[norm_pn][1]
            resolved[page_name] = r
            used.add(r)
            continue

        best_score, best_route = 0.0, None
        for norm_c, (_, route) in norm_component.items():
            if route in used:
                continue
            score = SequenceMatcher(None, norm_pn, norm_c).ratio()
            if score > best_score:
                best_score, best_route = score, route

        if best_score >= 0.6 and best_route is not None:
            resolved[page_name] = best_route
            used.add(best_route)
            continue

        resolved[page_name] = _page_name_to_route(page_name)

    return resolved


# ═══════════════════════════════════════════════════════════════════════════
# Legacy helpers (kept for the "router unparseable" branch)
# ═══════════════════════════════════════════════════════════════════════════


def _normalise(name: str) -> str:
    """Lowercase and strip separators for fuzzy comparison."""
    return re.sub(r"[-_\s]", "", name.lower())


def _parse_routes_simple(router_path: Path) -> list[str]:
    """Fallback: extract just route paths via simple regex."""
    if not router_path.is_file():
        return []
    text = router_path.read_text(encoding="utf-8")
    return re.findall(r"""path\s*:\s*['"]([^'"]+)['"]""", text)


def _best_route_guess(page_name: str, available_routes: list[str]) -> str:
    """Pick the best matching route from available ones, or fall back to naive."""
    norm_pn = _normalise(page_name)
    best_score, best_route = 0.0, None
    for route in available_routes:
        norm_r = _normalise(route)
        score = SequenceMatcher(None, norm_pn, norm_r).ratio()
        if score > best_score:
            best_score, best_route = score, route
    if best_score >= 0.5 and best_route is not None:
        return best_route
    return _page_name_to_route(page_name)


def _page_name_to_route(page_name: str) -> str:
    return f"/{page_name.replace('_', '-')}"
