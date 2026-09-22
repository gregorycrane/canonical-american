import importlib.util
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "tools" / "audit_entities.py"
SPEC = importlib.util.spec_from_file_location("audit_entities", MODULE_PATH)
audit_entities = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit_entities)


SAMPLE = """<?xml version="1.0"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0">
 <teiHeader/>
 <text><body><div type="chapter" n="1">
  <p>
   <persName key="wikidata,Q1000" reg="default:Smith,John,,,">John Smith</persName> met
   <persName reg="nomatch:0">a stranger</persName> in
   <placeName key="tgn,7013604">Cincinnati</placeName> near the
   <orgName>river company</orgName>.
   <rs type="place">the river</rs> and <rs>something unclear</rs>.
  </p>
 </div></body></text>
</TEI>"""


class AuditEntitiesTests(unittest.TestCase):
    def test_audit_file_counts_and_labels(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "sample.perseus-eng1.xml"
            path.write_text(SAMPLE, encoding="utf-8")
            stats = audit_entities.audit_file(path)

            self.assertEqual(stats["persName"]["total"], 2)
            self.assertEqual(stats["persName"]["with_key"], 1)
            self.assertEqual(
                stats["persName"]["reg_labels"], {"default": 1, "nomatch": 1}
            )

            self.assertEqual(stats["placeName"]["total"], 1)
            self.assertEqual(stats["placeName"]["with_key"], 1)

            self.assertEqual(stats["orgName"]["total"], 1)
            self.assertEqual(stats["orgName"]["with_key"], 0)

            self.assertEqual(stats["rs"]["total"], 2)
            self.assertEqual(stats["rs"]["types"], {"place": 1, "notype": 1})

    def test_audit_walks_corpus_and_aggregates(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            work_dir = root / "data" / "an_author" / "a_work"
            work_dir.mkdir(parents=True)
            (work_dir / "an_author.a_work.perseus-eng1.xml").write_text(
                SAMPLE, encoding="utf-8"
            )

            report = audit_entities.audit(root)

            self.assertEqual(report["file_count"], 1)
            self.assertEqual(report["errors"], [])
            self.assertEqual(report["totals"]["persName"]["total"], 2)
            self.assertEqual(report["totals"]["rs"]["types"], {"place": 1, "notype": 1})

    def test_reg_label_handles_missing_and_plain_values(self):
        self.assertEqual(audit_entities.reg_label(None), "missing")
        self.assertEqual(audit_entities.reg_label(""), "missing")
        self.assertEqual(audit_entities.reg_label("mostcommon:Whitman,,,,:6"), "mostcommon")


if __name__ == "__main__":
    unittest.main()
