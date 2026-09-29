import argparse
import json
import sys
from pathlib import Path

from . import __version__, estimate, tar
from .integrity import (IntegrityError, append_audit, command_line, confirm_unchanged, ensure_outside,
                        snapshot, verify_audit)
from .loadfile import LoadFileError, lfp_to_opt, opt_to_lfp, parse_lfp, parse_opt, read_text
from .qc import discover, render_markdown, run_qc


def _write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:  # newline="": identical bytes on every OS
        f.write(text)
    return path


def cmd_qc(a):
    root = Path(a.production).resolve()
    if not root.is_dir():
        raise SystemExit(f"Production folder not found: {root}")
    out = Path(a.out).resolve()
    ensure_outside(out, root)
    # --dat/--opt/--lfp are relative to the production root (absolute paths also accepted)
    dat = root / a.dat if a.dat else discover(root, ".dat")
    img = root / (a.opt or a.lfp) if (a.opt or a.lfp) else (discover(root, ".opt") or discover(root, ".lfp"))
    if not dat and not img:
        raise SystemExit("No .dat, .opt or .lfp found; specify --dat / --opt / --lfp")
    overrides = dict(kv.split("=", 1) for kv in a.field)
    before = snapshot([p for p in (dat, img) if p])
    report = run_qc(root, dat, img, overrides)
    after = confirm_unchanged(before)
    outputs = [_write(out / "qc_report.json", json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n"),
               _write(out / "qc_report.md", render_markdown(report))]
    append_audit(out / "audit.jsonl", command_line(), before, after, outputs, a.operator)
    s = report["summary"]
    print(f"{s['result']}: {s['dat_records']} records, {s['errors']} errors, {s['warnings']} warnings -> {out}")
    return 1 if s["result"] == "FAIL" else 0


def cmd_convert(a):
    src = Path(a.input).resolve()
    dst = Path(a.output).resolve()
    ensure_outside(dst.parent, src.parent)
    before = snapshot([src])
    text, _, _ = read_text(src)
    if src.suffix.lower() == ".opt":
        result = opt_to_lfp(parse_opt(text))
    elif src.suffix.lower() == ".lfp":
        result = lfp_to_opt(parse_lfp(text)[0])
    else:
        raise SystemExit("Input must be .opt or .lfp")
    after = confirm_unchanged(before)
    _write(dst, result)
    append_audit(Path(a.audit_log or dst.parent / "audit.jsonl"), command_line(), before, after, [dst], a.operator)
    print(f"Wrote {dst}")
    return 0


def cmd_elusion(a):
    r = tar.elusion(a.null_set, a.sample, a.found, a.responsive, a.confidence)
    if a.out:
        out = _write(a.out, tar.memo(r, a.matter))
        append_audit(Path(a.audit_log or out.parent / "audit.jsonl"), command_line(), {}, {}, [out], a.operator)
    print(json.dumps(r, indent=2, sort_keys=True))
    return 0


def cmd_sample_size(a):
    print(tar.sample_size(a.confidence, a.margin, a.population))
    return 0


def cmd_estimate(a):
    r = estimate.estimate(a.gb, a.months, estimate.load_assumptions(a.assumptions))
    md = estimate.render_markdown(r)
    if a.out:
        out = _write(a.out, md)
        inputs = snapshot([a.assumptions]) if a.assumptions else {}
        append_audit(Path(a.audit_log or out.parent / "audit.jsonl"), command_line(), inputs, inputs, [out], a.operator)
    print(md)
    return 0


def cmd_verify_log(a):
    problems = verify_audit(a.log)
    print("\n".join(problems) if problems else "Audit chain intact.")
    return 1 if problems else 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="prodflow", description="eDiscovery production QC, TAR validation and matter estimates.")
    p.add_argument("--version", action="version", version=f"prodflow {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    def audited(sp):
        sp.add_argument("--operator", help="Name recorded in the audit log (default: OS user)")
        sp.add_argument("--audit-log", help="Audit log path (default: audit.jsonl next to the output)")

    q = sub.add_parser("qc", help="Validate a production's load files against each other and the files on disk")
    q.add_argument("production", help="Production root folder (read-only)")
    q.add_argument("--dat"), q.add_argument("--opt"), q.add_argument("--lfp")
    q.add_argument("--out", default="prodflow_out", help="Report folder (must be outside the production)")
    q.add_argument("--field", action="append", default=[], metavar="ROLE=NAME",
                   help="Map a DAT field, e.g. beg=ProdBeg. Roles: beg end begattach endattach native text sha256 pagecount")
    q.add_argument("--operator")
    q.set_defaults(fn=cmd_qc)

    c = sub.add_parser("convert", help="Convert OPT <-> LFP image load files")
    c.add_argument("input"), c.add_argument("output")
    audited(c)
    c.set_defaults(fn=cmd_convert)

    e = sub.add_parser("elusion", help="Elusion test and recall estimate with exact confidence intervals")
    e.add_argument("--null-set", type=int, required=True, help="Documents not produced")
    e.add_argument("--sample", type=int, required=True, help="Random sample size drawn from the null set")
    e.add_argument("--found", type=int, required=True, help="Responsive documents found in the sample")
    e.add_argument("--responsive", type=int, required=True, help="Responsive documents identified by review")
    e.add_argument("--confidence", type=float, default=0.95)
    e.add_argument("--matter", default="[Matter]")
    e.add_argument("--out", help="Write a methodology memo (Markdown)")
    audited(e)
    e.set_defaults(fn=cmd_elusion)

    s = sub.add_parser("sample-size", help="Sample size for a target margin of error")
    s.add_argument("--confidence", type=float, default=0.95)
    s.add_argument("--margin", type=float, default=0.02)
    s.add_argument("--population", type=int)
    s.set_defaults(fn=cmd_sample_size)

    m = sub.add_parser("estimate", help="Volume and cost ranges for a matter")
    m.add_argument("--gb", type=float, required=True)
    m.add_argument("--months", type=int, default=6)
    m.add_argument("--assumptions", help="JSON file overriding default assumptions")
    m.add_argument("--out", help="Write the estimate (Markdown)")
    audited(m)
    m.set_defaults(fn=cmd_estimate)

    v = sub.add_parser("verify-log", help="Verify the hash chain of an audit log")
    v.add_argument("log")
    v.set_defaults(fn=cmd_verify_log)

    a = p.parse_args(argv)
    try:
        return a.fn(a)
    except (IntegrityError, LoadFileError, ValueError) as err:
        print(f"ERROR: {err}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
