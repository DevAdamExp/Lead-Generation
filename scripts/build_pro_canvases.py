#!/usr/bin/env python3
"""build_pro_canvases.py — hand-authored, professional Obsidian canvases.

Unlike build_canvases.py (which auto-draws EVERY wikilink → a hairball), this
renders a DELIBERATE architecture diagram from an explicit layout spec per folder:
labeled layer-groups, a left→right flow, colour-coded cards, and a curated set of
*labelled* edges that trace how the system actually works.

Vault root is fixed to docs/Lead-Generation (the folder with .obsidian). Re-run to
regenerate; each canvas is overwritten. Pure stdlib.

Edit a SPEC below to change the diagram, then re-run.
"""
from __future__ import annotations

import hashlib
import json
import os

VAULT = "docs/Lead-Generation"

# Grid + card geometry (Obsidian units).
NODE_W, NODE_H = 400, 210
PITCH_X, PITCH_Y = 560, 290
PAD, HEADER = 30, 46           # group padding; extra top space for the label

# Obsidian preset colours: 1 red · 2 orange · 3 yellow · 4 green · 5 cyan · 6 purple
SCRAPER = {
    "folder": "scrapper",
    "title": ("# 🕸️  Scraper Subsystem — Architecture Map\n"
              "**Discovery → Merge → Pipeline → Enrich → Verify.** "
              "Colours = layer (see legend). Start at [[Scraper System]]."),
    "nodes": [
        # stem,                          col, row, colour
        ("Scraper System",                0, 0, "4"),
        ("Discovery vs Enrichment",       0, 1, "3"),
        ("Google Maps Discovery",         1, 0, "5"),
        ("Hotfrog",                       1, 1, "5"),
        ("Directory Scrapers",            1, 2, "5"),
        ("Sources Orchestrator",          2, 1, "2"),
        ("Pipeline Integration",          3, 1, "2"),
        ("Google Maps Enrichment",        4, 0, "6"),
        ("Review Scraper",                4, 1, "6"),
        ("Accuracy and Verification",     5, 1, "1"),
        ("Maps Extraction Internals",     2, 3, "3"),
        ("Scraper Configuration",         4, 3, None),
        ("Scraper Roadmap and YAGNI",     5, 3, None),
    ],
    "groups": [
        ("①  Discovery — find businesses",
         ["Google Maps Discovery", "Hotfrog", "Directory Scrapers"]),
        ("④  Enrichment — add depth",
         ["Google Maps Enrichment", "Review Scraper"]),
        ("Shared reader — the accuracy core",
         ["Maps Extraction Internals"]),
        ("Reference",
         ["Scraper Configuration", "Scraper Roadmap and YAGNI"]),
    ],
    "edges": [
        # from, to, label, colour
        ("Google Maps Discovery", "Sources Orchestrator", "primary", "2"),
        ("Hotfrog", "Sources Orchestrator", "coverage", "2"),
        ("Directory Scrapers", "Sources Orchestrator", "gated", "2"),
        ("Sources Orchestrator", "Pipeline Integration", "candidate pool", "2"),
        ("Pipeline Integration", "Google Maps Enrichment", "enrich‑until‑N", "2"),
        ("Pipeline Integration", "Review Scraper", "export slice", "2"),
        ("Pipeline Integration", "Accuracy and Verification", "verify + ship", "2"),
        ("Google Maps Discovery", "Maps Extraction Internals", "uses reader", "1"),
        ("Google Maps Enrichment", "Maps Extraction Internals", "delegates to", "1"),
        ("Scraper System", "Discovery vs Enrichment", "core concept", "5"),
        ("Discovery vs Enrichment", "Google Maps Discovery", "discovery", "5"),
        ("Discovery vs Enrichment", "Google Maps Enrichment", "enrichment", "5"),
        ("Scraper Configuration", "Pipeline Integration", "knobs", None),
        ("Scraper Roadmap and YAGNI", "Accuracy and Verification", "what's fixed", None),
    ],
    "legend_row": 3,
}

TOR = {
    "folder": "Tor",
    "title": ("# 🧅  Tor Proxy Subsystem — Architecture Map\n"
              "**Fundamentals → Isolation → Rotator → API → Consumers.** "
              "Colours = layer (see legend). Start at [[Tor Proxy System]]."),
    "nodes": [
        ("Tor Proxy System",                 0, 0, "4"),
        ("Tor Fundamentals",                 1, 0, "3"),
        ("Circuit Isolation",                1, 1, "3"),
        ("ProxyRotator",                     2, 0, "2"),
        ("TorInstance",                      2, 1, "2"),
        ("get_proxy and Tags",               3, 0, "5"),
        ("Circuit Capacity and Concurrency", 3, 1, "5"),
        ("Failure Classification and Retry", 4, 0, "1"),
        ("Health and Thread-Safety",         4, 1, "1"),
        ("Consumers",                        5, 0, "6"),
        ("Proxy Strategy Tiers",             5, 1, "6"),
        ("Tor Daemon Deployment",            2, 3, None),
        ("Configuration",                    3, 3, None),
        ("Roadmap and YAGNI",                5, 3, None),
    ],
    "groups": [
        ("Foundations — the ideas", ["Tor Fundamentals", "Circuit Isolation"]),
        ("Core classes", ["ProxyRotator", "TorInstance"]),
        ("Public API", ["get_proxy and Tags", "Circuit Capacity and Concurrency"]),
        ("Reliability", ["Failure Classification and Retry", "Health and Thread-Safety"]),
        ("Consumers & strategy", ["Consumers", "Proxy Strategy Tiers"]),
        ("Deployment & config", ["Tor Daemon Deployment", "Configuration"]),
    ],
    "edges": [
        ("Tor Proxy System", "Tor Fundamentals", "start here", "3"),
        ("Tor Fundamentals", "Circuit Isolation", "the key idea", "3"),
        ("Circuit Isolation", "ProxyRotator", "implemented by", "2"),
        ("ProxyRotator", "TorInstance", "manages", "2"),
        ("ProxyRotator", "get_proxy and Tags", "exposes", "5"),
        ("get_proxy and Tags", "Circuit Capacity and Concurrency", "sizes", "5"),
        ("get_proxy and Tags", "Consumers", "used by", "5"),
        ("get_proxy and Tags", "Proxy Strategy Tiers", "tiers", "5"),
        ("ProxyRotator", "Failure Classification and Retry", "retry", "1"),
        ("ProxyRotator", "Health and Thread-Safety", "monitors", "1"),
        ("Tor Daemon Deployment", "ProxyRotator", "provisions", None),
        ("Configuration", "ProxyRotator", "tunes", None),
        ("Roadmap and YAGNI", "Proxy Strategy Tiers", "phase 4", None),
    ],
    "legend_row": 3,
}

LEGEND = ("### Legend\n"
          "🟩 MOC / entry  🟨 concept / core\n"
          "🟦 discovery source  🟪 enrichment\n"
          "🟧 orchestration + pipeline  🟥 quality gate\n"
          "⬜ reference\n\n"
          "**bold edge labels** trace the data flow.")


def _id(*parts: str) -> str:
    return hashlib.md5("|".join(parts).encode()).hexdigest()[:16]


def _rect(col: int, row: int):
    return col * PITCH_X, row * PITCH_Y, NODE_W, NODE_H


def _side(dx: float, dy: float) -> str:
    if abs(dx) >= abs(dy):
        return "right" if dx > 0 else "left"
    return "bottom" if dy > 0 else "top"


def build(spec: dict) -> str:
    folder = spec["folder"]
    fdir = os.path.join(VAULT, folder)
    pos = {}                                   # stem -> (x, y)
    color = {}
    for stem, col, row, col_c in spec["nodes"]:
        p = os.path.join(fdir, f"{stem}.md")
        if not os.path.exists(p):
            raise SystemExit(f"missing note: {p}")
        x, y, _, _ = _rect(col, row)
        pos[stem] = (x, y)
        color[stem] = col_c

    nodes = []

    # 1) groups first (render behind the cards)
    for label, members in spec["groups"]:
        xs = [pos[m][0] for m in members]
        ys = [pos[m][1] for m in members]
        x0, y0 = min(xs) - PAD, min(ys) - PAD - HEADER
        x1, y1 = max(xs) + NODE_W + PAD, max(ys) + NODE_H + PAD
        nodes.append({"id": _id(folder, "grp", label), "type": "group",
                      "label": label, "x": x0, "y": y0,
                      "width": x1 - x0, "height": y1 - y0})

    # 2) file cards
    for stem, (x, y) in pos.items():
        node = {"id": _id(folder, stem), "type": "file",
                "file": f"{folder}/{stem}.md", "x": x, "y": y,
                "width": NODE_W, "height": NODE_H}
        if color[stem]:
            node["color"] = color[stem]
        nodes.append(node)

    # 3) title + legend text cards (on top)
    top_y = min(y for _, y in pos.values())
    nodes.append({"id": _id(folder, "title"), "type": "text", "text": spec["title"],
                  "x": 0, "y": top_y - PITCH_Y - 20, "width": NODE_W * 3 + PITCH_X, "height": 150})
    nodes.append({"id": _id(folder, "legend"), "type": "text", "text": LEGEND,
                  "x": 0, "y": spec["legend_row"] * PITCH_Y - HEADER,
                  "width": NODE_W + 60, "height": NODE_H + 40})

    # edges — curated, labelled, sides derived from geometry
    edges = []
    for a, b, label, ec in spec["edges"]:
        ax, ay = pos[a]
        bx, by = pos[b]
        acx, acy = ax + NODE_W / 2, ay + NODE_H / 2
        bcx, bcy = bx + NODE_W / 2, by + NODE_H / 2
        e = {"id": _id(folder, a, b), "fromNode": _id(folder, a),
             "fromSide": _side(bcx - acx, bcy - acy), "toNode": _id(folder, b),
             "toSide": _side(acx - bcx, acy - bcy), "label": label}
        if ec:
            e["color"] = ec
        edges.append(e)

    out = os.path.join(fdir, f"{folder}.canvas")
    open(out, "w", encoding="utf-8").write(
        json.dumps({"nodes": nodes, "edges": edges}, indent=2, ensure_ascii=False))
    return out


if __name__ == "__main__":
    for spec in (SCRAPER, TOR):
        out = build(spec)
        data = json.loads(open(out, encoding="utf-8").read())
        cards = [n for n in data["nodes"] if n["type"] == "file"]
        groups = [n for n in data["nodes"] if n["type"] == "group"]
        print(f"{out}  —  {len(cards)} cards, {len(groups)} groups, {len(data['edges'])} labelled edges")
