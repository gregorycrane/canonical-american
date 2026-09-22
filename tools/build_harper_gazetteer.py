#!/usr/bin/env python3
"""Build searchable and MVP-reading forms of Harper's 1855 gazetteer.

The legacy transcription contains tens of thousands of TEI ``div`` entries.
This tool preserves those records in SQLite and rewrites the TEI reading copy
into bounded entry groups so MVP never has to render an entire alphabet letter.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import sqlite3
import tempfile
import shutil
from pathlib import Path

from lxml import etree


TEI_NS = "http://www.tei-c.org/ns/1.0"
CTS_NS = "http://chs.harvard.edu/xmlns/cts"
XML_NS = "http://www.w3.org/XML/1998/namespace"
XML_ID = f"{{{XML_NS}}}id"
NS = {"tei": TEI_NS}


def normalized_text(element: etree._Element | None) -> str:
    if element is None:
        return ""
    return " ".join("".join(element.itertext()).split())


def build_database(source: Path, database: Path, source_label: str | None = None) -> dict:
    """Stream TEI entries into SQLite and an FTS5 search index."""
    database.parent.mkdir(parents=True, exist_ok=True)
    temporary = database.with_suffix(database.suffix + ".tmp")
    temporary.unlink(missing_ok=True)
    connection = sqlite3.connect(temporary)
    connection.executescript(
        """
        PRAGMA journal_mode=OFF;
        PRAGMA synchronous=OFF;
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE entries (
            rowid INTEGER PRIMARY KEY,
            entry_id TEXT NOT NULL UNIQUE,
            letter TEXT NOT NULL,
            position INTEGER NOT NULL,
            headword TEXT NOT NULL,
            text TEXT NOT NULL,
            first_page TEXT,
            last_page TEXT,
            tgn_ids TEXT NOT NULL,
            place_names TEXT NOT NULL,
            tei_xml TEXT NOT NULL
        );
        CREATE INDEX entries_letter_position ON entries(letter, position);
        CREATE INDEX entries_headword ON entries(headword COLLATE NOCASE);
        CREATE VIRTUAL TABLE entries_fts USING fts5(
            entry_id UNINDEXED, headword, text, place_names,
            tokenize='unicode61 remove_diacritics 2'
        );
        """
    )
    insert_entry = connection.cursor()
    insert_fts = connection.cursor()
    current_page = ""
    current_letter = ""
    entry_start_pages: dict[int, str] = {}
    positions: dict[str, int] = {}
    count = 0

    context = etree.iterparse(
        str(source),
        events=("start", "end"),
        tag=(f"{{{TEI_NS}}}div", f"{{{TEI_NS}}}pb"),
        huge_tree=True,
    )
    for event, element in context:
        local = etree.QName(element).localname
        if event == "start" and local == "pb":
            current_page = element.get("n", "")
            continue
        if local != "div":
            continue
        if event == "start":
            parent = element.getparent()
            if parent is not None and etree.QName(parent).localname == "body":
                current_letter = element.get("n", "")
            parent_is_letter = (
                parent is not None
                and parent.getparent() is not None
                and etree.QName(parent.getparent()).localname == "body"
            )
            if element.get("type") == "entry" and parent_is_letter:
                entry_start_pages[id(element)] = current_page
            continue
        if element.get("type") != "entry":
            continue

        parent = element.getparent()
        parent_is_letter = (
            parent is not None
            and parent.getparent() is not None
            and etree.QName(parent.getparent()).localname == "body"
        )
        if not parent_is_letter:
            continue

        provisional_headword = normalized_text(element.find("./tei:head", NS))
        inferred_letter = next(
            (character.upper() for character in provisional_headword if character.isascii() and character.isalpha()),
            "?",
        )
        letter = current_letter if re.fullmatch(r"[A-Z]", current_letter or "") else inferred_letter
        positions[letter] = positions.get(letter, 0) + 1
        position = positions[letter]
        entry_id = element.get(XML_ID) or f"{letter.lower()}-{position:05d}"
        headword = provisional_headword
        text = normalized_text(element)
        pages = [pb.get("n", "") for pb in element.findall(".//tei:pb", NS) if pb.get("n")]
        first_page = entry_start_pages.pop(id(element), "") or (pages[0] if pages else "")
        last_page = pages[-1] if pages else current_page or first_page
        place_names = sorted(
            {normalized_text(place) for place in element.findall(".//tei:placeName", NS) if normalized_text(place)}
        )
        tgn_ids = sorted(
            {
                key
                for place in element.findall(".//tei:placeName", NS)
                for key in [place.get("key", "")]
                if key.lower().startswith("tgn")
            }
        )
        tei_xml = etree.tostring(element, encoding="unicode", with_tail=False)
        insert_entry.execute(
            """INSERT INTO entries
               (entry_id, letter, position, headword, text, first_page, last_page,
                tgn_ids, place_names, tei_xml)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                entry_id,
                letter,
                position,
                headword,
                text,
                first_page,
                last_page,
                json.dumps(tgn_ids, ensure_ascii=False),
                json.dumps(place_names, ensure_ascii=False),
                tei_xml,
            ),
        )
        insert_fts.execute(
            "INSERT INTO entries_fts(entry_id, headword, text, place_names) VALUES (?, ?, ?, ?)",
            (entry_id, headword, text, " | ".join(place_names)),
        )
        count += 1
        if count % 5000 == 0:
            connection.commit()

        element.clear()
        while element.getprevious() is not None:
            del element.getparent()[0]

    connection.executemany(
        "INSERT INTO metadata(key, value) VALUES (?, ?)",
        [
            ("title", "Harper's statistical gazetteer of the world"),
            ("source_tei", source_label or str(source)),
            ("entry_count", str(count)),
            ("format_version", "1"),
        ],
    )
    connection.commit()
    connection.execute("PRAGMA optimize")
    connection.close()
    os.replace(temporary, database)
    return {
        "entries": count,
        "letters": len(positions),
        "by_letter": dict(sorted(positions.items())),
    }


def build_reading_edition(source: Path, output: Path, entries_per_chunk: int) -> None:
    """Create bounded CTS chunks with a DOM rewrite.

    Harper contains nested ``div3`` subentries and two legacy continuation
    divisions inside G. A DOM rewrite is deliberately used here: it moves
    complete entry subtrees, so a chunk boundary can never split nested TEI.
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix="harper-reading-", suffix=".xml", dir=output.parent, delete=False
    ) as temp:
        temporary = Path(temp.name)
    parser = etree.XMLParser(load_dtd=False, resolve_entities=False, no_network=True, huge_tree=True)
    tree = etree.parse(str(source), parser)
    body = tree.find("./tei:text/tei:body", NS)
    if body is None:
        temporary.unlink(missing_ok=True)
        raise ValueError("Harper TEI has no text/body")

    # The legacy file splits the end of G into two extra top-level divisions.
    # Merge any non-citation continuation into the preceding letter division.
    previous_letter = None
    for division in list(body):
        if etree.QName(division).localname != "div":
            continue
        n = division.get("n", "")
        if n.isdigit() or re.fullmatch(r"[A-Z]", n):
            previous_letter = division
            continue
        if previous_letter is None:
            raise ValueError(f"Cannot attach legacy continuation division {n!r}")
        if division.text and division.text.strip():
            if len(previous_letter):
                previous_letter[-1].tail = (previous_letter[-1].tail or "") + division.text
            else:
                previous_letter.text = (previous_letter.text or "") + division.text
        for child in list(division):
            previous_letter.append(child)
        body.remove(division)

    for division in body.findall("./tei:div", NS):
        n = division.get("n", "")
        division.set("type", "letter")
        division.set("subtype", "front-matter" if n.isdigit() else "letter")
        original_text = division.text
        original_children = list(division)
        for child in original_children:
            division.remove(child)
        division.text = "\n"
        group_number = 1
        entry_count = 0
        group = etree.SubElement(
            division,
            f"{{{TEI_NS}}}div",
            type="chapter",
            subtype="entry-group",
            n=str(group_number),
        )
        group.text = original_text
        for child in original_children:
            is_entry = etree.QName(child).localname == "div" and child.get("type") == "entry"
            if is_entry and entry_count and entry_count % entries_per_chunk == 0:
                group_number += 1
                group = etree.SubElement(
                    division,
                    f"{{{TEI_NS}}}div",
                    type="chapter",
                    subtype="entry-group",
                    n=str(group_number),
                )
            group.append(child)
            if is_entry:
                entry_count += 1

    refs = tree.find("./tei:teiHeader/tei:encodingDesc/tei:refsDecl", NS)
    if refs is None:
        temporary.unlink(missing_ok=True)
        raise ValueError("Harper TEI has no refsDecl")
    refs.clear()
    refs.set(XML_ID, "CTS")
    wrapper = etree.SubElement(refs, f"{{{TEI_NS}}}citeStructure", match="/TEI/text/body", use="@n")
    letter = etree.SubElement(
        wrapper,
        f"{{{TEI_NS}}}citeStructure",
        unit="letter",
        n="letter",
        delim=":",
        match="div[@type='letter']",
        use="@n",
    )
    etree.SubElement(
        letter,
        f"{{{TEI_NS}}}citeStructure",
        unit="entry-group",
        n="chunk",
        delim=".",
        match="div[@type='chapter']",
        use="@n",
    )
    tree.write(str(temporary), encoding="UTF-8", xml_declaration=True, pretty_print=False)
    del tree
    # Do not replace the source unless the complete result is well formed.
    try:
        for _, element in etree.iterparse(str(temporary), events=("end",), huge_tree=True):
            element.clear()
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    os.replace(temporary, output)


def annotate_cts_inventory(reading_tei: Path) -> None:
    """Make the distinct reading purpose visible in the MVP edition menu."""
    inventory = reading_tei.parent / "__cts__.xml"
    if not inventory.exists():
        return
    tree = etree.parse(str(inventory))
    label = tree.find(f".//{{{CTS_NS}}}edition/{{{CTS_NS}}}label")
    description = tree.find(f".//{{{CTS_NS}}}edition/{{{CTS_NS}}}description")
    if label is not None and label.text and "reading edition" not in label.text.lower():
        label.text += " (reading edition)"
    note = "MVP reading edition grouped into bounded ranges of gazetteer entries."
    if description is not None and note not in (description.text or ""):
        description.text = ((description.text or "").rstrip() + " " + note).strip()
    tree.write(str(inventory), encoding="UTF-8", xml_declaration=True, pretty_print=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tei", type=Path, help="Converted Harper TEI P5 file")
    parser.add_argument("--database", type=Path, required=True, help="SQLite output")
    parser.add_argument("--reading-output", type=Path, help="Reading TEI output (defaults to in-place)")
    parser.add_argument("--entries-per-chunk", type=int, default=100)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    supplied_source = args.tei.resolve()
    if supplied_source.suffix == ".gz" and args.reading_output is None:
        raise SystemExit("--reading-output is required when the TEI source is compressed")
    compressed_temp: Path | None = None
    if supplied_source.suffix == ".gz":
        with tempfile.NamedTemporaryFile(prefix="harper-source-", suffix=".xml", delete=False) as temp:
            compressed_temp = Path(temp.name)
            with gzip.open(supplied_source, "rb") as compressed:
                shutil.copyfileobj(compressed, temp)
        source = compressed_temp
    else:
        source = supplied_source
    reading_output = (args.reading_output or args.tei).resolve()
    if args.entries_per_chunk < 1:
        raise SystemExit("--entries-per-chunk must be positive")
    try:
        summary = build_database(source, args.database.resolve(), str(args.tei))
        build_reading_edition(source, reading_output, args.entries_per_chunk)
        annotate_cts_inventory(reading_output)
    finally:
        if compressed_temp is not None:
            compressed_temp.unlink(missing_ok=True)
    summary.update(
        {
            "source": str(args.tei),
            "database": str(args.database),
            "reading_tei": str(args.reading_output or args.tei),
            "entries_per_chunk": args.entries_per_chunk,
        }
    )
    summary_path = args.database.resolve().with_suffix(".summary.json")
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
