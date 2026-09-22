PYTHON ?= python3
SOURCE ?= /Users/gcrane/Desktop/OldMacintoshHD/sgml/texts/cwar/xml
.PHONY: build beta-code validate test audit-entities

build:
	$(PYTHON) tools/convert_p4.py --source "$(SOURCE)" --output . --force

audit-entities:
	$(PYTHON) tools/audit_entities.py . --output entity_audit_baseline.json

beta-code:
	$(PYTHON) tools/convert_beta_code.py data

validate:
	$(PYTHON) tools/validate_corpus.py .

test:
	$(PYTHON) -m unittest discover -s tests
