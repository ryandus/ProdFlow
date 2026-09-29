# ProdFlow

[![CI](https://github.com/ryandus/ProdFlow/actions/workflows/ci.yml/badge.svg)](https://github.com/ryandus/ProdFlow/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.9%2B-blue) ![Dependencies](https://img.shields.io/badge/dependencies-none-brightgreen) ![License](https://img.shields.io/badge/license-MIT-lightgrey)

**Defensible eDiscovery production tooling:** load file QC, TAR validation statistics, and matter estimates, with a tamper-evident audit trail.

ProdFlow covers three questions that come up on almost every matter:

| Question | Command | Output |
|---|---|---|
| *Is this production clean enough to serve, or to load?* | `prodflow qc` | QC report (Markdown + JSON), pass/fail exit code |
| *Can we defend the review's recall?* | `prodflow elusion` | Exact confidence intervals + methodology memo |
| *What will this matter cost and how long will review take?* | `prodflow estimate` | Low/high volume, hours and cost with every assumption visible |

It is pure Python standard library. There's nothing to install beyond Python, and nothing the output depends on changes between machines.

---

## Quick start

```bash
git clone https://github.com/ryandus/ProdFlow.git && cd ProdFlow
python samples/generate.py                      # builds clean + defective synthetic productions
python -m prodflow qc samples/defective_production --out ~/prodflow_out
```

```
FAIL: 12 records, 9 errors, 5 warnings -> ~/prodflow_out
```

The synthetic sample production has nine planted defects. ProdFlow finds all of them, plus the downstream effects a real reviewer would hit. See the full report: [`examples/qc_report_defective.md`](examples/qc_report_defective.md).

Optional install: `pip install .` adds a `prodflow` command.

---

## 1. Production QC — `prodflow qc`

Validates a Concordance **DAT** against its **OPT** or **LFP** image load file, and both against the files actually on disk.

```bash
prodflow qc /path/to/PROD001 --out ./qc_PROD001 --operator "R. Hanks"
prodflow qc /path/to/PROD001 --dat DATA/PROD001.dat --lfp DATA/PROD001.lfp --field beg=ProdBegBates
```

| Check | Code | Severity |
|---|---|---|
| Rows with the wrong number of fields | `FIELD_COUNT` | error |
| Text qualifier never closed (corrupt export) | `UNTERMINATED_QUALIFIER` | error |
| Duplicate, overlapping, reversed or malformed Bates | `DUPLICATE_BATES` `BATES_OVERLAP` `BATES_REVERSED` `BATES_FORMAT` `BATES_PREFIX` | error |
| Gaps in the Bates sequence (withheld ranges needing a log) | `BATES_GAP` | warning |
| Inconsistent zero-padding within a prefix | `BATES_PADDING` | warning |
| Image count ≠ Bates span, DAT docs with no images, images with no DAT row | `PAGE_MISMATCH` `NO_IMAGE_DOC` `ORPHAN_IMAGE_DOC` | error |
| Family ranges that don't contain the document or don't resolve to real docs | `FAMILY_RANGE` `FAMILY_PARENT` `FAMILY_END` | error |
| Native / text / image paths that don't exist | `FILE_MISSING` | error |
| Paths that are absolute or escape the production root | `ABSOLUTE_PATH` `PATH_OUTSIDE_ROOT` | error |
| Paths whose case differs from disk (breaks on Linux platforms) | `CASE_MISMATCH` | warning |
| Native doesn't match the SHA-256 in the load file | `HASH_MISMATCH` | error |
| Unparseable date fields | `DATE_FORMAT` | error |
| Empty extracted-text files | `EMPTY_TEXT` | warning |
| Non-UTF-8 load file | `ENCODING` | warning |

Field names are recognized across common vendor conventions (`BegBates`, `BEGDOC`, `ProdBeg`, `BegAttach`, `NativeLink`…). Anything else can be mapped with `--field ROLE=NAME`.

**Exit codes:** `0` pass · `1` findings with errors · `2` could not run (bad input, integrity failure).

### Convert OPT ↔ LFP

```bash
prodflow convert DATA/PROD001.opt ../converted/PROD001.lfp
```

Page counts are recomputed from document breaks. The OPT → LFP → OPT round trip is covered by tests.

---

## 2. TAR validation — `prodflow elusion`

After a technology-assisted review, the question opposing counsel asks is *how much did you miss?* ProdFlow answers it with an elusion test on a random sample of the null set:

```bash
prodflow elusion --null-set 250000 --sample 1500 --found 6 --responsive 42000 \
                 --matter "Synthetic v. Example" --out memo/elusion_memo.md
```

> Estimated recall is **97.67%** (95% CI: 95.08% – 99.13%).
> An estimated **1,000** responsive documents remain in the null set (95% CI: 367 – 2,171).

- Intervals are **exact Clopper-Pearson** binomial intervals, the conservative choice for contested methodology. The implementation is checked against `scipy.stats.beta` reference values in the test suite.
- The memo states the method and its limitations in plain language, ready for a meet-and-confer or a declaration exhibit. See [`examples/elusion_memo.md`](examples/elusion_memo.md).

Plan the sample before you draw it:

```bash
prodflow sample-size --confidence 0.95 --margin 0.02 --population 250000    # -> 2379
```

---

## 3. Matter estimate — `prodflow estimate`

```bash
prodflow estimate --gb 120 --months 6 --out estimate.md
```

| Measure | Low | High |
|---|---:|---:|
| Review set after culling | 117,600 | 288,000 |
| Review hours (incl. QC) | 2,352 | 7,680 |
| **Total** | **$147,744** | **$630,312** |

Every assumption (docs/GB, de-dupe rate, culling rate, review pace, rates, QC overhead, hosting) is printed with the result and can be overridden from a JSON file. The defaults are illustrative planning figures, **not vendor quotes**. Full output: [`examples/estimate_120gb.md`](examples/estimate_120gb.md).

---

## Defensibility by design

These are guarantees the code enforces, not conventions:

| Guarantee | How |
|---|---|
| **Source is read-only** | Inputs are opened read-only. ProdFlow refuses to write output anywhere inside the production folder. |
| **Integrity verified before and after** | Every input load file is SHA-256 hashed before processing and re-hashed after. Any change aborts the run with exit code 2. |
| **Tamper-evident audit log** | Each run appends to `audit.jsonl`: UTC timestamp, tool version, operator, host, command, input hashes (before/after) and output hashes. Every entry includes the hash of the previous entry, so an edited, deleted or reordered entry breaks the chain. |
| **Reproducible output** | Reports are sorted and carry no timestamps. The same inputs always produce a byte-identical report. CI verifies this. |
| **No silent failures** | Malformed rows, undecodable files and unresolvable paths are reported as findings or stop the run. Nothing is skipped quietly. |

```bash
prodflow verify-log ./qc_PROD001/audit.jsonl
# Audit chain intact.
```

---

## Repository layout

```
prodflow/          CLI and library (stdlib only)
  qc.py            production QC checks and report rendering
  loadfile.py      DAT / OPT / LFP parsing and conversion
  tar.py           Clopper-Pearson, elusion, recall, sample size, memo
  estimate.py      volume and cost model
  integrity.py     SHA-256, read-only guards, hash-chained audit log
samples/           generator for clean and defective synthetic productions
examples/          reports generated from the samples
tests/             unittest suite (runs on Linux and Windows in CI)
```

Rebuild the samples at any time with `python samples/generate.py`. Output is byte-identical on every run.

## Testing

```bash
python -m unittest discover -s tests -v
```

The suite covers: every planted defect is detected with the expected counts, a clean production passes with zero findings, the source tree hashes identically after a run, hostile paths (`..\..\`, `C:\`) are rejected, audit-log tampering is detected, and the statistics match reference values.

## Scope and limitations

- Image files are checked for existence, not decoded. Blank or corrupt TIFFs aren't detected yet.
- Only SHA-256 hash fields are verified. MD5/SHA-1 fields are carried through but not checked.
- LFP image-type codes (`2` TIFF, `4` JPEG, `7` PDF) follow common usage. Confirm them against your review platform's import spec.
- The recall estimate treats the review's responsive count as exact and assumes a simple random sample. Both are stated in the generated memo.

## Data notice

All sample data is synthetic and generated by `samples/generate.py`. No case, client or law-enforcement data is included or was used to build this project.

## Author

**Ryan C. Hanks** — digital forensics and eDiscovery. Related work: [EDRM Litigation Forensics Showcase](https://github.com/ryandus/edrm-litigation-forensics-showcase) · [TraceFlow](https://github.com/ryandus/TraceFlow) · [SyncFlow](https://github.com/ryandus/SyncFlow) · [DFIR Investigation Framework](https://github.com/ryandus/DFIR-Investigation-Framework)

MIT License.
