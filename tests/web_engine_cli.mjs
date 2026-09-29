// Test driver: runs the browser engine under Node so test_web_parity.py can compare it with the Python CLI.
// Usage: node tests/web_engine_cli.mjs '<json list of requests>'  -> prints a JSON list of results.
import { readFileSync, readdirSync, statSync, writeFileSync, mkdirSync, appendFileSync } from "node:fs";
import { join, dirname } from "node:path";
import * as engine from "../docs/engine.js";
import { buildSample } from "../docs/samples.js";

function loadTree(root) {
  const files = new Map();
  const walk = (dir, rel) => {
    for (const name of readdirSync(dir)) {
      const full = join(dir, name), r = rel ? `${rel}/${name}` : name;
      if (statSync(full).isDirectory()) walk(full, r);
      else files.set(r, async () => new Uint8Array(readFileSync(full)));
    }
  };
  walk(root, "");
  return files;
}

async function handle(req) {
  switch (req.op) {
    case "qc": {
      const report = await engine.runQc(loadTree(req.root), req.dat, req.image, req.overrides || {});
      return { json: engine.prettyJson(report), md: engine.renderQcMarkdown(report) };
    }
    case "samples":
      for (const [defective, name] of [[false, "clean_production"], [true, "defective_production"]]) {
        for (const [p, bytes] of await buildSample(defective)) {
          const full = join(req.out, name, p);
          mkdirSync(dirname(full), { recursive: true });
          writeFileSync(full, bytes);
        }
      }
      return {};
    case "elusion": {
      const r = engine.elusion(req.null_set, req.sample, req.found, req.responsive, req.confidence);
      return { result: r, memo: engine.elusionMemo(r, req.matter) };
    }
    case "sample_size":
      return { n: engine.sampleSize(req.confidence, req.margin, req.population ?? null) };
    case "estimate":
      return { md: engine.renderEstimateMarkdown(engine.estimate(req.gb, req.months)) };
    case "audit": {
      let prev = engine.GENESIS;
      for (const op of ["Ryan Hanks", "Examiner Ñ"]) {
        const entry = await engine.auditEntry({ command: "prodflow-web qc", operator: op, runtime: "node",
          inputs: [{ path: "DATA/PROD001.dat", sha256_before: "a".repeat(64), sha256_after: "a".repeat(64) }],
          outputs: [{ path: "qc_report.json", sha256: "b".repeat(64) }], prev });
        appendFileSync(req.log, engine.canonical(entry) + "\n");
        prev = entry.entry_sha256;
      }
      return {};
    }
    case "convert": {
      const text = readFileSync(req.input, "utf8");
      return { out: req.input.toLowerCase().endsWith(".opt") ? engine.optToLfp(engine.parseOpt(text)) : engine.lfpToOpt(engine.parseLfp(text)[0]) };
    }
    default:
      throw new Error(`unknown op ${req.op}`);
  }
}

const results = [];
for (const req of JSON.parse(process.argv[2])) results.push(await handle(req));
process.stdout.write(JSON.stringify(results));
