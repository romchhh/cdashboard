"""Дедуплікація та об'єднання інсайтів після правил."""

from __future__ import annotations

from typing import Any

# Групи взаємовиключних інсайтів: залишаємо перший (найвищий пріоритет після сортування).
_MERGE_GROUPS: list[tuple[str, ...]] = [
    ("GEN01", "GEN06", "GEN23", "GEN07"),
    ("SEO01", "SEO10", "SEO02"),
    ("GAD01", "GAD02", "GAD09"),
    ("META01", "META02", "META03"),
    ("GEN19", "CROSS09"),
]

# Якщо є канальний інсайт — прибираємо загальний дубль по тій же темі.
_CHANNEL_SUPPRESS = {
    "GAD01": ("GEN11_google_ads",),
    "GAD02": ("GEN03",),
    "META01": ("GEN11_meta_ads",),
    "META02": ("GEN03",),
    "SEO01": ("GEN11_seo",),
    "SEO03": ("GEN11_seo",),
}


def _group_key(item_id: str) -> str | None:
    for group in _MERGE_GROUPS:
        for token in group:
            if token.endswith("_"):
                if item_id.startswith(token):
                    return group[0]
            elif item_id == token or item_id.startswith(f"{token}_"):
                return group[0]
    return None


def _merge_items(primary: dict[str, Any], secondary: dict[str, Any]) -> dict[str, Any]:
    merged = dict(primary)
    extra_why = [w for w in (secondary.get("why") or []) if w]
    why = list(merged.get("why") or [])
    for w in extra_why:
        if w not in why:
            why.append(w)
    if why:
        merged["why"] = why[:3]
    if secondary.get("impact") and not merged.get("impact"):
        merged["impact"] = secondary["impact"]
    if secondary.get("action") and not merged.get("action"):
        merged["action"] = secondary["action"]
    merged["impact_score"] = max(
        float(merged.get("impact_score") or 0),
        float(secondary.get("impact_score") or 0),
    )
    merged["priority"] = int(merged.get("priority") or 0) + 5
    # Оновити текст/chip
    parts = [merged.get("what_happened") or ""]
    if merged.get("why"):
        parts.append("Причини: " + "; ".join(merged["why"][:3]))
    if merged.get("impact"):
        parts.append(str(merged["impact"]))
    if merged.get("action"):
        parts.append(f"Перевірити: {merged['action']}")
    merged["text"] = ". ".join(p for p in parts if p)
    merged["chip"] = f"{merged.get('title')}: {(merged.get('what_happened') or '')[:90]}"
    return merged


def postprocess_insights(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not items:
        return []

    by_id = {str(i.get("id")): i for i in items}
    drop: set[str] = set()

    # Об'єднання в групах
    groups_seen: dict[str, dict[str, Any]] = {}
    for item in items:
        iid = str(item.get("id") or "")
        gkey = _group_key(iid)
        if not gkey:
            continue
        anchor = groups_seen.get(gkey)
        if anchor is None:
            groups_seen[gkey] = item
            continue
        anchor_id = str(anchor.get("id"))
        if float(item.get("impact_score") or 0) > float(anchor.get("impact_score") or 0):
            drop.add(anchor_id)
            merged = _merge_items(item, anchor)
            groups_seen[gkey] = merged
            by_id[iid] = merged
            drop.discard(iid)
        else:
            drop.add(iid)
            by_id[anchor_id] = _merge_items(anchor, item)
            groups_seen[gkey] = by_id[anchor_id]

    # Підавлення дублікатів між рівнями
    present = {iid for iid in by_id if iid not in drop}
    for src, targets in _CHANNEL_SUPPRESS.items():
        if any(p.startswith(src) or p == src for p in present):
            for t in targets:
                if t in present:
                    drop.add(t)
                for pid in list(present):
                    if pid.startswith(t):
                        drop.add(pid)

    result = [by_id[iid] for iid in by_id if iid not in drop]
    return result
