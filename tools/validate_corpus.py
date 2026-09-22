#!/usr/bin/env python3
"""Run structural CTS/TEI checks over a generated canoical-american corpus."""

from __future__ import annotations

import csv
import sys
from pathlib import Path

from lxml import etree

TEI = {"tei": "http://www.tei-c.org/ns/1.0"}
XML_BASE = "{http://www.w3.org/XML/1998/namespace}base"


def validate(root: Path) -> int:
    errors = []
    manifest = root / "conversion_manifest.csv"
    if not manifest.exists():
        errors.append("missing conversion_manifest.csv")
        rows = []
    else:
        with manifest.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
    parser = etree.XMLParser(huge_tree=True)
    for row in rows:
        path = root / row["output"]
        try:
            tree = etree.parse(str(path), parser)
        except Exception as exc:
            errors.append(f"{path}: XML parse failed: {exc}")
            continue
        doc = tree.getroot()
        body = doc.find("./tei:text/tei:body", TEI)
        if body is None:
            errors.append(f"{path}: missing text/body")
            continue
        if body.get(XML_BASE) != row["urn"]:
            errors.append(f"{path}: body xml:base does not match manifest URN")
        refs = doc.find("./tei:teiHeader/tei:encodingDesc/tei:refsDecl", TEI)
        if refs is None or not refs.xpath(".//tei:citeStructure", namespaces=TEI):
            errors.append(f"{path}: missing CTS citeStructure")
        if not body.xpath(".//tei:div[@type='chapter'][@n]", namespaces=TEI):
            errors.append(f"{path}: no citable chapter")
        work_cts = path.parent / "__cts__.xml"
        group_cts = path.parent.parent / "__cts__.xml"
        for metadata_path in (work_cts, group_cts):
            if not metadata_path.exists():
                errors.append(f"{path}: missing {metadata_path}")
    if errors:
        print("\n".join(errors[:100]), file=sys.stderr)
        print(f"FAILED: {len(errors)} error(s)", file=sys.stderr)
        return 1
    print(f"OK: {len(rows)} TEI files and their CTS inventories")
    return 0


if __name__ == "__main__":
    raise SystemExit(validate(Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()))
