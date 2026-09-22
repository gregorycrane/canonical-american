PYTHON ?= python3
SOURCE ?= /Users/gcrane/Desktop/OldMacintoshHD/sgml/texts/cwar/xml

.PHONY: build validate test

build:
	$(PYTHON) tools/convert_p4.py --source "$(SOURCE)" --output . --force

validate:
	$(PYTHON) tools/validate_corpus.py .

test:
	$(PYTHON) tests/test_convert_p4.py
