"""Regenerate the synthetic demo QC output folders in gui/demo/. Deterministic: fixed audit timestamp, host and operator.

Runs the real QC engine on the synthetic productions from samples/generate.py. No real case data.
"""
import copy
import hashlib
import json
import shutil
import sys
import tempfile
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "samples"))
from generate import build  # noqa: E402

from prodflow import __version__  # noqa: E402
from prodflow.integrity import GENESIS, _canonical  # noqa: E402
from prodflow.qc import render_markdown, run_qc  # noqa: E402

OUT = HERE / "demo"


def dump(report):  # same serialization as cmd_qc in prodflow/cli.py
    return (json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()


def write_run(name, production, report):
    out = OUT / name
    out.mkdir(parents=True)
    files = {"qc_report.json": dump(report), "qc_report.md": render_markdown(report).encode()}
    for fn, data in files.items():
        (out / fn).write_bytes(data)
    # Same fields and canonical form as prodflow.integrity.append_audit, with fixed synthetic values
    # in place of the clock, hostname, OS user and absolute paths.
    entry = {
        "timestamp_utc": "2026-01-15T14:00:00+00:00", "tool": "prodflow", "tool_version": __version__,
        "python": "3.12.0", "host": "demo-host", "operator": "Demo Operator",
        "command": f"prodflow qc samples/{production} --out gui/demo/{name}",
        "inputs": [{"path": f"samples/{production}/{lf['path']}", "sha256_before": lf["sha256"],
                    "sha256_after": lf["sha256"]} for lf in sorted(report["load_files"], key=lambda lf: lf["path"])],
        "outputs": [{"path": f"gui/demo/{name}/{fn}", "sha256": hashlib.sha256(data).hexdigest()}
                    for fn, data in sorted(files.items())],
        "prev_entry_sha256": GENESIS,
    }
    entry["entry_sha256"] = hashlib.sha256(_canonical(entry).encode("utf-8")).hexdigest()
    (out / "audit.jsonl").write_bytes((_canonical(entry) + "\n").encode("utf-8"))


if OUT.exists():
    shutil.rmtree(OUT)
with tempfile.TemporaryDirectory() as tmp:
    for name, defective in (("clean", False), ("defective", True)):
        root = Path(tmp) / f"{name}_production"
        build(root, defective)
        report = run_qc(root, root / "DATA/PROD001.dat", root / "DATA/PROD001.opt")
        write_run(name, root.name, report)

# Tampered copy: every error removed and the summary rewritten to a self-consistent PASS after the run
# was audited. Only the audit record still holds the original hash. The viewer must reject it.
write_run("tampered", "defective_production", report)
doctored = copy.deepcopy(report)
doctored["findings"] = [x for x in report["findings"] if x["severity"] != "error"]
doctored["summary"].update(errors=0, result="PASS",
                           by_code=dict(sorted(Counter(x["code"] for x in doctored["findings"]).items())))
(OUT / "tampered" / "qc_report.json").write_bytes(dump(doctored))
print("Built gui/demo/clean, gui/demo/defective and gui/demo/tampered")
