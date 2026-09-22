"""Convert Beta Code contained in TEI Greek foreign-language elements."""

from __future__ import annotations

import gzip
import os
import re
import shutil
import tempfile
import unicodedata
from pathlib import Path

import beta_code
from lxml import etree


TEI_NS = "http://www.tei-c.org/ns/1.0"
XML_NS = "http://www.w3.org/XML/1998/namespace"
XML_LANG = f"{{{XML_NS}}}lang"
GREEK_RE = re.compile(r"[\u0370-\u03ff\u1f00-\u1fff]")
LETTER_RE = re.compile(r"[A-Za-z]")
KNOWN_NON_GREEK_PLACEHOLDERS = {"Zzz"}
DIACRITIC_RUN_RE = re.compile(r"([aehiouw]+)([()/\\=|+]+)", re.I)


def is_beta_code_candidate(value: str) -> bool:
    stripped = value.strip()
    return bool(
        stripped
        and stripped not in KNOWN_NON_GREEK_PLACEHOLDERS
        and not GREEK_RE.search(stripped)
        and LETTER_RE.search(stripped)
    )


def _normalize_legacy_beta(value: str) -> str:
    # This collection also uses ``>`` for smooth breathing and, once, puts a
    # breathing before a lowercase initial vowel. Normalize those older forms
    # to the conventional Beta Code understood by beta-code-py.
    value = re.sub(r"(?<=[aehiouw])>", ")", value, flags=re.I)
    value = re.sub(r"(?<![A-Za-z*])([()])([aehiouw])", r"\2\1", value, flags=re.I)

    order = {"(": 0, ")": 0, "+": 1, "/": 2, "\\": 2, "=": 2, "|": 3}

    def reorder(match: re.Match) -> str:
        marks = "".join(sorted(match.group(2), key=lambda mark: order[mark]))
        return match.group(1) + marks

    return DIACRITIC_RUN_RE.sub(reorder, value)


def beta_to_unicode(value: str) -> str:
    normalized = _normalize_legacy_beta(value)
    return unicodedata.normalize("NFC", beta_code.beta_code_to_greek(normalized))


def _convert_text_nodes(element: etree._Element) -> None:
    if element.text:
        element.text = beta_to_unicode(element.text)
    for child in element:
        _convert_text_nodes(child)
        if child.tail:
            child.tail = beta_to_unicode(child.tail)


def convert_tree(tree: etree._ElementTree) -> list[dict]:
    changes = []
    for element in tree.findall(f".//{{{TEI_NS}}}foreign"):
        if element.get(XML_LANG) != "grc":
            continue
        original = "".join(element.itertext())
        if not is_beta_code_candidate(original):
            continue
        _convert_text_nodes(element)
        converted = "".join(element.itertext())
        changes.append({"original": original, "unicode": converted})
    return changes


def _atomic_write(tree: etree._ElementTree, path: Path) -> None:
    with tempfile.NamedTemporaryFile(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False
    ) as handle:
        temporary = Path(handle.name)
    try:
        tree.write(
            str(temporary),
            encoding="UTF-8",
            xml_declaration=True,
            pretty_print=True,
        )
        etree.parse(str(temporary), etree.XMLParser(huge_tree=True))
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def convert_xml_file(path: Path, write: bool = True) -> list[dict]:
    parser = etree.XMLParser(load_dtd=False, resolve_entities=False, no_network=True, huge_tree=True)
    tree = etree.parse(str(path), parser)
    changes = convert_tree(tree)
    if changes and write:
        _atomic_write(tree, path)
    return changes


def convert_gzip_xml(path: Path, write: bool = True) -> list[dict]:
    with tempfile.TemporaryDirectory(prefix="american-beta-code-") as temp_dir:
        expanded_name = path.name[:-3] if path.name.endswith(".gz") else path.name
        expanded = Path(temp_dir) / expanded_name
        with gzip.open(path, "rb") as source, expanded.open("wb") as destination:
            shutil.copyfileobj(source, destination, 1024 * 1024)
        changes = convert_xml_file(expanded, write=True)
        if changes and write:
            with tempfile.NamedTemporaryFile(
                prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False
            ) as handle:
                temporary = Path(handle.name)
            try:
                with expanded.open("rb") as source, gzip.open(
                    temporary, "wb", compresslevel=9
                ) as destination:
                    shutil.copyfileobj(source, destination, 1024 * 1024)
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
        return changes
