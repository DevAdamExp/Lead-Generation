#!/usr/bin/env python3
"""build_canvases.py — one Obsidian Canvas per folder, auto-laid-out from wikilinks.

Usage:
    python scripts/build_canvases.py <vault_root> [<vault_root> ...]

For every folder under each vault root that directly holds >= 1 markdown note,
writes  <folder>/<folder-name>.canvas :
  - a file-node per note, arranged radially around the folder's HUB note
    (frontmatter `type: moc`, else the most-linked-to note),
  - edges drawn from the [[wikilinks]] between notes IN THAT SAME FOLDER.

Node `file` paths are written relative to <vault_root>, so pass the folder you
actually open in Obsidian as the vault. Re-run any time — each canvas is
overwritten in place (idempotent). Links pointing outside the folder are ignored,
so every canvas is a clean, self-contained per-folder map. Pure stdlib.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sys

_WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]")
_TYPE_MOC = re.compile(r"^type:\s*moc\b", re.M | re.I)
_W, _H = 340, 240          # canvas node size (Obsidian units)


def _id(*parts: str) -> str:
    return hashlib.md5("|".join(parts).encode()).hexdigest()[:16]


def _frontmatter(txt: str) -> str:
    if not txt.startswith("---"):
        return ""
    end = txt.find("\n---", 3)
    return txt[:end] if end != -1 else ""


def _side(dx: float, dy: float) -> str:
    """Which side of a node an edge toward (dx, dy) should leave from."""
    if abs(dx) >= abs(dy):
        return "right" if dx > 0 else "left"
    return "bottom" if dy > 0 else "top"


def _folders_with_md(root: str):
    for dirpath, _dirs, files in os.walk(root):
        if any(f.endswith(".md") for f in files):
            yield dirpath


def build_canvas(folder: str, vault_root: str) -> str | None:
    notes = sorted(f for f in os.listdir(folder) if f.endswith(".md"))
    if not notes:
        return None
    stems = [os.path.splitext(n)[0] for n in notes]
    stem_set = set(stems)

    links = {s: set() for s in stems}        # s -> stems it points to (in-folder)
    incoming = {s: 0 for s in stems}
    moc = None
    for note, s in zip(notes, stems):
        txt = open(os.path.join(folder, note), encoding="utf-8").read()
        if moc is None and _TYPE_MOC.search(_frontmatter(txt)):
            moc = s
        for m in _WIKILINK.finditer(txt):
            tgt = m.group(1).strip()
            if tgt in stem_set and tgt != s:
                links[s].add(tgt)
                incoming[tgt] += 1

    hub = moc or max(stems, key=lambda s: incoming[s])

    # Radial layout: hub at origin, everything else on a ring around it.
    others = [s for s in stems if s != hub]
    radius = max(520, int(len(others) * 78))
    pos = {hub: (0, 0)}
    for i, s in enumerate(others):
        ang = 2 * math.pi * i / max(len(others), 1)
        pos[s] = (int(radius * math.cos(ang)), int(radius * math.sin(ang)))

    rel_dir = os.path.relpath(folder, vault_root)

    def vault_path(stem: str) -> str:
        p = f"{stem}.md" if rel_dir in (".", "") else f"{rel_dir}/{stem}.md"
        return p.replace(os.sep, "/")

    nodes = []
    for s in stems:
        x, y = pos[s]
        node = {"id": _id(folder, s), "type": "file", "file": vault_path(s),
                "x": x - _W // 2, "y": y - _H // 2, "width": _W, "height": _H}
        if s == hub:
            node["color"] = "4"          # accent the hub (green)
        nodes.append(node)

    seen, edges = set(), []               # one edge per unordered pair
    for a in stems:
        for b in links[a]:
            key = tuple(sorted((a, b)))
            if key in seen:
                continue
            seen.add(key)
            ax, ay = pos[a]
            bx, by = pos[b]
            edges.append({
                "id": _id(folder, a, b),
                "fromNode": _id(folder, a), "fromSide": _side(bx - ax, by - ay),
                "toNode": _id(folder, b), "toSide": _side(ax - bx, ay - by),
            })

    out = os.path.join(folder, f"{os.path.basename(os.path.abspath(folder))}.canvas")
    open(out, "w", encoding="utf-8").write(json.dumps({"nodes": nodes, "edges": edges}, indent=2))
    return out


def main(argv: list[str]) -> None:
    roots = argv or ["."]
    for root in roots:
        root = os.path.abspath(root)
        for folder in _folders_with_md(root):
            out = build_canvas(folder, root)
            if out:
                data = json.loads(open(out, encoding="utf-8").read())
                print(f"{out}  —  {len(data['nodes'])} nodes, {len(data['edges'])} edges")


if __name__ == "__main__":
    main(sys.argv[1:])
