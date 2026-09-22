PYTHON ?= python3
SOURCE ?= /Users/gcrane/Desktop/OldMacintoshHD/sgml/texts/cwar/xml
HARPER_SOURCE := sources/harpgaz_1855.p5.xml.gz
HARPER_TEI := data/j_calvin_smith/harpgaz_1855/j_calvin_smith.harpgaz_1855.perseus-eng1.xml

.PHONY: build harper beta-code validate test

build:
	$(PYTHON) tools/convert_p4.py --source "$(SOURCE)" --output . --force

harper:
	$(PYTHON) tools/build_harper_gazetteer.py \
	  $(HARPER_SOURCE) \
	  --reading-output $(HARPER_TEI) \
	  --database gazetteers/harpgaz_1855.sqlite3

beta-code:
	$(PYTHON) tools/convert_beta_code.py data \
	  --gzip sources/harpgaz_1855.p5.xml.gz

validate:
	$(PYTHON) tools/validate_corpus.py .

test:
	$(PYTHON) -m unittest discover -s tests
