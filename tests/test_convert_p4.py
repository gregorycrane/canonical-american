import importlib.util
import tempfile
import unittest
from pathlib import Path

from lxml import etree


MODULE_PATH = Path(__file__).parents[1] / "tools" / "convert_p4.py"
SPEC = importlib.util.spec_from_file_location("convert_p4", MODULE_PATH)
convert_p4 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(convert_p4)


SAMPLE = '''<?xml version="1.0"?>
<!DOCTYPE TEI.2 [<!ENTITY mdash "&#x2014;">]>
<TEI.2>
 <teiHeader><fileDesc><titleStmt><title>A Test &mdash; Work</title><author>Jane Doe</author></titleStmt><sourceDesc><p>Boston, 1890.</p></sourceDesc></fileDesc><encodingDesc><refsDecl><state unit="chapter"/></refsDecl></encodingDesc></teiHeader>
 <text><body><div1 id="c.1" type="chapter" n="1"><pb id="p.1" n="1"/><head>One</head><p>Text.</p></div1></body></text>
</TEI.2>'''


class ConvertP4Tests(unittest.TestCase):
    def test_converts_sample_and_builds_cts(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            source = temp / "source"
            output = temp / "output"
            source.mkdir()
            (source / "doe.test.ie.xml").write_text(SAMPLE, encoding="utf-8")
            args = convert_p4.parse_args(["--source", str(source), "--output", str(output)])
            self.assertEqual(convert_p4.build(args), 0)
            files = list((output / "data").glob("*/*/*.xml"))
            version = next(path for path in files if path.name != "__cts__.xml")
            tree = etree.parse(str(version))
            ns = {"tei": convert_p4.TEI_NS}
            self.assertEqual(tree.getroot().tag, f"{{{convert_p4.TEI_NS}}}TEI")
            self.assertEqual(tree.find("./tei:text", ns).get(convert_p4.XML_LANG), "eng")
            body = tree.find("./tei:text/tei:body", ns)
            self.assertTrue(body.get(f"{{{convert_p4.XML_NS}}}base").startswith("urn:cts:americanLit:"))
            self.assertEqual(body.find("./tei:div", ns).get("type"), "chapter")
            self.assertIsNotNone(tree.find(".//tei:refsDecl[@xml:id='CTS']", {**ns, "xml": convert_p4.XML_NS}))
            self.assertTrue((version.parent / "__cts__.xml").exists())
            self.assertTrue((version.parent.parent / "__cts__.xml").exists())

    def test_streaming_converter_preserves_structure(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            source = temp / "large.xml"
            output = temp / "converted.xml"
            source.write_text(SAMPLE, encoding="utf-8")
            urn = "urn:cts:americanLit:jane_doe.test.perseus-eng1"
            report = convert_p4.convert_large(source, output, urn)
            tree = etree.parse(str(output))
            ns = {"tei": convert_p4.TEI_NS}
            self.assertTrue(report["streaming_conversion"])
            self.assertEqual(tree.getroot().tag, f"{{{convert_p4.TEI_NS}}}TEI")
            self.assertEqual(
                tree.find("./tei:text/tei:body", ns).get(f"{{{convert_p4.XML_NS}}}base"), urn
            )
            self.assertIsNotNone(tree.find(".//tei:refsDecl", ns))
            self.assertIsNotNone(tree.find(".//tei:publicationStmt", ns))
            self.assertIsNotNone(tree.find(".//tei:revisionDesc", ns))


if __name__ == "__main__":
    unittest.main()
