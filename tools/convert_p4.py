#!/usr/bin/env python3
"""Convert the Perseus American TEI P4 collection to TEI P5 with CTS data."""

from __future__ import annotations

import argparse
import csv
import hashlib
import html.entities
import json
import re
import shutil
import sys
import tempfile
import unicodedata
import xml.sax
from xml.sax.saxutils import XMLGenerator
from xml.sax.xmlreader import AttributesImpl
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from lxml import etree


TEI_NS = "http://www.tei-c.org/ns/1.0"
CTS_NS = "http://chs.harvard.edu/xmlns/cts"
XML_NS = "http://www.w3.org/XML/1998/namespace"
NSMAP = {None: TEI_NS}
XML_ID = f"{{{XML_NS}}}id"
XML_LANG = f"{{{XML_NS}}}lang"

BUILTIN_ENTITIES = {"amp", "lt", "gt", "quot", "apos"}
CUSTOM_ENTITIES = {
    "Perseus.publish": "",
    "PersTemplates": "",
    "tprime": "‴",
    "natur": "♮",
    "shortnote": "♪",
    "verbar": "|",
    "dot": "˙",
    "minus": "−",
    "Ebreve": "Ĕ",
    "ebreve": "ĕ",
    "ibreve": "ĭ",
    "obreve": "ŏ",
    "ngrave": "ǹ",
    "pacute": "é",
    "quantity1": "℔",
    "quantity2": "℥",
    "dram": "ʒ",
    "quantity3": "℈",
    "minim": "♏",
}

DIV_RE = re.compile(r"div[0-9]+$", re.I)
DOCTYPE_RE = re.compile(r"<!DOCTYPE\s+[^\[]*(?:\[(?:.|\n|\r)*?\]\s*)?>", re.I)
ENTITY_RE = re.compile(r"&([A-Za-z][\w.-]+);")
INVALID_XML_CONTROL_RE = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f]")


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def normalized_text(element: Optional[etree._Element]) -> str:
    if element is None:
        return ""
    return " ".join("".join(element.itertext()).split())


def slugify(value: str, fallback: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower().replace("&", " and ")
    value = re.sub(r"[^a-z0-9]+", "_", value).strip("_")
    if not value:
        value = fallback
    if not value[0].isalpha():
        value = f"x_{value}"
    return value[:80]


def version_stem(path: Path) -> str:
    stem = path.name
    for suffix in (".ie.xml", ".xml"):
        if stem.lower().endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    return slugify(stem, "work")


def entity_value(name: str) -> Optional[str]:
    if name in CUSTOM_ENTITIES:
        return CUSTOM_ENTITIES[name]
    value = html.entities.html5.get(name + ";")
    if value is None:
        value = html.entities.html5.get(name)
    return value


def clean_legacy_xml(raw: str) -> Tuple[str, Counter]:
    raw = raw.lstrip("\ufeff")
    raw = DOCTYPE_RE.sub("", raw, count=1)
    raw = INVALID_XML_CONTROL_RE.sub("�", raw)
    unknown = Counter()

    def replace_entity(match: re.Match) -> str:
        name = match.group(1)
        if name in BUILTIN_ENTITIES:
            return match.group(0)
        value = entity_value(name)
        if value is None:
            unknown[name] += 1
            return f"[UNRESOLVED ENTITY: {name}]"
        return value

    return ENTITY_RE.sub(replace_entity, raw), unknown


def parse_legacy(path: Path, allow_recovery: bool = True) -> Tuple[etree._ElementTree, dict]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    cleaned, unknown = clean_legacy_xml(raw)
    parser = etree.XMLParser(load_dtd=False, resolve_entities=False, no_network=True, huge_tree=True)
    recovered = False
    try:
        root = etree.fromstring(cleaned.encode("utf-8"), parser)
    except etree.XMLSyntaxError:
        if not allow_recovery:
            raise
        parser = etree.XMLParser(
            load_dtd=False,
            resolve_entities=False,
            no_network=True,
            huge_tree=True,
            recover=True,
        )
        root = etree.fromstring(cleaned.encode("utf-8"), parser)
        recovered = True
    if root is None or local_name(root.tag).lower() not in {"tei.2", "tei"}:
        raise ValueError("No TEI root element found")
    return etree.ElementTree(root), {
        "recovered": recovered,
        "parser_messages": [entry.message for entry in parser.error_log],
        "unknown_entities": dict(unknown),
    }


def extract_metadata(tree: etree._ElementTree, source: Path) -> dict:
    root = tree.getroot()
    title = next((normalized_text(e) for e in root.iter() if local_name(e.tag) == "title"), "")
    author = next((normalized_text(e) for e in root.iter() if local_name(e.tag) == "author"), "")
    source_desc = next(
        (normalized_text(e) for e in root.iter() if local_name(e.tag) == "sourceDesc"), ""
    )
    title = title or version_stem(source).replace("_", " ").title()
    author = author or "Anonymous"
    return {"title": title, "author": author, "source_desc": source_desc}


def set_tei_namespace(root: etree._Element) -> etree._Element:
    root.tag = f"{{{TEI_NS}}}TEI"
    for element in root.iter():
        if not isinstance(element.tag, str):
            continue
        name = local_name(element.tag)
        if DIV_RE.fullmatch(name):
            name = "div"
        elif name == "TEI.2":
            name = "TEI"
        elif name == "foreName":
            name = "forename"
        element.tag = f"{{{TEI_NS}}}{name}"
    # Rebuild the root once so lxml serializes TEI as the default namespace
    # instead of assigning the less readable ``ns0`` prefix to every element.
    replacement = etree.Element(f"{{{TEI_NS}}}TEI", nsmap=NSMAP)
    replacement.attrib.update(root.attrib)
    replacement.text = root.text
    replacement.tail = root.tail
    for child in list(root):
        root.remove(child)
        replacement.append(child)
    root.getroottree()._setroot(replacement)
    etree.cleanup_namespaces(replacement)
    return replacement


def migrate_attributes(root: etree._Element, report: dict) -> None:
    seen_ids = set()
    deduped = 0
    for element in root.iter():
        if not isinstance(element.tag, str):
            continue
        name = local_name(element.tag)
        attrs = dict(element.attrib)
        element.attrib.clear()
        for key, value in attrs.items():
            key_name = local_name(key)
            if key_name == "lang":
                element.set(XML_LANG, "grc" if value in {"greek", "gr"} else value)
            elif key_name == "id" and name == "language":
                element.set("ident", "grc" if value in {"greek", "gr"} else value)
            elif key_name == "id":
                candidate = re.sub(r"[^A-Za-z0-9_.-]", "_", value)
                if not candidate or not re.match(r"[A-Za-z_]", candidate):
                    candidate = "id_" + candidate
                base = candidate
                serial = 2
                while candidate in seen_ids:
                    candidate = f"{base}_{serial}"
                    serial += 1
                    deduped += 1
                seen_ids.add(candidate)
                element.set(XML_ID, candidate)
            else:
                element.set(key_name, value)
    report["deduplicated_xml_ids"] = deduped


def find_child(parent: etree._Element, name: str) -> Optional[etree._Element]:
    return next((c for c in parent if isinstance(c.tag, str) and local_name(c.tag) == name), None)


def ensure_header(root: etree._Element, source_name: str) -> etree._Element:
    header = find_child(root, "teiHeader")
    if header is None:
        header = etree.Element(f"{{{TEI_NS}}}teiHeader")
        root.insert(0, header)
    file_desc = find_child(header, "fileDesc")
    if file_desc is None:
        file_desc = etree.SubElement(header, f"{{{TEI_NS}}}fileDesc")
    title_stmt = find_child(file_desc, "titleStmt")
    if title_stmt is None:
        title_stmt = etree.Element(f"{{{TEI_NS}}}titleStmt")
        file_desc.insert(0, title_stmt)
        etree.SubElement(title_stmt, f"{{{TEI_NS}}}title").text = source_name
    publication_stmt = find_child(file_desc, "publicationStmt")
    if publication_stmt is None:
        publication_stmt = etree.Element(f"{{{TEI_NS}}}publicationStmt")
        title_index = list(file_desc).index(title_stmt)
        file_desc.insert(title_index + 1, publication_stmt)
        etree.SubElement(publication_stmt, f"{{{TEI_NS}}}publisher").text = "Perseus Digital Library"
        availability = etree.SubElement(publication_stmt, f"{{{TEI_NS}}}availability")
        etree.SubElement(availability, f"{{{TEI_NS}}}p").text = (
            "Converted from the legacy Perseus TEI P4 American collection."
        )
    source_desc = find_child(file_desc, "sourceDesc")
    if source_desc is None:
        source_desc = etree.SubElement(file_desc, f"{{{TEI_NS}}}sourceDesc")
        etree.SubElement(source_desc, f"{{{TEI_NS}}}p").text = f"Legacy source file: {source_name}."
    return header


def replace_refs_decl(header: etree._Element) -> None:
    encoding = find_child(header, "encodingDesc")
    if encoding is None:
        encoding = etree.SubElement(header, f"{{{TEI_NS}}}encodingDesc")
    for child in list(encoding):
        if isinstance(child.tag, str) and local_name(child.tag) == "refsDecl":
            encoding.remove(child)
    refs = etree.SubElement(encoding, f"{{{TEI_NS}}}refsDecl")
    refs.set(XML_ID, "CTS")
    wrapper = etree.SubElement(refs, f"{{{TEI_NS}}}citeStructure")
    wrapper.set("match", "/TEI/text/body")
    wrapper.set("use", "@n")
    chapter = etree.SubElement(wrapper, f"{{{TEI_NS}}}citeStructure")
    chapter.set("unit", "chapter")
    chapter.set("n", "chunk")
    chapter.set("delim", ":")
    chapter.set("match", "div[@type='chapter']")
    chapter.set("use", "@n")


def ensure_text_and_chapters(root: etree._Element, urn: str, report: dict) -> None:
    text = find_child(root, "text")
    if text is None:
        raise ValueError("No text element")
    text.set(XML_LANG, "eng")
    body = find_child(text, "body")
    if body is None:
        raise ValueError("No body element")
    body.set(XML_ID if False else f"{{{XML_NS}}}base", urn)

    direct_divs = [c for c in body if isinstance(c.tag, str) and local_name(c.tag) == "div"]
    if not direct_divs:
        wrapper = etree.Element(f"{{{TEI_NS}}}div", type="chapter", subtype="chapter", n="1")
        wrapper.text = body.text
        body.text = None
        for child in list(body):
            body.remove(child)
            wrapper.append(child)
        body.append(wrapper)
        direct_divs = [wrapper]
        report["wrapped_body_as_chapter"] = True

    used = set()
    renumbered = 0
    for index, div in enumerate(direct_divs, 1):
        original_n = (div.get("n") or "").strip()
        n = original_n or str(index)
        if n in used:
            n = str(index)
            while n in used:
                n = f"{index}_{len(used) + 1}"
            renumbered += 1
        used.add(n)
        div.set("n", n)
        div.set("type", "chapter")
        div.set("subtype", "chapter")
    report["top_level_chapters"] = len(direct_divs)
    report["renumbered_duplicate_chapters"] = renumbered


def add_revision(root: etree._Element, source_name: str) -> None:
    header = find_child(root, "teiHeader")
    revision = find_child(header, "revisionDesc")
    if revision is None:
        revision = etree.SubElement(header, f"{{{TEI_NS}}}revisionDesc")
    change = etree.SubElement(revision, f"{{{TEI_NS}}}change")
    change.set("when", "2026-09-21")
    change.text = f"Converted from TEI P4 source {source_name}; CTS metadata and citations added."


def cts_textgroup(group_id: str, author: str) -> etree._ElementTree:
    root = etree.Element(f"{{{CTS_NS}}}textgroup", nsmap={"ti": CTS_NS})
    root.set("urn", f"urn:cts:americanLit:{group_id}")
    name = etree.SubElement(root, f"{{{CTS_NS}}}groupname")
    name.set(XML_LANG, "eng")
    name.text = author
    return etree.ElementTree(root)


def cts_work(group_id: str, work_id: str, title: str, description: str) -> etree._ElementTree:
    work_urn = f"urn:cts:americanLit:{group_id}.{work_id}"
    version_urn = f"{work_urn}.perseus-eng1"
    root = etree.Element(f"{{{CTS_NS}}}work", nsmap={"ti": CTS_NS})
    root.set("groupUrn", f"urn:cts:americanLit:{group_id}")
    root.set("projid", f"americanLit:{work_id}")
    root.set("urn", work_urn)
    root.set(XML_LANG, "eng")
    title_el = etree.SubElement(root, f"{{{CTS_NS}}}title")
    title_el.set(XML_LANG, "eng")
    title_el.text = title
    edition = etree.SubElement(root, f"{{{CTS_NS}}}edition")
    edition.set("urn", version_urn)
    edition.set("workUrn", work_urn)
    edition.set(XML_LANG, "eng")
    label = etree.SubElement(edition, f"{{{CTS_NS}}}label")
    label.set(XML_LANG, "eng")
    label.text = title
    desc = etree.SubElement(edition, f"{{{CTS_NS}}}description")
    desc.set(XML_LANG, "eng")
    desc.text = description or "Legacy Perseus nineteenth-century American collection."
    return etree.ElementTree(root)


def write_xml(tree: etree._ElementTree, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tree.write(
        str(path),
        encoding="UTF-8",
        xml_declaration=True,
        pretty_print=True,
    )


def clean_legacy_to_temp(source: Path) -> Tuple[Path, Counter]:
    """Create a DTD-free, entity-expanded temporary XML file without loading it all."""
    unknown = Counter()
    temp = tempfile.NamedTemporaryFile(prefix="american-p4-", suffix=".xml", delete=False)
    temp_path = Path(temp.name)
    temp.close()
    header_buffer = ""
    header_done = False

    def replace_entities(value: str) -> str:
        def replace(match: re.Match) -> str:
            name = match.group(1)
            if name in BUILTIN_ENTITIES:
                return match.group(0)
            result = entity_value(name)
            if result is None:
                unknown[name] += 1
                return f"[UNRESOLVED ENTITY: {name}]"
            return result
        return ENTITY_RE.sub(replace, INVALID_XML_CONTROL_RE.sub("�", value))

    with source.open("r", encoding="utf-8", errors="replace") as src, temp_path.open(
        "w", encoding="utf-8"
    ) as dst:
        for line in src:
            if not header_done:
                header_buffer += line
                cleaned = DOCTYPE_RE.sub("", header_buffer, count=1)
                if cleaned != header_buffer or "<!DOCTYPE" not in header_buffer:
                    dst.write(replace_entities(cleaned.lstrip("\ufeff")))
                    header_buffer = ""
                    header_done = True
                elif len(header_buffer) > 1024 * 1024:
                    raise ValueError("DOCTYPE exceeds 1 MB and cannot be removed safely")
            else:
                dst.write(replace_entities(line))
        if header_buffer:
            dst.write(replace_entities(DOCTYPE_RE.sub("", header_buffer, count=1)))
    return temp_path, unknown


class StreamingP4Handler(xml.sax.handler.ContentHandler):
    """SAX transformer used for sources too large for a practical DOM rewrite."""

    def __init__(self, output, urn: str, source_name: str):
        super().__init__()
        self.writer = XMLGenerator(output, encoding="utf-8", short_empty_elements=True)
        self.urn = urn
        self.source_name = source_name
        self.stack: List[str] = []
        self.suppress_depth = 0
        self.has_publication = False
        self.has_revision = False
        self.used_ids = set()
        self.chapter_ids = set()
        self.chapters = 0
        self.duplicate_chapters = 0
        self.deduplicated_ids = 0

    def startDocument(self):
        self.writer.startDocument()

    def endDocument(self):
        self.writer.endDocument()

    @staticmethod
    def renamed(name: str) -> str:
        if DIV_RE.fullmatch(name):
            return "div"
        if name == "TEI.2":
            return "TEI"
        if name == "foreName":
            return "forename"
        return name

    def converted_attrs(self, name: str, attrs) -> dict:
        result = {}
        for key in attrs.getNames():
            value = attrs.getValue(key)
            if key == "lang":
                result["xml:lang"] = "grc" if value in {"greek", "gr"} else value
            elif key == "id" and name == "language":
                result["ident"] = "grc" if value in {"greek", "gr"} else value
            elif key == "id":
                candidate = re.sub(r"[^A-Za-z0-9_.-]", "_", value)
                if not candidate or not re.match(r"[A-Za-z_]", candidate):
                    candidate = "id_" + candidate
                base, serial = candidate, 2
                while candidate in self.used_ids:
                    candidate = f"{base}_{serial}"
                    serial += 1
                    self.deduplicated_ids += 1
                self.used_ids.add(candidate)
                result["xml:id"] = candidate
            else:
                result[key] = value
        return result

    def startElement(self, source_name: str, attrs):
        parent = self.stack[-1] if self.stack else None
        if self.suppress_depth:
            self.suppress_depth += 1
            self.stack.append(source_name)
            return
        if source_name == "refsDecl" and parent == "encodingDesc":
            self.suppress_depth = 1
            self.stack.append(source_name)
            return

        name = self.renamed(source_name)
        converted = self.converted_attrs(name, attrs)
        if name == "TEI":
            converted["xmlns"] = TEI_NS
        elif name == "text":
            converted["xml:lang"] = "eng"
        elif name == "body":
            converted["xml:base"] = self.urn
        elif name == "publicationStmt":
            self.has_publication = True
        elif name == "revisionDesc":
            self.has_revision = True
        if name == "div" and parent == "body":
            self.chapters += 1
            n = (converted.get("n") or str(self.chapters)).strip()
            if n in self.chapter_ids:
                n = str(self.chapters)
                while n in self.chapter_ids:
                    n = f"{self.chapters}_{len(self.chapter_ids) + 1}"
                self.duplicate_chapters += 1
            self.chapter_ids.add(n)
            converted.update({"n": n, "type": "chapter", "subtype": "chapter"})
        self.writer.startElement(name, AttributesImpl(converted))
        self.stack.append(source_name)

    def _simple_element(self, name: str, attrs=None, text=None):
        self.writer.startElement(name, AttributesImpl(attrs or {}))
        if text:
            self.writer.characters(text)
        self.writer.endElement(name)

    def _write_refs(self):
        self.writer.startElement("refsDecl", AttributesImpl({"xml:id": "CTS"}))
        self.writer.startElement(
            "citeStructure", AttributesImpl({"match": "/TEI/text/body", "use": "@n"})
        )
        self._simple_element(
            "citeStructure",
            {
                "unit": "chapter",
                "n": "chunk",
                "delim": ":",
                "match": "div[@type='chapter']",
                "use": "@n",
            },
        )
        self.writer.endElement("citeStructure")
        self.writer.endElement("refsDecl")

    def _write_publication(self):
        self.writer.startElement("publicationStmt", AttributesImpl({}))
        self._simple_element("publisher", text="Perseus Digital Library")
        self.writer.startElement("availability", AttributesImpl({}))
        self._simple_element(
            "p", text="Converted from the legacy Perseus TEI P4 American collection."
        )
        self.writer.endElement("availability")
        self.writer.endElement("publicationStmt")

    def _write_change(self):
        self._simple_element(
            "change",
            {"when": "2026-09-21"},
            f"Converted from TEI P4 source {self.source_name}; CTS metadata and citations added.",
        )

    def endElement(self, source_name: str):
        if self.suppress_depth:
            self.suppress_depth -= 1
            self.stack.pop()
            return
        name = self.renamed(source_name)
        if name == "encodingDesc":
            self._write_refs()
        if name == "revisionDesc":
            self._write_change()
        if name == "teiHeader" and not self.has_revision:
            self.writer.startElement("revisionDesc", AttributesImpl({}))
            self._write_change()
            self.writer.endElement("revisionDesc")
        self.writer.endElement(name)
        if name == "titleStmt" and not self.has_publication:
            self._write_publication()
        self.stack.pop()

    def characters(self, content: str):
        if not self.suppress_depth:
            self.writer.characters(content)

    def processingInstruction(self, target: str, data: str):
        if not self.suppress_depth:
            self.writer.processingInstruction(target, data)


def convert_large(source: Path, destination: Path, urn: str) -> dict:
    cleaned, unknown = clean_legacy_to_temp(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with destination.open("wb") as output:
            handler = StreamingP4Handler(output, urn, source.name)
            parser = xml.sax.make_parser()
            parser.setFeature(xml.sax.handler.feature_namespaces, False)
            for feature in (
                xml.sax.handler.feature_external_ges,
                xml.sax.handler.feature_external_pes,
            ):
                try:
                    parser.setFeature(feature, False)
                except (xml.sax.SAXNotRecognizedException, xml.sax.SAXNotSupportedException):
                    pass
            parser.setContentHandler(handler)
            parser.parse(str(cleaned))
        return {
            "recovered": False,
            "parser_messages": [],
            "unknown_entities": dict(unknown),
            "deduplicated_xml_ids": handler.deduplicated_ids,
            "top_level_chapters": handler.chapters,
            "renumbered_duplicate_chapters": handler.duplicate_chapters,
            "streaming_conversion": True,
        }
    finally:
        cleaned.unlink(missing_ok=True)


def source_files(source: Path) -> List[Path]:
    return sorted(
        p for p in source.glob("*.xml") if not p.name.startswith(".") and not p.name.endswith(".org.xml")
    )


def unique_ids(records: List[dict]) -> None:
    author_groups = defaultdict(list)
    for record in records:
        author_groups[slugify(record["author"], "anonymous")].append(record)
    for base, members in sorted(author_groups.items()):
        authors = sorted({m["author"] for m in members})
        author_to_id = {}
        for author in authors:
            candidate = base
            if len(authors) > 1:
                digest = hashlib.sha1(author.encode("utf-8")).hexdigest()[:8]
                candidate = f"{base}_{digest}"
            author_to_id[author] = candidate
        for member in members:
            member["group_id"] = author_to_id[member["author"]]

    seen = set()
    for record in sorted(records, key=lambda r: r["source"]):
        base = version_stem(Path(record["source"]))
        candidate = base
        serial = 2
        while (record["group_id"], candidate) in seen:
            candidate = f"{base}_{serial}"
            serial += 1
        seen.add((record["group_id"], candidate))
        record["work_id"] = candidate


def inventory(source: Path, allow_recovery: bool) -> Tuple[List[dict], List[dict]]:
    records, failures = [], []
    for path in source_files(source):
        try:
            tree, parse_report = parse_legacy(path, allow_recovery=allow_recovery)
            metadata = extract_metadata(tree, path)
            records.append({"source": path.name, **metadata, "parse_report": parse_report})
        except Exception as exc:
            failures.append({"source": path.name, "status": "skipped", "error": str(exc)})
    unique_ids(records)
    return records, failures


def convert_one(source: Path, output: Path, record: dict, allow_recovery: bool) -> dict:
    urn = f"urn:cts:americanLit:{record['group_id']}.{record['work_id']}.perseus-eng1"
    work_dir = output / "data" / record["group_id"] / record["work_id"]
    version_name = f"{record['group_id']}.{record['work_id']}.perseus-eng1.xml"
    version_path = work_dir / version_name
    if source.stat().st_size >= 64 * 1024 * 1024:
        parse_report = convert_large(source, version_path, urn)
        report = {"source": source.name, "status": "converted", **parse_report}
    else:
        tree, parse_report = parse_legacy(source, allow_recovery=allow_recovery)
        root = set_tei_namespace(tree.getroot())
        tree._setroot(root)
        report = {"source": source.name, "status": "converted", **parse_report}
        migrate_attributes(root, report)
        header = ensure_header(root, source.name)
        replace_refs_decl(header)
        ensure_text_and_chapters(root, urn, report)
        add_revision(root, source.name)
        write_xml(tree, version_path)
    write_xml(
        cts_work(
            record["group_id"],
            record["work_id"],
            record["title"],
            record["source_desc"],
        ),
        work_dir / "__cts__.xml",
    )
    report.update(
        {
            "author": record["author"],
            "title": record["title"],
            "group_id": record["group_id"],
            "work_id": record["work_id"],
            "urn": urn,
            "output": str(version_path.relative_to(output)),
        }
    )
    return report


def write_reports(output: Path, reports: List[dict]) -> None:
    manifest_rows = [r for r in reports if r.get("status") == "converted"]
    fields = ["source", "author", "title", "group_id", "work_id", "urn", "output"]
    with (output / "conversion_manifest.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in manifest_rows:
            writer.writerow({key: row.get(key, "") for key in fields})
    summary = {
        "converted": len(manifest_rows),
        "skipped": sum(r.get("status") == "skipped" for r in reports),
        "recovered": sum(bool(r.get("recovered")) for r in reports),
        "unknown_entity_types": sorted(
            {name for r in reports for name in r.get("unknown_entities", {})}
        ),
        "reports": reports,
    }
    (output / "conversion_report.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def build(args: argparse.Namespace) -> int:
    source = args.source.resolve()
    output = args.output.resolve()
    if not source.is_dir():
        raise SystemExit(f"Source directory does not exist: {source}")
    data_dir = output / "data"
    if data_dir.exists():
        if not args.force:
            raise SystemExit(f"Output already exists: {data_dir}; pass --force to replace it")
        shutil.rmtree(data_dir)
    output.mkdir(parents=True, exist_ok=True)

    records, failures = inventory(source, allow_recovery=not args.strict)
    if args.limit:
        records = records[: args.limit]
    reports = list(failures)
    groups = {}
    for number, record in enumerate(records, 1):
        path = source / record["source"]
        try:
            report = convert_one(path, output, record, allow_recovery=not args.strict)
            reports.append(report)
            groups[record["group_id"]] = record["author"]
            if number % 25 == 0 or number == len(records):
                print(f"[{number}/{len(records)}] {record['source']}", flush=True)
        except Exception as exc:
            reports.append({"source": record["source"], "status": "skipped", "error": str(exc)})
            print(f"[skip] {record['source']}: {exc}", file=sys.stderr, flush=True)

    for group_id, author in groups.items():
        write_xml(cts_textgroup(group_id, author), data_dir / group_id / "__cts__.xml")
    write_reports(output, reports)
    converted = sum(r.get("status") == "converted" for r in reports)
    skipped = sum(r.get("status") == "skipped" for r in reports)
    print(f"Converted {converted} file(s); skipped {skipped}; textgroups {len(groups)}")
    return 1 if args.strict and skipped else 0


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Directory of legacy *.xml files")
    parser.add_argument("--output", type=Path, required=True, help="Canonical repository root")
    parser.add_argument("--force", action="store_true", help="Replace the generated data directory")
    parser.add_argument("--strict", action="store_true", help="Disable XML recovery and fail on skipped files")
    parser.add_argument("--limit", type=int, help="Convert only the first N discovered files")
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(build(parse_args()))
