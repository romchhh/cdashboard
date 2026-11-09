"""Регресія матчингу назв (cosmyparser/scripts/match_cases.py)."""
from __future__ import annotations

import runpy
import sys
from pathlib import Path


def test_cosmy_match_cases_regression():
    root = Path(__file__).resolve().parents[1]
    script = root / "cosmyparser" / "scripts" / "match_cases.py"
    sys.path.insert(0, str(root / "cosmyparser"))
    ns = runpy.run_path(str(script))
    threshold = ns["THRESHOLD"]
    score_fn = ns["score"]
    bad = 0
    for query, title, expected in ns["CASES"]:
        s = score_fn(query, title)
        ok = (s >= threshold) == expected
        bad += not ok
    assert bad == 0, f"{bad} пар матчингу не пройшли регресію"
