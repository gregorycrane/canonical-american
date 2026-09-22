#!/usr/bin/env python3
"""Audit named-entity tagging/linking coverage across the canonical-american corpus.

Produces a before/after-comparable snapshot: mention counts, external-`key`
linking coverage, legacy `reg=` heuristic-label breakdown for `persName`, and
`rs/@type` distribution, per file and corpus-wide.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, Iterable, List

from lxml import etree

TEI_NS = "http://www.tei-c.org/ns/1.0"
TEI = {"tei": TEI_NS}

ENTITY_TAGS = ("persName", "placeName", "orgName", "rs")


def reg_label(reg: str | None) -> str:
    """First ``:``-delimited token of a legacy ``reg=`` heuristic string."""
    if not reg:
        return "missing"
    return reg.split(":", 1)[0] or "missing"


def audit_file(path: Path) -> Dict:
    parser = etree.XMLParser(huge_tree=True)
    tree = etree.parse(str(path), parser)
    root = tree.getroot()

    stats: Dict[str, Dict] = {}
    for tag in ENTITY_TAGS:
        elements = root.findall(f".//tei:{tag}", TEI)
        total = len(elements)
        with_key = sum(1 for el in elements if el.get("key"))
        entry: Dict = {"total": total, "with_key": with_key}
        if tag == "persName":
            entry["reg_labels"] = dict(Counter(reg_label(el.get("reg")) for el in elements))
        if tag == "rs":
            entry["types"] = dict(Counter(el.get("type") or "notype" for el in elements))
        stats[tag] = entry
    return stats


def merge_counts(target: Dict, source: Dict) -> None:
    for tag, entry in source.items():
        agg = target.setdefault(tag, {"total": 0, "with_key": 0})
        agg["total"] += entry["total"]
        agg["with_key"] += entry["with_key"]
        if "reg_labels" in entry:
            labels = agg.setdefault("reg_labels", Counter())
            labels.update(entry["reg_labels"])
        if "types" in entry:
            types = agg.setdefault("types", Counter())
            types.update(entry["types"])


def finalize_counters(entry: Dict) -> Dict:
    for key in ("reg_labels", "types"):
        if key in entry and isinstance(entry[key], Counter):
            entry[key] = dict(entry[key])
    return entry


def audit(root: Path, files: Iterable[Path] | None = None) -> Dict:
    if files is None:
        files = sorted(root.glob("data/**/*.perseus-eng1.xml"))
    per_file = {}
    totals: Dict[str, Dict] = {}
    errors: List[str] = []
    for path in files:
        try:
            stats = audit_file(path)
        except Exception as exc:  # malformed XML shouldn't abort the whole audit
            errors.append(f"{path}: {exc}")
            continue
        per_file[str(path.relative_to(root))] = stats
        merge_counts(totals, stats)

    for entry in totals.values():
        finalize_counters(entry)

    return {
        "corpus_root": str(root),
        "file_count": len(per_file),
        "errors": errors,
        "totals": totals,
        "files": per_file,
    }


def parse_args(argv: List[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", default=".", help="Repository root (default: cwd)")
    parser.add_argument(
        "--output",
        default="entity_audit_baseline.json",
        help="Path to write the JSON report (default: entity_audit_baseline.json)",
    )
    return parser.parse_args(argv)


def main(argv: List[str] | None = None) -> int:
    args = parse_args(argv)
    root = Path(args.root).resolve()
    report = audit(root)
    output_path = Path(args.output)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    totals = report["totals"]
    print(f"Audited {report['file_count']} files under {root}")
    for tag in ENTITY_TAGS:
        entry = totals.get(tag, {"total": 0, "with_key": 0})
        total = entry["total"]
        with_key = entry["with_key"]
        pct = 100 * with_key / total if total else 0.0
        print(f"  {tag}: {total} mentions, {with_key} linked ({pct:.1f}%)")
    if report["errors"]:
        print(f"  {len(report['errors'])} file(s) failed to parse; see report", file=sys.stderr)
    print(f"Wrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
