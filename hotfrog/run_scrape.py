#!/usr/bin/env python3
"""
run_scrape.py — batch Hotfrog scrape to a spreadsheet.

Usage:
    python run_scrape.py --country us --limit 20 \
        --queries "plumbers austin" "hvac austin" \
        --out leads

Writes leads.csv and leads.xlsx (deduped by name+phone).
"""
import argparse
import csv
import sys
import time
import hotfrog_core as core

FIELDS = ["name", "address", "phone", "website", "description",
          "query", "source_search_url"]


def run(queries, country, limit, out):
    rows, seen = [], set()
    for q in queries:
        try:
            results = core.search_businesses(q, country, limit)
        except Exception as e:
            print(f"  ! '{q}' failed: {e}", file=sys.stderr)
            continue
        for r in results:
            key = (r.get("name"), r.get("phone"))
            if key in seen:
                continue
            seen.add(key)
            r["query"] = q
            rows.append({k: r.get(k) for k in FIELDS})
        print(f"  {q}: {len(results)} found")
        time.sleep(1.5)  # be polite; avoid hammering the site

    with open(f"{out}.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)

    try:
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Hotfrog leads"
        ws.append(FIELDS)
        for row in rows:
            ws.append([row.get(k) for k in FIELDS])
        wb.save(f"{out}.xlsx")
        print(f"Wrote {len(rows)} rows to {out}.csv and {out}.xlsx")
    except ImportError:
        print(f"Wrote {len(rows)} rows to {out}.csv (install openpyxl for .xlsx)")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--queries", nargs="+", required=True)
    p.add_argument("--country", default="us")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--out", default="hotfrog_leads")
    a = p.parse_args()
    run(a.queries, a.country, a.limit, a.out)