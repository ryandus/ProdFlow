import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "samples"))

import generate  # noqa: E402
from prodflow import estimate, tar  # noqa: E402
from prodflow.cli import main  # noqa: E402
from prodflow.integrity import IntegrityError, append_audit, ensure_outside, sha256_file, verify_audit  # noqa: E402
from prodflow.loadfile import lfp_to_opt, opt_to_lfp, parse_dat, parse_lfp, parse_opt, split_bates  # noqa: E402
from prodflow.qc import run_qc  # noqa: E402

EXPECTED_DEFECTS = {
    "BATES_GAP": 2, "BATES_OVERLAP": 1, "CASE_MISMATCH": 1, "DATE_FORMAT": 1, "EMPTY_TEXT": 1,
    "FAMILY_END": 1, "FAMILY_RANGE": 1, "FIELD_COUNT": 1, "FILE_MISSING": 1, "HASH_MISMATCH": 1,
    "ORPHAN_IMAGE_DOC": 1, "PAGECOUNT_FIELD": 1, "PAGE_MISMATCH": 1,
}


def tree_hashes(root):
    return {p.relative_to(root).as_posix(): sha256_file(p) for p in sorted(root.rglob("*")) if p.is_file()}


class Samples(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.base = Path(cls.tmp.name)
        cls.clean, cls.bad = cls.base / "clean", cls.base / "bad"
        generate.build(cls.clean, defective=False)
        generate.build(cls.bad, defective=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def qc(self, root):
        return run_qc(root, root / "DATA/PROD001.dat", root / "DATA/PROD001.opt")

    def test_clean_production_passes(self):
        r = self.qc(self.clean)
        self.assertEqual(r["summary"]["result"], "PASS")
        self.assertEqual(r["findings"], [])
        self.assertEqual(r["summary"]["dat_records"], 12)
        self.assertEqual(r["summary"]["bates_pages"], sum(generate.PAGES))

    def test_every_planted_defect_is_found(self):
        self.assertEqual(self.qc(self.bad)["summary"]["by_code"], EXPECTED_DEFECTS)

    def test_report_is_deterministic(self):
        a = json.dumps(self.qc(self.bad), sort_keys=True)
        b = json.dumps(self.qc(self.bad), sort_keys=True)
        self.assertEqual(a, b)

    def test_source_is_never_modified(self):
        before = tree_hashes(self.bad)
        with tempfile.TemporaryDirectory() as out:
            self.assertEqual(main(["qc", str(self.bad), "--out", out, "--operator", "test"]), 1)
            self.assertEqual(verify_audit(Path(out) / "audit.jsonl"), [])
        self.assertEqual(tree_hashes(self.bad), before)

    def test_refuses_output_inside_production(self):
        self.assertEqual(main(["qc", str(self.clean), "--out", str(self.clean / "out")]), 2)
        self.assertFalse((self.clean / "out").exists())

    def test_hostile_paths_are_rejected(self):
        root = self.base / "hostile"
        generate.build(root, defective=False)
        opt = root / "DATA/PROD001.opt"
        lines = opt.read_text().splitlines()
        lines[0] = lines[0].replace("IMAGES\\001", "..\\..\\etc")
        lines[1] = lines[1].replace("IMAGES\\001", "C:\\Evidence")
        opt.write_text("\r\n".join(lines) + "\r\n")
        codes = self.qc(root)["summary"]["by_code"]
        self.assertEqual(codes.get("PATH_OUTSIDE_ROOT"), 1)
        self.assertEqual(codes.get("ABSOLUTE_PATH"), 1)


class LoadFiles(unittest.TestCase):
    def test_dat_qualifiers_protect_delimiters_and_newlines(self):
        text = "þAþ\x14þBþ\r\nþx\x14yþ\x14þline1\nline2þ\r\n"
        records, unterminated = parse_dat(text)
        self.assertFalse(unterminated)
        self.assertEqual(records, [(1, ["A", "B"]), (2, ["x\x14y", "line1\nline2"])])

    def test_unterminated_qualifier_is_flagged(self):
        self.assertTrue(parse_dat("þAþ\x14þB\r\n")[1])

    def test_split_bates(self):
        self.assertEqual(split_bates("ABC-000123"), ("ABC-", 123, 6))
        self.assertIsNone(split_bates("ABC"))

    def test_opt_lfp_round_trip(self):
        opt = "K1,V1,IMAGES\\001\\K1.tif,Y,,,2\r\nK2,V1,IMAGES\\001\\K2.tif,,,,\r\nK3,V1,IMAGES\\001\\K3.tif,Y,,,1\r\n"
        lfp = opt_to_lfp(parse_opt(opt))
        self.assertIn("IM,K1,D,0,@V1;IMAGES\\001;K1.tif;2", lfp)
        self.assertEqual(lfp_to_opt(parse_lfp(lfp)[0]), opt)


class Tar(unittest.TestCase):
    def test_clopper_pearson_matches_reference_values(self):
        # Reference values from scipy.stats.beta.ppf
        for (k, n), (lo, hi) in {(0, 100): (0.0, 0.0362167), (6, 1500): (0.0014693, 0.0086858),
                                 (1, 10): (0.0025286, 0.4450161), (50, 50): (0.9288783, 1.0)}.items():
            got = tar.clopper_pearson(k, n)
            self.assertAlmostEqual(got[0], lo, places=6)
            self.assertAlmostEqual(got[1], hi, places=6)

    def test_sample_size(self):
        self.assertEqual(tar.sample_size(0.95, 0.05), 385)
        self.assertLess(tar.sample_size(0.95, 0.05, population=1000), 385)

    def test_elusion_and_recall(self):
        r = tar.elusion(250000, 1500, 6, 42000)
        self.assertAlmostEqual(r["est_missed"], 1000.0)
        self.assertAlmostEqual(r["recall"], 42000 / 43000, places=6)
        self.assertLess(r["recall_ci"][0], r["recall"])
        self.assertGreater(r["recall_ci"][1], r["recall"])

    def test_invalid_inputs_raise(self):
        with self.assertRaises(ValueError):
            tar.elusion(100, 200, 1, 10)
        with self.assertRaises(ValueError):
            tar.clopper_pearson(5, 4)


class Integrity(unittest.TestCase):
    def test_audit_tampering_is_detected(self):
        with tempfile.TemporaryDirectory() as d:
            log, out = Path(d) / "audit.jsonl", Path(d) / "o.txt"
            out.write_text("x")
            for _ in range(3):
                append_audit(log, "cmd", {}, {}, [out], "tester")
            self.assertEqual(verify_audit(log), [])
            lines = log.read_text().splitlines()
            log.write_text("\n".join([lines[0], lines[1].replace("tester", "someone"), lines[2]]) + "\n")
            self.assertTrue(any("altered" in p for p in verify_audit(log)))
            log.write_text("\n".join([lines[0], lines[2]]) + "\n")
            self.assertTrue(any("chain broken" in p for p in verify_audit(log)))

    def test_ensure_outside(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(IntegrityError):
                ensure_outside(Path(d) / "sub", d)
            ensure_outside(Path(d).parent / "elsewhere", d)


class Estimate(unittest.TestCase):
    def test_arithmetic(self):
        low = estimate.estimate(100, 6)["scenarios"]["low"]
        self.assertEqual(low["docs_collected"], 400000)
        self.assertEqual(low["docs_after_dedupe"], 280000)
        self.assertEqual(low["cost"]["total"], sum(v for k, v in low["cost"].items() if k != "total"))

    def test_unknown_assumption_rejected(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"typo": {"low": 1, "high": 2}}, f)
        with self.assertRaises(ValueError):
            estimate.load_assumptions(f.name)
        Path(f.name).unlink()


if __name__ == "__main__":
    unittest.main()
