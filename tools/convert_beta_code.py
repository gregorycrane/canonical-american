#!/usr/bin/env python3
"""Find and convert Beta Code in TEI ``foreign[@xml:lang='grc']`` spans."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from tools.beta_code_tei import convert_gzip_xml, convert_xml_file
except ModuleNotFoundError:  # Direct execution as ``python tools/...``.
    from beta_code_tei import convert_gzip_xml, convert_xml_file


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, nargs="?", default=Path("data"))
    parser.add_argument("--gzip", type=Path, action="append", default=[])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--report", type=Path, default=Path("beta_code_conversion.json"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    files = sorted(args.root.rglob("*.xml")) if args.root.is_dir() else [args.root]
    report = []
    for path in files:
        if path.name == "__cts__.xml":
            continue
        changes = convert_xml_file(path, write=not args.dry_run)
        if changes:
            report.append({"file": str(path), "changes": changes})
    for path in args.gzip:
        changes = convert_gzip_xml(path, write=not args.dry_run)
        if changes:
            report.append({"file": str(path), "changes": changes})
    payload = {
        "mode": "dry-run" if args.dry_run else "write",
        "files_changed": len(report),
        "spans_converted": sum(len(item["changes"]) for item in report),
        "files": report,
    }
    args.report.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: payload[key] for key in ("mode", "files_changed", "spans_converted")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
