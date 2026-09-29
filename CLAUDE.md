## Minimal Code
- Write the shortest solution that fully meets the stated requirement. YAGNI: no features, options, or config not asked for.
- Python stdlib first (the CLI has zero dependencies; keep it that way). Browser code uses web platform APIs only, no npm packages or CDN scripts.
- No new classes, abstraction layers, factories, or helper modules unless the same logic is needed in 3+ places.
- No speculative error handling for situations that can't realistically happen. Let unexpected errors raise.
- Edit existing code in place with minimal diffs. Don't refactor, rename, or reformat code outside the task.
- No boilerplate docstrings or comments that restate the code. Comment only non-obvious "why".
- Keep explanations short: what changed and why.

## Evidence Handling: never minimize
This is forensic tooling. If minimal code conflicts with defensibility in court, defensibility wins.
- SHA-256 hash verification before and after any read, copy, or transform of evidence. Don't add MD5 or SHA-1 unless asked (e.g., to match a legacy acquisition record).
- Never modify source evidence files; never write output inside the production folder.
- Chain-of-custody logging: timestamps, tool version, operator, input/output hashes (hash-chained audit.jsonl).
- Validate evidence input and fail explicitly. Never silently skip or swallow errors on evidence data.
- Output must be deterministic and reproducible (no timestamps in reports, stable sort order).
- No real case data in this repo. Samples and tests use synthetic data only.

## Two engines, one specification
- QC, TAR and estimate logic exists twice: prodflow/ (Python CLI) and docs/engine.js (browser, GitHub Pages).
- Any behavior change must be made in both, then pass `python -m unittest discover -s tests` including tests/test_web_parity.py (byte-identical reports, needs Node.js).
- docs/index.html keeps a strict Content-Security-Policy with connect-src 'none'. Do not add external scripts, fonts, or network calls.
