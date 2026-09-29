"""The browser engine (docs/engine.js) must produce exactly what the Python CLI produces.

Runs the JS engine under Node; skipped when Node is not installed.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "samples"))

import generate  # noqa: E402
from prodflow import estimate, tar  # noqa: E402
from prodflow.integrity import sha256_file, verify_audit  # noqa: E402
from prodflow.loadfile import lfp_to_opt, opt_to_lfp, parse_lfp, parse_opt, read_text  # noqa: E402
from prodflow.qc import run_qc  # noqa: E402

NODE = shutil.which("node")
D, Q = "\x14", "\xfe"


def js(requests):
    out = subprocess.run([NODE, str(ROOT / "tests" / "web_engine_cli.mjs"), json.dumps(requests)],
                         capture_output=True, text=True, encoding="utf-8", check=True)
    return json.loads(out.stdout)


def py_report(root, dat, image, overrides=None):
    r = run_qc(root, root / dat if dat else None, root / image if image else None, overrides)
    from prodflow.qc import render_markdown
    return json.dumps(r, indent=2, sort_keys=True, ensure_ascii=False) + "\n", render_markdown(r)


def dat_bytes(rows, encoding="utf-8", bom=b""):
    return bom + ("\r\n".join(D.join(f"{Q}{v}{Q}" for v in r) for r in rows) + "\r\n").encode(encoding)


def build_chaos(root):
    """A production exercising every check and edge case the parser handles."""
    for sub in ("DATA", "IMAGES", "NATIVES", "TEXT"):
        (root / sub).mkdir(parents=True)
    for i in range(1, 30):
        (root / f"IMAGES/X{i:04d}.tif").write_bytes(b"II*\x00")
    (root / "NATIVES/good.bin").write_bytes(b"native")
    (root / "TEXT/empty.txt").write_bytes(b"")
    header = ["Prod Beg", "Prod End", "BegAttach", "EndAttach", "Sent Date", "Created_Date", "NativeLink", "TextLink",
              "SHA256", "Pages", "Prod Beg"]
    good_sha = sha256_file(root / "NATIVES/good.bin")
    rows = [header,
            ["X0001", "X0002", "X0001", "X0004", "2/29/2020", "2021-02-03T04:05:06", "NATIVES\\good.bin", "", good_sha, "2", ""],
            ["X0003", "X0004", "X0001", "X0004", "2/29/2021", "03/04/2021 12:30 PM", "C:\\evidence\\a.bin", "TEXT\\empty.txt", "", "2", ""],
            ["X0004", "X0006", "", "", "20211231", "03/04/2021 25:00:00", "..\\..\\etc\\passwd", "", "", "3", ""],
            ["X0010", "X010", "BAD", "X0010", "13/01/2021", "3/4/2021 1:02:03", "NATIVES\\good.bin", "", "abc", "1", ""],
            ["Y0005", "X0007", "", "", "", "12/31/2021 11:59 am", "", "", "", "", ""],
            ["X0020", "X0019", "", "", "1/1/2021", "", "", "", "", "", ""],
            ["X0001", "X0001", "", "", "01/01/0000", "", "\\\\server\\share\\x", "", "", "", ""],
            ["NOBATES", "NOBATES", "", "", "", "", "natives\\GOOD.BIN", "", good_sha.upper(), "", ""]]
    (root / "DATA/chaos.dat").write_bytes(dat_bytes(rows, "utf-16"))  # UTF-16 with BOM
    opt = ["X0001,V,IMAGES\\X0001.tif,Y,,,2", "X0002,V,IMAGES\\x0002.TIF,,,,", "X0003,V,IMAGES\\X0003.tif,Y,,,5",
           "X0004,V,IMAGES\\X0004.tif,Y,,,", "X0004,V,IMAGES\\X0004.tif,,,,", "X0099,V,IMAGES\\X0099.tif,Y,,,1",
           "short,V", "", "X0020,V,IMAGES/X0020.tif,y,,,1"]
    (root / "DATA/chaos.opt").write_bytes(("\r\n".join(opt) + "\n").encode("utf-8"))
    # A cp1252 DAT with an unterminated qualifier, and an LFP image file
    cp = ("þBegBatesþ\x14þEndBatesþ\x14þCustodianþ\r\nþZ01þ\x14þZ02þ\x14þMuñozþ\r\nþZ03þ\x14þZ03þ\x14þOpen").encode("cp1252")
    (root / "DATA/latin.dat").write_bytes(cp)
    lfp = ["IM,Z01,D,0,@V;IMAGES;X0001.tif;2", "IM,Z02, ,0,@V;IMAGES;X0002.tif;2", "FT,ignored",
           "IM,Z03,D,0,@V;\\IMAGES\\;X0003.tif;2", "IM,broken", "IM,Z09,D,0,@V;IMAGES"]
    (root / "DATA/latin.lfp").write_bytes("\r\n".join(lfp).encode("utf-8"))
    # Small files for the remaining structural checks
    (root / "DATA/empty.dat").write_bytes(b"\r\n")
    (root / "DATA/nobreak.opt").write_bytes(b"X0001,V,IMAGES\\X0001.tif,,,,\r\nX0002,V,IMAGES\\X0002.tif,Y,,,1\r\n")
    (root / "DATA/nobeg.dat").write_bytes(dat_bytes([["Custodian", "DocID"], ["Lee", "1"]]))
    (root / "DATA/fam.dat").write_bytes(dat_bytes([["BegBates", "EndBates", "BegAttach", "EndAttach"],
                                                   ["F0002", "F0003", "F0001", "F0003"]]))


@unittest.skipUnless(NODE or os.environ.get("CI"), "Node.js not installed")  # never skipped in CI
class WebParity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.base = Path(cls.tmp.name)
        generate.build(cls.base / "py/clean_production", False)
        generate.build(cls.base / "py/defective_production", True)
        build_chaos(cls.base / "chaos")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_samples_are_byte_identical(self):
        js([{"op": "samples", "out": str(self.base / "js")}])
        tree = lambda r: {p.relative_to(r).as_posix(): sha256_file(p) for p in sorted(r.rglob("*")) if p.is_file()}
        self.assertEqual(tree(self.base / "js"), tree(self.base / "py"))

    def test_qc_reports_are_identical(self):
        cases = [
            (self.base / "py/clean_production", "DATA/PROD001.dat", "DATA/PROD001.opt", {}),
            (self.base / "py/defective_production", "DATA/PROD001.dat", "DATA/PROD001.opt", {}),
            (self.base / "py/defective_production", None, "DATA/PROD001.opt", {}),
            (self.base / "chaos", "DATA/chaos.dat", "DATA/chaos.opt", {"beg": "Prod Beg", "end": "Prod End", "pagecount": "Pages"}),
            (self.base / "chaos", "DATA/chaos.dat", "DATA/chaos.opt", {}),
            (self.base / "chaos", "DATA/latin.dat", "DATA/latin.lfp", {}),
            (self.base / "chaos", "DATA/empty.dat", "DATA/nobreak.opt", {}),
            (self.base / "chaos", "DATA/nobeg.dat", None, {}),
            (self.base / "chaos", "DATA/fam.dat", None, {}),
        ]
        results = js([{"op": "qc", "root": str(r), "dat": d, "image": i, "overrides": o} for r, d, i, o in cases])
        for (root, dat, image, overrides), got in zip(cases, results):
            with self.subTest(root=root.name, dat=dat, overrides=overrides):
                want_json, want_md = py_report(root, dat, image, overrides)
                self.assertEqual(got["json"], want_json)
                self.assertEqual(got["md"], want_md)
        # together the fixtures must exercise every finding type the QC engine can emit
        seen = set().union(*(json.loads(r["json"])["summary"]["by_code"] for r in results))
        emitted = set(re.findall(r'"(?:error|warning)", "([A-Z_]+)"', (ROOT / "prodflow/qc.py").read_text()))
        emitted |= {"CASE_MISMATCH", "FILE_MISSING", "ABSOLUTE_PATH", "PATH_OUTSIDE_ROOT"}  # raised via _resolve
        self.assertEqual(emitted - seen, set())

    def test_statistics_agree(self):
        grid = [(250000, 1500, 6, 42000, 0.95), (1000, 385, 0, 100, 0.95), (50000, 2000, 37, 9000, 0.99),
                (10, 10, 10, 5, 0.9), (800000, 5000, 1, 120000, 0.95)]
        got = js([{"op": "elusion", "null_set": a, "sample": b, "found": c, "responsive": d, "confidence": e,
                   "matter": "Parity"} for a, b, c, d, e in grid])
        for args, g in zip(grid, got):
            want = tar.elusion(*args)
            for key in ("elusion_rate", "est_missed", "recall", "margin_achieved"):
                self.assertAlmostEqual(g["result"][key], want[key], delta=2e-6 * max(1, abs(want[key])))
            self.assertEqual(g["memo"], tar.memo(want, "Parity"))
        sizes = [(c, m, p) for c in (0.8, 0.9, 0.95, 0.975, 0.99, 0.999) for m in (0.005, 0.01, 0.02, 0.025, 0.05)
                 for p in (None, 1000, 250000)]
        got = js([{"op": "sample_size", "confidence": c, "margin": m, "population": p} for c, m, p in sizes])
        self.assertEqual([g["n"] for g in got], [tar.sample_size(c, m, p) for c, m, p in sizes])

    def test_estimates_agree(self):
        cases = [(120.0, 6), (0.5, 0), (2500.0, 18), (37.3, 3)]
        got = js([{"op": "estimate", "gb": g, "months": m} for g, m in cases])
        for (g, m), out in zip(cases, got):
            self.assertEqual(out["md"], estimate.render_markdown(estimate.estimate(g, m)))

    def test_web_audit_log_verifies_with_cli(self):
        log = self.base / "web_audit.jsonl"
        js([{"op": "audit", "log": str(log)}])
        self.assertEqual(verify_audit(log), [])

    def test_conversion_agrees(self):
        opt = self.base / "py/clean_production/DATA/PROD001.opt"
        lfp = self.base / "conv.lfp"
        lfp.write_bytes(opt_to_lfp(parse_opt(read_text(opt)[0])).encode("utf-8"))
        got = js([{"op": "convert", "input": str(opt)}, {"op": "convert", "input": str(lfp)}])
        self.assertEqual(got[0]["out"], lfp.read_bytes().decode("utf-8"))
        self.assertEqual(got[1]["out"], lfp_to_opt(parse_lfp(read_text(lfp)[0])[0]))


if __name__ == "__main__":
    unittest.main()
