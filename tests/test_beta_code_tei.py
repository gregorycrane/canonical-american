import unittest

from lxml import etree

from tools import beta_code_tei


class BetaCodeTEITests(unittest.TestCase):
    def test_converts_beta_code_and_preserves_nested_markup(self):
        root = etree.fromstring(
            '''<TEI xmlns="http://www.tei-c.org/ns/1.0"><text><body><p>
            <foreign xml:lang="grc">*)afrodi/th ga/mou <milestone n="1"/>plokai=s h(/detai.</foreign>
            </p></body></text></TEI>'''.encode()
        )
        tree = etree.ElementTree(root)
        changes = beta_code_tei.convert_tree(tree)
        foreign = root.find(".//{http://www.tei-c.org/ns/1.0}foreign")
        self.assertEqual("".join(foreign.itertext()).strip(), "Ἀφροδίτη γάμου πλοκαῖς ἥδεται.")
        self.assertEqual(len(changes), 1)
        self.assertIsNotNone(foreign.find("{http://www.tei-c.org/ns/1.0}milestone"))

    def test_skips_unicode_greek_and_known_placeholder(self):
        root = etree.fromstring(
            '''<TEI xmlns="http://www.tei-c.org/ns/1.0"><text><body><p>
            <foreign xml:lang="grc">Ἑλλάς</foreign><foreign xml:lang="grc">Zzz</foreign>
            </p></body></text></TEI>'''.encode()
        )
        self.assertEqual(beta_code_tei.convert_tree(etree.ElementTree(root)), [])

    def test_normalizes_legacy_smooth_breathing_and_mark_order(self):
        self.assertEqual(beta_code_tei.beta_to_unicode("i>sonomi/a"), "ἰσονομία")
        self.assertEqual(beta_code_tei.beta_to_unicode("(espe/ra"), "ἑσπέρα")
        self.assertEqual(beta_code_tei.beta_to_unicode("ou=("), "οὗ")


if __name__ == "__main__":
    unittest.main()
