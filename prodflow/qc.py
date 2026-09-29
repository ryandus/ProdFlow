"""Production QC: validates DAT + OPT/LFP load files against each other and against the files on disk."""
import re
from datetime import datetime
from functools import lru_cache
from pathlib import Path, PureWindowsPath

from . import __version__
from .integrity import sha256_file
from .loadfile import parse_dat, parse_lfp, parse_opt, read_text, split_bates

ROLE_ALIASES = {
    "beg": {"begbates", "begdoc", "begno", "beginbates", "prodbeg", "prodbegbates", "batesbegin", "startbates"},
    "end": {"endbates", "enddoc", "endno", "prodend", "prodendbates", "batesend"},
    "begattach": {"begattach", "begfamily", "familybeg", "begattachment"},
    "endattach": {"endattach", "endfamily", "familyend", "endattachment"},
    "native": {"nativelink", "nativepath", "nativefile", "native"},
    "text": {"textlink", "textpath", "extractedtext", "textfile", "ocrpath"},
    "sha256": {"sha256", "sha256hash", "hashsha256"},
    "pagecount": {"pagecount", "pages", "pgcount"},
}
DATE_FORMATS = ("%m/%d/%Y", "%Y-%m-%d", "%Y%m%d", "%m/%d/%Y %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%m/%d/%Y %I:%M %p")
SEVERITY_ORDER = {"error": 0, "warning": 1}


def _norm(name):
    return re.sub(r"[^a-z0-9]", "", name.lower())


class _Findings(list):
    def add(self, severity, code, message, source="", line=0, bates=""):
        self.append({"severity": severity, "code": code, "message": message,
                     "source": source, "line": line, "bates": bates})


def _resolve(root, raw):
    """Map a load-file path to a file under root. Returns (path_or_None, problem_code_or_None)."""
    win = PureWindowsPath(raw.strip())
    if win.drive or raw.strip().startswith("\\\\"):
        return None, "ABSOLUTE_PATH"
    parts = [p for p in win.parts if p not in ("\\", "/", ".")]
    if ".." in parts:
        return None, "PATH_OUTSIDE_ROOT"
    # Compare against real directory entries so case differences are caught the same way
    # on case-insensitive (Windows/macOS) and case-sensitive (Linux) filesystems.
    cur, mismatch = root, False
    for part in parts:
        actual = _listing(cur).get(part.lower())
        if actual is None:
            return None, "FILE_MISSING"
        mismatch |= actual != part
        cur = cur / actual
    if not cur.is_file():
        return None, "FILE_MISSING"
    return cur, "CASE_MISMATCH" if mismatch else None


@lru_cache(maxsize=None)
def _listing(directory):
    return {c.name.lower(): c.name for c in directory.iterdir()} if directory.is_dir() else {}


def _check_file(root, raw, kind, f, source, line, bates):
    path, problem = _resolve(root, raw)
    if problem == "CASE_MISMATCH":
        f.add("warning", "CASE_MISMATCH", f"{kind} path case differs from disk: {raw}", source, line, bates)
    elif problem:
        f.add("error", problem, f"{kind} not usable: {raw}", source, line, bates)
    return path


def _load(path, root, f):
    text, enc, warn = read_text(path)
    rel = path.relative_to(root).as_posix()
    if warn:
        f.add("warning", "ENCODING", warn, rel)
    return text, enc, rel


def _check_dat(root, dat_path, overrides, f):
    text, enc, rel = _load(dat_path, root, f)
    records, unterminated = parse_dat(text)
    if unterminated:
        f.add("error", "UNTERMINATED_QUALIFIER", "File ends inside a text qualifier; records after this point are unreliable", rel)
    if not records:
        f.add("error", "EMPTY_LOADFILE", "DAT contains no header", rel)
        return {"path": rel, "encoding": enc}, []
    (_, header), rows = records[0], records[1:]
    normed = [_norm(h) for h in header]
    for h in sorted({h for h in normed if normed.count(h) > 1}):
        f.add("error", "DUPLICATE_FIELD", f"Header field repeated: {h}", rel, 1)
    roles = {}
    for role, aliases in ROLE_ALIASES.items():
        wanted = {_norm(overrides[role])} if role in overrides else aliases
        idx = next((i for i, h in enumerate(normed) if h in wanted), None)
        if idx is not None:
            roles[role] = idx
    for role in ("beg", "end"):
        if role not in roles:
            f.add("error", "MISSING_FIELD", f"No {role} Bates field found (use --field {role}=NAME)", rel, 1)
    date_cols = [i for i, h in enumerate(normed) if "date" in h]

    docs = []
    for line, vals in rows:
        if len(vals) != len(header):
            f.add("error", "FIELD_COUNT", f"{len(vals)} fields, header has {len(header)}", rel, line)
            continue
        get = lambda role: vals[roles[role]].strip() if role in roles else ""
        beg, end = get("beg"), get("end")
        doc = {"line": line, "beg": beg, "end": end, "b": split_bates(beg) if beg else None,
               "e": split_bates(end) if end else None}
        if "beg" in roles and "end" in roles:
            if not doc["b"] or not doc["e"]:
                f.add("error", "BATES_FORMAT", f"Unparseable Bates range '{beg}'-'{end}'", rel, line, beg)
                doc["b"] = doc["e"] = None
            elif doc["b"][0] != doc["e"][0]:
                f.add("error", "BATES_PREFIX", f"Beg/End prefixes differ: {beg} / {end}", rel, line, beg)
                doc["b"] = doc["e"] = None
            elif doc["e"][1] < doc["b"][1]:
                f.add("error", "BATES_REVERSED", f"End Bates precedes Beg Bates: {beg} / {end}", rel, line, beg)
                doc["b"] = doc["e"] = None
        span = doc["e"][1] - doc["b"][1] + 1 if doc["b"] else None
        doc["span"] = span
        if span and get("pagecount") and get("pagecount") != str(span):
            f.add("warning", "PAGECOUNT_FIELD", f"PageCount {get('pagecount')} but Bates span is {span}", rel, line, beg)
        for i in date_cols:
            v = vals[i].strip()
            if v and not any(_parses(v, fmt) for fmt in DATE_FORMATS):
                f.add("error", "DATE_FORMAT", f"{header[i]} value '{v}' is not a recognized date", rel, line, beg)
        if get("native"):
            native = _check_file(root, get("native"), "Native", f, rel, line, beg)
            expected = get("sha256").lower()
            if native and expected:
                if not re.fullmatch(r"[0-9a-f]{64}", expected):
                    f.add("error", "HASH_FORMAT", f"SHA-256 field is not 64 hex characters: {expected}", rel, line, beg)
                elif sha256_file(native) != expected:
                    f.add("error", "HASH_MISMATCH", f"Native SHA-256 does not match load file: {get('native')}", rel, line, beg)
        if get("text"):
            txt = _check_file(root, get("text"), "Text", f, rel, line, beg)
            if txt and txt.stat().st_size == 0:
                f.add("warning", "EMPTY_TEXT", f"Extracted text file is empty: {get('text')}", rel, line, beg)
        doc["begattach"], doc["endattach"] = get("begattach"), get("endattach")
        docs.append(doc)

    _check_bates_sequence([d for d in docs if d["b"]], rel, f)
    _check_families([d for d in docs if d["b"]], rel, f)
    return {"path": rel, "encoding": enc, "records": len(rows)}, docs


def _parses(value, fmt):
    try:
        datetime.strptime(value, fmt)
        return True
    except ValueError:
        return False


def _check_bates_sequence(docs, rel, f):
    seen = {}
    for d in docs:
        if d["beg"] in seen:
            f.add("error", "DUPLICATE_BATES", f"Beg Bates also used on line {seen[d['beg']]}", rel, d["line"], d["beg"])
        seen.setdefault(d["beg"], d["line"])
    by_prefix = {}
    for d in docs:
        by_prefix.setdefault(d["b"][0], []).append(d)
    for prefix, group in by_prefix.items():
        widths = sorted({d["b"][2] for d in group} | {d["e"][2] for d in group})
        if len(widths) > 1:
            f.add("warning", "BATES_PADDING", f"Prefix '{prefix}' uses mixed number widths {widths}", rel)
        group.sort(key=lambda d: (d["b"][1], d["line"]))
        for prev, cur in zip(group, group[1:]):
            if cur["b"][1] <= prev["e"][1] and cur["beg"] != prev["beg"]:
                f.add("error", "BATES_OVERLAP", f"Overlaps {prev['beg']}-{prev['end']}", rel, cur["line"], cur["beg"])
            elif cur["b"][1] > prev["e"][1] + 1:
                w = prev["e"][2]
                f.add("warning", "BATES_GAP",
                      f"Gap {prefix}{prev['e'][1] + 1:0{w}d}-{prefix}{cur['b'][1] - 1:0{w}d} "
                      f"({cur['b'][1] - prev['e'][1] - 1} pages unaccounted for)", rel, cur["line"], cur["beg"])


def _check_families(docs, rel, f):
    begs = {d["beg"] for d in docs}
    ends = {d["end"] for d in docs}
    for d in docs:
        ba, ea = d["begattach"], d["endattach"]
        if not ba and not ea:
            continue
        b, e = split_bates(ba) if ba else None, split_bates(ea) if ea else None
        if not b or not e:
            f.add("error", "FAMILY_FORMAT", f"Unparseable family range '{ba}'-'{ea}'", rel, d["line"], d["beg"])
            continue
        if not (b[0] == d["b"][0] == e[0] and b[1] <= d["b"][1] and d["e"][1] <= e[1]):
            f.add("error", "FAMILY_RANGE", f"Document falls outside its family range {ba}-{ea}", rel, d["line"], d["beg"])
        if ba not in begs:
            f.add("error", "FAMILY_PARENT", f"BegAttach {ba} is not the Beg Bates of any produced document", rel, d["line"], d["beg"])
        if ea not in ends:
            f.add("error", "FAMILY_END", f"EndAttach {ea} is not the End Bates of any produced document", rel, d["line"], d["beg"])


def _check_images(root, img_path, kind, f):
    text, enc, rel = _load(img_path, root, f)
    if kind == "opt":
        recs = parse_opt(text)
        for r in recs:
            if r["ncols"] < 3:
                f.add("error", "OPT_FORMAT", f"Expected 7 columns, found {r['ncols']}", rel, r["line"])
    else:
        recs, other = parse_lfp(text)
        for r in recs:
            if not r["path"]:
                f.add("error", "LFP_FORMAT", "IM line missing @Volume;Dir;File;Type", rel, r["line"])
    recs = [r for r in recs if r["key"]]
    if recs and not recs[0]["docbreak"]:
        f.add("error", "NO_FIRST_DOCBREAK", "First image is not flagged as a document break", rel, recs[0]["line"], recs[0]["key"])
    seen, pages, current = {}, {}, None
    for r in recs:
        if r["key"] in seen:
            f.add("error", "DUPLICATE_IMAGE_KEY", f"Image key also on line {seen[r['key']]}", rel, r["line"], r["key"])
        seen.setdefault(r["key"], r["line"])
        if r["path"]:
            _check_file(root, r["path"], "Image", f, rel, r["line"], r["key"])
        if r["docbreak"]:
            current = r["key"]
            pages[current] = {"count": 0, "line": r["line"], "declared": r["pagecount"]}
        if current:
            pages[current]["count"] += 1
    for key, p in pages.items():
        if p["declared"] and p["declared"] != str(p["count"]):
            f.add("warning", "OPT_PAGECOUNT", f"Declares {p['declared']} pages, {p['count']} image lines follow", rel, p["line"], key)
    return {"path": rel, "encoding": enc}, pages, len(recs)


def _reconcile(docs, pages, img_rel, f, dat_rel):
    dat_begs = set()
    for d in docs:
        if not d["b"]:
            continue
        dat_begs.add(d["beg"])
        p = pages.get(d["beg"])
        if p is None:
            f.add("error", "NO_IMAGE_DOC", f"No document break in {img_rel} for this document", dat_rel, d["line"], d["beg"])
        elif p["count"] != d["span"]:
            f.add("error", "PAGE_MISMATCH", f"Bates span is {d['span']} pages, {img_rel} has {p['count']} images",
                  dat_rel, d["line"], d["beg"])
    for key in sorted(set(pages) - dat_begs):
        f.add("error", "ORPHAN_IMAGE_DOC", f"Image document not present in the DAT", img_rel, pages[key]["line"], key)


def discover(root, suffix):
    hits = sorted(p for p in Path(root).rglob("*") if p.is_file() and p.suffix.lower() == suffix)
    if len(hits) > 1:
        raise ValueError(f"Multiple {suffix} files found; specify one: {[str(h) for h in hits]}")
    return hits[0] if hits else None


def run_qc(root, dat=None, image_file=None, overrides=None):
    """Returns a deterministic report dict. Never writes to root."""
    root = Path(root).resolve()
    _listing.cache_clear()
    f = _Findings()
    load_files, docs, pages, n_images = [], [], None, 0
    if dat:
        info, docs = _check_dat(root, Path(dat).resolve(), overrides or {}, f)
        load_files.append(info)
    if image_file:
        kind = "lfp" if Path(image_file).suffix.lower() == ".lfp" else "opt"
        info, pages, n_images = _check_images(root, Path(image_file).resolve(), kind, f)
        load_files.append(info)
        if dat:
            _reconcile(docs, pages, info["path"], f, load_files[0]["path"])
    for lf in load_files:
        lf["sha256"] = sha256_file(root / lf["path"])

    findings = sorted(f, key=lambda x: (SEVERITY_ORDER[x["severity"]], x["code"], x["source"], x["line"], x["bates"], x["message"]))
    valid = [d for d in docs if d["b"]]
    ranges = {}
    for d in sorted(valid, key=lambda d: (d["b"][0], d["b"][1])):
        r = ranges.setdefault(d["b"][0], [d["beg"], d["end"]])
        r[1] = d["end"]
    codes = {}
    for x in findings:
        codes[x["code"]] = codes.get(x["code"], 0) + 1
    return {
        "tool": "prodflow", "tool_version": __version__,
        "load_files": load_files,
        "summary": {
            "dat_records": sum(lf.get("records", 0) for lf in load_files),
            "documents_validated": len(valid), "image_records": n_images, "bates_pages": sum(d["span"] for d in valid),
            "bates_ranges": [{"prefix": k, "first": v[0], "last": v[1]} for k, v in sorted(ranges.items())],
            "errors": sum(x["severity"] == "error" for x in findings),
            "warnings": sum(x["severity"] == "warning" for x in findings),
            "by_code": dict(sorted(codes.items())),
            "result": "FAIL" if any(x["severity"] == "error" for x in findings) else "PASS",
        },
        "findings": findings,
    }


def render_markdown(report):
    s = report["summary"]
    out = [f"# Production QC Report — {s['result']}", "",
           f"Generated by prodflow {report['tool_version']}. Deterministic: identical inputs produce an identical report.", "",
           "## Load files", "", "| File | Encoding | SHA-256 |", "|---|---|---|"]
    out += [f"| `{lf['path']}` | {lf['encoding']} | `{lf['sha256']}` |" for lf in report["load_files"]]
    out += ["", "## Summary", "", "| Measure | Value |", "|---|---|",
            f"| DAT records | {s['dat_records']} |", f"| Documents with valid Bates | {s['documents_validated']} |", f"| Image records | {s['image_records']} |",
            f"| Pages by Bates span | {s['bates_pages']} |", f"| Errors | {s['errors']} |", f"| Warnings | {s['warnings']} |"]
    out += [f"| Range `{r['prefix']}` | {r['first']} – {r['last']} |" for r in s["bates_ranges"]]
    if s["by_code"]:
        out += ["", "## Findings by type", "", "| Code | Count |", "|---|---|"]
        out += [f"| {c} | {n} |" for c, n in s["by_code"].items()]
        out += ["", "## Findings", "", "| Severity | Code | Bates / Key | Source:Line | Detail |", "|---|---|---|---|---|"]
        out += [f"| {x['severity']} | {x['code']} | {x['bates']} | {x['source']}:{x['line']} | {x['message'].replace('|', '/')} |"
                for x in report["findings"]]
    else:
        out += ["", "No findings."]
    return "\n".join(out) + "\n"
