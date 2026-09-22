# canonical-american

TEI P5 and CTS conversion of the Perseus nineteenth-century American collection.

The legacy source is kept outside this repository. `tools/convert_p4.py` converts
the TEI P4 files into a canonical repository layout, generates CTS textgroup and
work inventories, and writes CSV/JSON audit reports.

## Build

```bash
python3 -m pip install -r requirements.txt
python3 tools/convert_p4.py \
  --source /Users/gcrane/Desktop/OldMacintoshHD/sgml/texts/cwar/xml \
  --output . \
  --force
```

The converter:

- removes the obsolete external DTD and expands legacy named entities;
- migrates `TEI.2`, `div1`–`div6`, `id`, and `lang` constructs to TEI P5;
- preserves page breaks and existing named-entity markup;
- adds `text/@xml:lang`, `body/@xml:base`, and a modern CTS
  `refsDecl/citeStructure` for top-level chapters;
- emits `data/<textgroup>/<work>/__cts__.xml` and version files;
- records every source-to-CTS mapping in `conversion_manifest.csv`;
- records recoveries, skipped files, unknown entities, and structural changes in
  `conversion_report.json`.

The collection namespace is `urn:cts:americanLit:`. Generated documents use the
version identifier `perseus-eng1`.

## Harper's gazetteer

Harper's 1855 gazetteer is both a historical reading text and a database of
77,000+ place entries. After the corpus conversion, build both forms with:

```bash
make harper
```

The repository stores the original converted P5 source as the ordinary,
GitHub-safe compressed file `sources/harpgaz_1855.p5.xml.gz`. The command
creates the ignored build products `gazetteers/harpgaz_1855.sqlite3`, including
an FTS5 full-text index, and the expanded TEI reading edition with citations of the form
`letter.entry-group` (100 entries per group). Page breaks, entry IDs, named
entities, TGN keys, and complete TEI entry markup are preserved. The bounded
groups keep individual MVP reading views responsive; the SQLite form supports
headword, place-name, and full-text lookup without treating a whole alphabet
letter as one passage.

## Check an existing build

```bash
python3 tools/validate_corpus.py .
python3 -m unittest discover -s tests
```

The same steps are available as `make build`, `make validate`, and `make test`.

## Publish

This checkout is already connected to
`https://github.com/gregorycrane/canonical-american.git`:

```bash
git add README.md Makefile requirements.txt tools tests .gitignore
git commit -m "Add scalable TEI P4 to P5 and CTS converter"
git push -u origin main
```

Generated corpus files can then be committed separately. GitHub rejects an
ordinary Git blob over 100 MB; run `find data -type f -size +95M -print` before
that commit and use Git LFS or split/omit any reported source after review.
