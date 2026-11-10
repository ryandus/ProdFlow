"""ProdFlow Report Viewer: read-only Streamlit viewer for a `prodflow qc` output folder."""
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath

import streamlit as st

from prodflow import __version__
from prodflow.integrity import _canonical, _last_entry_hash, sha256_file, verify_audit

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
AUDIT = HERE / "audit" / "audit.jsonl"
DEMOS = {
    "Demo: clean production": HERE / "demo" / "clean",
    "Demo: defective production": HERE / "demo" / "defective",
    "Demo: tampered report": HERE / "demo" / "tampered",
}
HEX64 = re.compile(r"[0-9a-f]{64}")


def verify(report):
    """Raise unless the report has the expected shape and its summary agrees with its own findings."""
    if report["tool"] != "prodflow" or not isinstance(report["tool_version"], str):
        raise ValueError("not a prodflow QC report")
    s, findings = report["summary"], report["findings"]
    for x in findings:
        if (x["severity"] not in ("error", "warning") or type(x["line"]) is not int
                or not all(isinstance(x[k], str) for k in ("code", "message", "source", "bates"))):
            raise ValueError(f"malformed finding: {json.dumps(x, ensure_ascii=False)}")
    for lf in report["load_files"]:
        if not isinstance(lf["path"], str) or not isinstance(lf["encoding"], str) or not HEX64.fullmatch(lf["sha256"]):
            raise ValueError(f"malformed load file entry: {json.dumps(lf, ensure_ascii=False)}")
    for r in s["bates_ranges"]:
        if not all(isinstance(r[k], str) for k in ("prefix", "first", "last")):
            raise ValueError(f"malformed Bates range: {json.dumps(r, ensure_ascii=False)}")
    for k in ("documents_validated", "image_records", "bates_pages"):
        if type(s[k]) is not int:
            raise ValueError(f"summary field {k} is not an integer")
    errors = sum(x["severity"] == "error" for x in findings)
    derived = {"errors": errors, "warnings": len(findings) - errors,
               "by_code": dict(Counter(x["code"] for x in findings)),
               "result": "FAIL" if errors else "PASS",
               "dat_records": sum(lf.get("records", 0) for lf in report["load_files"])}
    diff = sorted(k for k in derived if s[k] != derived[k])
    if diff:
        raise ValueError(f"summary does not match the report body (fields: {', '.join(diff)})")
    return derived


def cross_check(path, data, report, report_sha):
    """Raise unless the CLI's audit chain is intact and its latest record of qc_report.json matches this report."""
    problems = verify_audit(path)
    if problems:
        raise ValueError("audit.jsonl chain is broken: " + "; ".join(problems))
    rows, recorded = [], None
    for n, e in enumerate(map(json.loads, data.decode("utf-8").splitlines()), 1):
        for role, items, key in (("input", e["inputs"], "sha256_after"), ("output", e["outputs"], "sha256")):
            for item in items:
                # CLI entries hold absolute paths, the hostname and the OS user: show bare file names only.
                name = PureWindowsPath(item["path"]).name
                rows.append({"Entry": n, "Time (UTC)": str(e["timestamp_utc"]), "Tool version": str(e["tool_version"]),
                             "Role": role, "File": name, "SHA-256": str(item[key])})
                if role == "output" and name == "qc_report.json":
                    recorded = (item["sha256"], sorted(i["sha256_after"] for i in e["inputs"]))
    if not recorded:
        raise ValueError("audit.jsonl has no entry recording qc_report.json")
    if recorded[0] != report_sha:
        raise ValueError(f"qc_report.json SHA-256 MISMATCH: file is {report_sha}, audit.jsonl recorded {recorded[0]}")
    if recorded[1] != sorted(lf["sha256"] for lf in report["load_files"]):
        raise ValueError("load file hashes in the report differ from the inputs recorded in audit.jsonl")
    return rows


def shown_path(path):
    """Repo-relative path, or the bare name for a path outside the repo. Never an absolute path."""
    p = Path(path).resolve()
    return p.relative_to(ROOT).as_posix() if p.is_relative_to(ROOT) else p.name


def audit(operator, folder, before, after, expected, outcome):
    rec = {"tool": "prodflow-gui", "tool_version": __version__, "streamlit": st.__version__, "operator": operator,
           "action": "load_report", "input": shown_path(folder), "mode": "rb (read-only)",
           "inputs": [{"path": n, "sha256_before": before[n], "sha256_after": after.get(n)} for n in sorted(before)],
           "expected_sha256": expected or None, "outcome": outcome}
    # Streamlit reruns the script on every interaction; log each distinct load once per session.
    if st.session_state.get("last_audit") == rec:
        return
    st.session_state["last_audit"] = rec
    AUDIT.parent.mkdir(exist_ok=True)
    # Same chain and canonical form as prodflow.integrity.append_audit, so `prodflow verify-log` checks this log.
    # append_audit itself is not used: it records the hostname, OS user and absolute paths.
    entry = {"timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), **rec,
             "prev_entry_sha256": _last_entry_hash(AUDIT)}
    entry["entry_sha256"] = hashlib.sha256(_canonical(entry).encode("utf-8")).hexdigest()
    with open(AUDIT, "a", encoding="utf-8", newline="\n") as f:
        f.write(_canonical(entry) + "\n")


def show_audit():
    problems = verify_audit(AUDIT)
    st.text("Hash chain: " + ("intact" if not problems else "BROKEN: " + "; ".join(problems)))
    with open(AUDIT, encoding="utf-8") as f:
        st.dataframe([json.loads(line) for line in f], hide_index=True,
                     column_order=["timestamp_utc", "operator", "action", "input", "outcome", "inputs",
                                   "expected_sha256", "tool_version", "streamlit", "prev_entry_sha256", "entry_sha256"])
    st.caption(f"{shown_path(AUDIT)} · Operator is self-reported · Hash-chained (check with `prodflow verify-log`) · "
               "Append-only by this app, not tamper-proof")


def hash_lines(before, after):
    return "".join(f"{name:<15} SHA-256 before  {before[name]}\n{'':<15} SHA-256 after   {after.get(name)}\n"
                   for name in sorted(before, reverse=True))


st.set_page_config(page_title="ProdFlow Report Viewer", layout="wide")
st.title("ProdFlow Report Viewer")
st.caption(f"Read-only viewer for prodflow qc output · v{__version__} · local only, no network calls")

operator = st.sidebar.text_input("Operator (self-reported)").strip()
choice = st.sidebar.selectbox("QC report", [*DEMOS, "Local path"])
local = st.sidebar.text_input("QC output folder").strip() if choice == "Local path" else ""
expected = st.sidebar.text_input("Expected SHA-256 of qc_report.json (independent record)").strip().lower()
if not operator or (choice == "Local path" and not local):
    st.info("Enter the operator name and choose a QC report. Every load is recorded in the audit log.")
    st.stop()
folder = DEMOS.get(choice) or Path(local)

before, after, error, trail = {}, {}, None, None
try:
    with open(folder / "qc_report.json", "rb") as f:
        data = f.read()
    before["qc_report.json"] = hashlib.sha256(data).hexdigest()
    if (folder / "audit.jsonl").exists():
        with open(folder / "audit.jsonl", "rb") as f:
            audit_data = f.read()
        before["audit.jsonl"] = hashlib.sha256(audit_data).hexdigest()
    try:
        report = json.loads(data)
        derived = verify(report)
        if "audit.jsonl" in before:
            trail = cross_check(folder / "audit.jsonl", audit_data, report, before["qc_report.json"])
        if expected and expected != before["qc_report.json"]:
            raise ValueError(f"expected SHA-256 MISMATCH: qc_report.json is {before['qc_report.json']}, "
                             f"operator supplied {expected}")
    finally:
        for name in before:
            after[name] = sha256_file(folder / name)
    if after != before:
        raise ValueError("source file changed on disk while it was being read")
except OSError as e:
    error = f"{type(e).__name__}: {e.strerror}"  # strerror only: str(e) would include the absolute path
except (ValueError, KeyError, TypeError, AttributeError, IndexError) as e:
    error = f"{type(e).__name__}: {e}"
audit(operator, folder, before, after, expected,
      error or "internally consistent" + ("; audit record MATCH" if trail else "; no audit.jsonl")
      + ("; expected SHA-256 MATCH" if expected else ""))

if error:
    st.error("**Report rejected. Nothing is displayed.**")
    st.code(error, language=None, wrap_lines=True)  # error text can quote report content: literal only
    st.code(f"{'Source':<15} {shown_path(folder)}  (opened rb, read-only)\n" + hash_lines(before, after), language=None)
    with st.expander("Audit log", expanded=True):
        show_audit()
    st.stop()

st.success("Internally consistent: source unchanged during read, summary recomputed from the findings"
           + (", audit chain intact and its record of this report matches" if trail else "")
           + ". This does not prove authenticity.")
if not trail:
    st.warning("No audit.jsonl next to this report. It was not cross-checked against the CLI's audit record.")
st.code(f"{'Source':<15} {shown_path(folder)}  (opened rb, read-only)\n" + hash_lines(before, after)
        + f"{'Summary':<15} RECOMPUTED from findings and load files\n"
        + f"{'Audit record':<15} " + ("chain intact; qc_report.json and load file hashes MATCH\n" if trail else "not present\n")
        + f"{'Expected':<15} " + ("MATCH" if expected else "not supplied"), language=None)

# Report-derived strings are untrusted. Render them only as literal text (st.text, st.code, st.dataframe),
# never through st.markdown / st.write / st.caption / alert bodies, so embedded markdown cannot render or fetch.
findings, s = report["findings"], report["summary"]
st.text(f'Generated by prodflow {report["tool_version"]}')
# Counted from the validated findings and load file list, not read from the report's own summary block.
for col, (label, value) in zip(st.columns(5), [
    ("Result", derived["result"]),
    ("Errors", derived["errors"]),
    ("Warnings", derived["warnings"]),
    ("Finding types", len(derived["by_code"])),
    ("Load files", len(report["load_files"])),
]):
    col.metric(label, value)
left, right = st.columns([3, 2])
left.dataframe([{"Load file": lf["path"], "Encoding": lf["encoding"], "Records": lf.get("records"),
                 "SHA-256": lf["sha256"]} for lf in report["load_files"]], hide_index=True)
right.dataframe([{"As reported by the file": k, "Value": str(v)} for k, v in [
    ("DAT records", s["dat_records"]), ("Documents with valid Bates", s["documents_validated"]),
    ("Image records", s["image_records"]), ("Pages by Bates span", s["bates_pages"]),
    *((f'Range {r["prefix"]}', f'{r["first"]} – {r["last"]}') for r in s["bates_ranges"])]], hide_index=True)

st.sidebar.divider()
severities = st.sidebar.multiselect("Severity", ["error", "warning"])
codes = st.sidebar.multiselect("Code", sorted(derived["by_code"]))
sources = st.sidebar.multiselect("Source", sorted({x["source"] for x in findings}))
query = st.sidebar.text_input("Search").lower()

shown = [
    x for x in findings
    if (not severities or x["severity"] in severities)
    and (not codes or x["code"] in codes)
    and (not sources or x["source"] in sources)
    and query in json.dumps(x, ensure_ascii=False).lower()
]

findings_tab, trail_tab, audit_tab = st.tabs(
    [f"Findings ({len(shown)} of {len(findings)})", "Source audit trail", "Viewer audit log"])
with findings_tab:
    if not findings:
        st.info("No findings.")
    elif not shown:
        st.info("No findings match the current filters.")
    else:
        counts = Counter((x["code"], x["severity"]) for x in shown)
        st.bar_chart([{"Code": c, "Severity": sev, "Findings": n} for (c, sev), n in sorted(counts.items())],
                     x="Code", y="Findings", color="Severity", horizontal=True)
        left, right = st.columns([3, 2])
        picked = left.dataframe(
            [{"Severity": x["severity"], "Code": x["code"], "Bates / Key": x["bates"], "Source": x["source"],
              "Line": x["line"], "Detail": x["message"]} for x in shown],
            hide_index=True, on_select="rerun", selection_mode="single-row",
        ).selection.rows
        with right:
            if not picked:
                st.info("Select a row to see the finding detail.")
            else:
                x = shown[picked[0]]
                st.subheader("Finding detail")
                st.text(f'{x["severity"]} · {x["code"]}')
                st.text(x["message"])
                st.code(json.dumps(x, indent=2, ensure_ascii=False), language="json", wrap_lines=True)
with trail_tab:
    if trail:
        st.dataframe(trail, hide_index=True)
        st.caption("From the audit.jsonl next to the report: hash chain intact. Operator, hostname, command line "
                   "and absolute paths recorded by the CLI are not displayed.")
    else:
        st.info("No audit.jsonl next to this report.")
with audit_tab:
    show_audit()
