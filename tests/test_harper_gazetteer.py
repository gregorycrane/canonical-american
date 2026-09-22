import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path

from lxml import etree


MODULE_PATH = Path(__file__).parents[1] / "tools" / "build_harper_gazetteer.py"
SPEC = importlib.util.spec_from_file_location("build_harper_gazetteer", MODULE_PATH)
harper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(harper)


SAMPLE = '''<?xml version="1.0"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0">
 <teiHeader><fileDesc><titleStmt><title>Gazetteer</title></titleStmt>
 <publicationStmt><p>Test</p></publicationStmt><sourceDesc><p>Test</p></sourceDesc></fileDesc>
 <encodingDesc><refsDecl xml:id="CTS"><citeStructure match="/TEI/text/body" use="@n"/></refsDecl></encodingDesc></teiHeader>
 <text xml:lang="eng"><body xml:base="urn:cts:americanLit:test.gaz.perseus-eng1">
  <div type="chapter" n="A"><pb n="1"/><head>A</head>
   <div type="entry" xml:id="alpha"><head>Alpha</head><p><placeName key="tgn,1">Alpha</placeName> text.</p>
    <div type="entry" xml:id="alpha-sub"><head>Alpha Minor</head><p>Nested subentry.</p></div>
   </div>
   <div type="entry" xml:id="beta"><head>Beta</head><p>More.</p><pb n="2"/></div>
   <div type="entry" xml:id="gamma"><head>Gamma</head><p>Last.</p></div>
  </div>
 </body></text>
</TEI>'''


class HarperGazetteerTests(unittest.TestCase):
    def test_database_and_bounded_reading_chunks(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            source = temp / "source.xml"
            reading = temp / "reading.xml"
            database = temp / "gazetteer.sqlite3"
            source.write_text(SAMPLE, encoding="utf-8")

            summary = harper.build_database(source, database)
            harper.build_reading_edition(source, reading, entries_per_chunk=2)
            self.assertEqual(summary["entries"], 3)

            connection = sqlite3.connect(database)
            self.assertEqual(
                connection.execute(
                    "SELECT entry_id FROM entries_fts WHERE entries_fts MATCH 'Alpha'"
                ).fetchall(),
                [("alpha",)],
            )
            self.assertEqual(
                connection.execute(
                    "SELECT first_page, last_page FROM entries WHERE entry_id='beta'"
                ).fetchone(),
                ("1", "2"),
            )
            connection.close()

            tree = etree.parse(str(reading))
            ns = {"tei": harper.TEI_NS}
            groups = tree.findall(".//tei:div[@type='chapter']", ns)
            self.assertEqual([len(g.findall("./tei:div[@type='entry']", ns)) for g in groups], [2, 1])
            self.assertEqual(
                tree.xpath("string(//tei:refsDecl/tei:citeStructure/tei:citeStructure/@unit)", namespaces=ns),
                "letter",
            )


if __name__ == "__main__":
    unittest.main()
