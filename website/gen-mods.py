#!/usr/bin/env python3
"""Generate website/welcome/mods.json from mods-26.3/CHECKLIST.md.

Output feeds the welcome page's mod showcase. Deterministic; run from
repo root:  python3 website/gen-mods.py
"""
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
CHECKLIST = ROOT / "mods-26.3" / "CHECKLIST.md"
NOTES = ROOT / "website" / "mod-notes.json"
OUT = ROOT / "website" / "welcome" / "mods.json"

ROW_RE = re.compile(
    r"^\|\s*(?:~~)?`(?P<jar>[^`]+)`(?:~~)?\s*\|\s*(?P<name>[^|]+?)\s*"
    r"\|\s*(?P<side>[^|]+?)\s*\|\s*(?P<typ>[^|]+?)\s*\|$")

CATEGORY_ORDER = ["gameplay", "qol", "performance", "client", "tech", "server"]
CAT_LABEL_DE = {
    "gameplay": "Gameplay",
    "qol": "Komfort",
    "performance": "Performance",
    "client": "Client-Empfehlung",
    "tech": "Bibliothek",
    "server": "Server-Intern",
}


def parse_side(side: str) -> tuple[str, str]:
    """Normalize the checklist's side wording -> (badge, badgeClass)."""
    s = side.strip().lower()
    if "beide seiten pflicht" in s or s == "both sides mandatory":
        return "Client+Server nötig", "req"
    if "beide seiten empfohlen" in s:
        return "Client empfohlen", "rec"
    if "beidseitig optional" in s:
        return "Client optional", "opt"
    if "server pflicht" in s:
        return "Server macht's, Client egal", "srv"
    if "client pflicht" in s:
        return "Client nötig!", "cli"
    if "server-only" in s:
        return "nur Server", "srv"
    return side.strip(), "opt"


def main() -> int:
    rows = []
    for line in CHECKLIST.read_text(encoding="utf-8").splitlines():
        m = ROW_RE.match(line.strip())
        if not m:
            continue
        jar = m.group("jar").strip()
        name = m.group("name").strip()
        disabled = line.strip().startswith("| ~~")
        rows.append({
            "jar": jar,
            "name": name,
            "side": m.group("side").strip(),
            "typ": m.group("typ").strip(),
            "disabled": disabled,
        })

    notes = json.loads(NOTES.read_text(encoding="utf-8"))["mods"]

    mods = []
    unmatched = []
    for row in rows:
        stem = row["jar"].removesuffix(".jar").lower()
        note = notes.get(row["jar"].lower()) or notes.get(stem)
        if note is None:
            unmatched.append(row["jar"])
            note = {}
        badge, badge_class = parse_side(row["side"])
        category = note.get("category", "tech")
        mods.append({
            "name": note.get("displayName") or row["name"],
            "jar": row["jar"],
            "category": category,
            "sideBadge": badge,
            "sideClass": badge_class,
            "tip": note.get("tip", ""),
            "clientHelp": note.get("clientHelp", ""),
            "disabled": row["disabled"],
        })

    cats: dict[str, list] = {}
    for mod in mods:
        if mod["disabled"]:
            continue
        cats.setdefault(mod["category"], []).append(mod)

    ordered_cats = [c for c in CATEGORY_ORDER if c in cats]
    for c in cats:
        if c not in ordered_cats:
            ordered_cats.append(c)

    payload = {
        "generatedFrom": "mods-26.3/CHECKLIST.md",
        "categories": [
            {"id": cid, "label": CAT_LABEL_DE.get(cid, cid), "mods": cats[cid]}
            for cid in ordered_cats
        ],
        "totals": {
            "active": sum(len(v) for v in cats.values()),
            "disabled": sum(1 for m in mods if m["disabled"]),
        },
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}:"
          f" {payload['totals']['active']} active,"
          f" {payload['totals']['disabled']} disabled")
    if unmatched:
        print("WITHOUT NOTES (please enrich in mod-notes.json):",
              file=sys.stderr)
        for j in unmatched:
            print("  -", j, file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
