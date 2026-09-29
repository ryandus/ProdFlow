import * as E from "./engine.js";
import { buildSample, DEFECTS } from "./samples.js";

const $ = (id) => document.getElementById(id);
function el(tag, props = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") n.className = v;
    else if (k === "text") n.textContent = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v);
  }
  for (const kid of kids) if (kid != null) n.append(kid);
  return n;
}
const fmt = (n) => Number(n).toLocaleString("en-US");

const CODE_INFO = {
  FIELD_COUNT: "A DAT row has more or fewer fields than the header, so its values are misaligned.",
  UNTERMINATED_QUALIFIER: "A text qualifier (þ) was never closed; everything after it is unreliable.",
  EMPTY_LOADFILE: "The DAT file has no header row.",
  DUPLICATE_FIELD: "The same field name appears twice in the DAT header.",
  MISSING_FIELD: "No Beg or End Bates field could be identified; map it under Field mapping.",
  DUPLICATE_BATES: "Two documents share the same beginning Bates number.",
  BATES_OVERLAP: "A document's Bates range overlaps the previous document's range.",
  BATES_REVERSED: "End Bates is lower than Beg Bates.",
  BATES_FORMAT: "A Bates value has no trailing number and cannot be sequenced.",
  BATES_PREFIX: "Beg and End Bates use different prefixes.",
  BATES_GAP: "Pages are missing from the sequence. Legitimate if withheld and logged; otherwise a production error.",
  BATES_PADDING: "The same prefix uses different zero-padding widths, which breaks sorting in review platforms.",
  PAGE_MISMATCH: "The number of images for a document does not match its Bates span.",
  NO_IMAGE_DOC: "A document in the DAT has no matching document break in the image load file.",
  ORPHAN_IMAGE_DOC: "The image load file has a document that is not in the DAT.",
  NO_FIRST_DOCBREAK: "The first image is not marked as the start of a document.",
  DUPLICATE_IMAGE_KEY: "The same image key appears more than once.",
  OPT_PAGECOUNT: "The page count declared in the OPT differs from the number of image lines.",
  OPT_FORMAT: "An OPT line has too few columns.",
  LFP_FORMAT: "An LFP image line is missing its @Volume;Directory;File;Type section.",
  FAMILY_RANGE: "A document falls outside its own attachment family range.",
  FAMILY_PARENT: "BegAttach does not point to a produced document.",
  FAMILY_END: "EndAttach does not point to the end of a produced document.",
  FAMILY_FORMAT: "A family range value cannot be parsed.",
  FILE_MISSING: "A native, text or image path in the load file does not exist in the production.",
  ABSOLUTE_PATH: "A path points to a drive or server (C:\\ or \\\\server) instead of inside the production.",
  PATH_OUTSIDE_ROOT: "A path uses ..\\ to escape the production folder.",
  CASE_MISMATCH: "Path capitalization differs from the actual file. Works on Windows, breaks on Linux-based platforms.",
  HASH_MISMATCH: "A native's SHA-256 does not match the hash recorded in the load file: altered, corrupted or swapped.",
  HASH_FORMAT: "The SHA-256 field is not a valid 64-character hash.",
  DATE_FORMAT: "A date field holds a value that is not a valid date.",
  EMPTY_TEXT: "An extracted text file is empty, so the document will not be searchable.",
  PAGECOUNT_FIELD: "The DAT's PageCount field disagrees with the Bates span.",
  ENCODING: "The load file is not UTF-8; accented names may import incorrectly.",
};

// ------------------------------------------------------------------ tabs
const TABS = ["qc", "tar", "est", "about"];
function selectTab(name, focus = false) {
  if (!TABS.includes(name)) name = "qc";
  for (const t of TABS) {
    const on = t === name;
    $(`tab-${t}`).setAttribute("aria-selected", String(on));
    $(`tab-${t}`).tabIndex = on ? 0 : -1;
    $(`panel-${t}`).hidden = !on;
  }
  if (focus) $(`tab-${name}`).focus();
  history.replaceState(null, "", `#${name}`);
}
TABS.forEach((t, i) => {
  const b = $(`tab-${t}`);
  b.addEventListener("click", () => selectTab(t));
  b.addEventListener("keydown", (e) => {
    const d = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    if (d) selectTab(TABS[(i + d + TABS.length) % TABS.length], true);
  });
});
selectTab(location.hash.slice(1));
window.addEventListener("hashchange", () => selectTab(location.hash.slice(1)));

// ------------------------------------------------------------------ downloads
function download(name, text, type = "text/plain") {
  const url = URL.createObjectURL(new Blob([text], { type: `${type};charset=utf-8` }));
  const a = el("a", { href: url, download: name });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// ------------------------------------------------------------------ QC
const qc = { report: null, json: "", md: "", audit: "", filter: { sev: "", code: "", q: "" } };
const status = (msg, isError = false) => { $("qc-status").textContent = msg; $("qc-status").classList.toggle("error", isError); };

for (const [title, detail] of DEFECTS) $("defect-list").append(el("li", {}, el("strong", { text: title }), ` — ${detail}`));

async function readAll(files, rel) { return rel ? await files.get(rel)() : null; }

async function runOn(files, label, datRel, imgRel, overrides, operator) {
  for (const b of document.querySelectorAll("#panel-qc .btn")) b.disabled = true;
  status(`Checking ${label}…`);
  try {
    const rels = [datRel, imgRel].filter(Boolean);
    const before = {};
    for (const r of rels) before[r] = await E.sha256Hex(await readAll(files, r));
    const report = await E.runQc(files, datRel, imgRel, overrides);
    const inputs = [];
    for (const r of rels) {
      const after = await E.sha256Hex(await readAll(files, r));
      if (after !== before[r]) throw new Error(`Source file changed during processing: ${r}`);
      inputs.push({ path: r, sha256_before: before[r], sha256_after: after });
    }
    qc.report = report;
    qc.json = E.prettyJson(report);
    qc.md = E.renderQcMarkdown(report);
    const kind = imgRel && imgRel.toLowerCase().endsWith(".lfp") ? "lfp" : "opt";
    const entry = await E.auditEntry({
      command: `prodflow-web qc "${label}"${datRel ? ` --dat ${datRel}` : ""}${imgRel ? ` --${kind} ${imgRel}` : ""}`,
      inputs, operator, runtime: navigator.userAgent,
      outputs: [{ path: "qc_report.json", sha256: await E.sha256Hex(qc.json) }, { path: "qc_report.md", sha256: await E.sha256Hex(qc.md) }],
    });
    qc.audit = E.canonical(entry) + "\n";
    qc.filter = { sev: "", code: "", q: "" };
    $("f-sev").value = ""; $("f-search").value = "";
    renderReport(label);
    status("");
    $("qc-results").hidden = false;
    $("verdict").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (err) {
    status(err.message || String(err), true);
  } finally {
    for (const b of document.querySelectorAll("#panel-qc .btn")) b.disabled = false;
  }
}

function renderReport(label) {
  const r = qc.report, s = r.summary;
  const v = $("verdict");
  v.className = `verdict ${s.result === "PASS" ? "pass" : "fail"}`;
  v.replaceChildren(el("span", { class: "badge", text: s.result }),
    el("p", { text: `${label}: ${fmt(s.errors)} error${s.errors === 1 ? "" : "s"} and ${fmt(s.warnings)} warning${s.warnings === 1 ? "" : "s"} across ${fmt(s.dat_records)} DAT records and ${fmt(s.image_records)} image records.` }));

  const tiles = [["DAT records", s.dat_records], ["Documents with valid Bates", s.documents_validated], ["Image records", s.image_records],
    ["Pages by Bates span", s.bates_pages], ["Errors", s.errors, "err"], ["Warnings", s.warnings, "warn"]];
  $("tiles").replaceChildren(...tiles.map(([k, val, cls]) => el("div", { class: `tile ${val && cls ? cls : ""}` },
    el("div", { class: "v", text: fmt(val) }), el("div", { class: "k", text: k }))),
    ...s.bates_ranges.map((b) => el("div", { class: "tile range" }, el("div", { class: "v mono", text: `${b.first} – ${b.last}` }),
      el("div", { class: "k", text: `Bates range${b.prefix ? ` (${b.prefix})` : ""}` }))));

  const sevOf = {};
  for (const f of r.findings) sevOf[f.code] ??= f.severity;
  $("chips").replaceChildren(...Object.entries(s.by_code).map(([code, n]) => el("button", {
    class: `chip ${sevOf[code]}`, "aria-pressed": "false", title: CODE_INFO[code] || code,
    onclick: () => { qc.filter.code = qc.filter.code === code ? "" : code; renderFindings(); },
  }, `${code} · ${n}`)));
  $("no-findings").hidden = r.findings.length > 0;
  $("findings").hidden = r.findings.length === 0;

  $("hashes").tBodies[0].replaceChildren(...r.load_files.map((lf) => el("tr", {},
    el("td", { class: "mono nowrap", text: lf.path }), el("td", { text: lf.encoding }), el("td", { class: "hash", text: lf.sha256 }))));
  renderFindings();
}

const MAX_ROWS = 2000;
function renderFindings() {
  const { sev, code, q } = qc.filter;
  const needle = q.trim().toLowerCase();
  const rows = qc.report.findings.filter((f) => (!sev || f.severity === sev) && (!code || f.code === code) &&
    (!needle || `${f.code} ${f.bates} ${f.source} ${f.message}`.toLowerCase().includes(needle)));
  for (const c of $("chips").children) c.setAttribute("aria-pressed", String(c.textContent.startsWith(`${code} ·`) && !!code));
  $("code-help").textContent = code ? `${code}: ${CODE_INFO[code] || ""}` : "Select a finding type to filter and see what it means.";
  const body = rows.slice(0, MAX_ROWS).map((f) => el("tr", {},
    el("td", {}, el("span", { class: `sev ${f.severity}`, text: f.severity })),
    el("td", { class: "mono", title: CODE_INFO[f.code] || "", text: f.code }),
    el("td", { class: "mono nowrap", text: f.bates }),
    el("td", { class: "mono nowrap", text: `${f.source}:${f.line}` }),
    el("td", { text: f.message })));
  if (rows.length > MAX_ROWS) body.push(el("tr", {}, el("td", { colspan: "5", class: "muted", text: `Showing ${fmt(MAX_ROWS)} of ${fmt(rows.length)}. Download the report for all findings.` })));
  if (!rows.length && qc.report.findings.length) body.push(el("tr", {}, el("td", { colspan: "5", class: "muted", text: "No findings match the filter." })));
  $("findings").tBodies[0].replaceChildren(...body);
}
$("f-sev").addEventListener("change", (e) => { qc.filter.sev = e.target.value; renderFindings(); });
$("f-search").addEventListener("input", (e) => { qc.filter.q = e.target.value; renderFindings(); });
$("dl-md").addEventListener("click", () => download("qc_report.md", qc.md, "text/markdown"));
$("dl-json").addEventListener("click", () => download("qc_report.json", qc.json, "application/json"));
$("dl-audit").addEventListener("click", () => download("audit.jsonl", qc.audit, "application/x-ndjson"));

async function runSample(defective) {
  const bytes = await buildSample(defective);
  const files = new Map([...bytes].map(([p, b]) => [p, async () => b]));
  await runOn(files, defective ? "Defective sample" : "Clean sample", "DATA/PROD001.dat", "DATA/PROD001.opt", {}, "sample");
}
$("run-defective").addEventListener("click", () => runSample(true));
$("run-clean").addEventListener("click", () => runSample(false));

// ---- own production
const own = { files: null, name: "" };
function acceptFiles(entries, name) {
  own.files = new Map(entries.map(([p, file]) => [p, async () => new Uint8Array(await file.arrayBuffer())]));
  own.name = name;
  const dats = E.discover(own.files, ".dat");
  const imgs = [...E.discover(own.files, ".opt"), ...E.discover(own.files, ".lfp")];
  const fill = (sel, list) => {
    sel.replaceChildren(...list.map((p) => el("option", { value: p, text: p })), el("option", { value: "", text: "(none)" }));
    sel.value = list[0] || "";
  };
  fill($("sel-dat"), dats);
  fill($("sel-img"), imgs);
  $("own-summary").textContent = `${name}: ${fmt(own.files.size)} files, ${dats.length} DAT and ${imgs.length} image load file${imgs.length === 1 ? "" : "s"} found.`;
  $("own-setup").hidden = false;
  if (!dats.length && !imgs.length) status("No .dat, .opt or .lfp files found in that folder. Choose the production's top-level folder.", true);
  else status("");
}
$("folder").addEventListener("change", (e) => {
  const list = [...e.target.files];
  if (!list.length) return;
  const top = list[0].webkitRelativePath.split("/")[0];
  acceptFiles(list.map((f) => [f.webkitRelativePath.split("/").slice(1).join("/"), f]), top);
});
const drop = $("drop");
drop.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); $("folder").click(); } });
for (const ev of ["dragenter", "dragover"]) drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); });
for (const ev of ["dragleave", "drop"]) drop.addEventListener(ev, () => drop.classList.remove("over"));
drop.addEventListener("drop", async (e) => {
  e.preventDefault();
  const entry = [...e.dataTransfer.items].map((i) => i.webkitGetAsEntry && i.webkitGetAsEntry()).find(Boolean);
  if (!entry || !entry.isDirectory) { status("Drop a folder (the production's top-level folder), not individual files.", true); return; }
  status("Reading folder…");
  const out = [];
  const walk = async (dirEntry, prefix) => {
    const reader = dirEntry.createReader();
    for (;;) {
      const batch = await new Promise((res, rej) => reader.readEntries(res, rej));
      if (!batch.length) break;
      for (const ent of batch) {
        const rel = prefix ? `${prefix}/${ent.name}` : ent.name;
        if (ent.isDirectory) await walk(ent, rel);
        else out.push([rel, await new Promise((res, rej) => ent.file(res, rej))]);
      }
    }
  };
  try {
    await walk(entry, "");
    acceptFiles(out, entry.name);
  } catch (err) {
    status(`Could not read the folder: ${err.message || err}`, true);
  }
});
$("run-own").addEventListener("click", () => {
  if (!own.files) return;
  const dat = $("sel-dat").value || null, img = $("sel-img").value || null;
  if (!dat && !img) { status("Select a DAT or image load file.", true); return; }
  const overrides = {};
  for (const line of $("overrides").value.split("\n")) {
    const i = line.indexOf("=");
    if (i > 0) overrides[line.slice(0, i).trim()] = line.slice(i + 1).trim();
  }
  runOn(own.files, own.name, dat, img, overrides, $("operator").value.trim());
});

// ------------------------------------------------------------------ tiny markdown renderer (safe: text nodes only)
function inline(text) {
  const frag = document.createDocumentFragment();
  const re = /(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`)/g;
  let last = 0, m;
  while ((m = re.exec(text))) {
    frag.append(text.slice(last, m.index));
    const t = m[0];
    frag.append(t.startsWith("**") ? el("strong", { text: t.slice(2, -2) }) : t.startsWith("`") ? el("code", { text: t.slice(1, -1) }) : el("em", { text: t.slice(1, -1) }));
    last = m.index + t.length;
  }
  frag.append(text.slice(last));
  return frag;
}
function renderMd(md, target) {
  const lines = md.split("\n"), out = [];
  for (let i = 0; i < lines.length;) {
    const l = lines[i];
    if (!l.trim()) { i++; continue; }
    const h = /^(#{1,3}) (.*)$/.exec(l);
    if (h) { out.push(el(`h${h[1].length}`, {}, inline(h[2]))); i++; continue; }
    if (l.startsWith("|")) {
      const rows = [];
      while (i < lines.length && lines[i].startsWith("|")) rows.push(lines[i++]);
      const cells = (r) => r.slice(1, -1).split("|").map((c) => c.trim());
      const t = el("table", {}, el("thead", {}, el("tr", {}, ...cells(rows[0]).map((c) => el("th", {}, inline(c))))),
        el("tbody", {}, ...rows.slice(2).map((r) => el("tr", {}, ...cells(r).map((c) => el("td", {}, inline(c)))))));
      out.push(el("div", { class: "table-wrap" }, t));
      continue;
    }
    const listRe = /^(\d+\.|-) /;
    if (listRe.test(l)) {
      const ordered = /^\d/.test(l), list = el(ordered ? "ol" : "ul");
      while (i < lines.length && (listRe.test(lines[i]) || /^ {2,}\S/.test(lines[i]))) {
        if (listRe.test(lines[i])) list.append(el("li", {}, inline(lines[i].replace(listRe, ""))));
        else list.lastChild.append(" ", inline(lines[i].trim()));
        i++;
      }
      out.push(list);
      continue;
    }
    const para = [];
    while (i < lines.length && lines[i].trim() && !/^(#|\||\d+\. |- )/.test(lines[i])) para.push(lines[i++]);
    out.push(el("p", {}, inline(para.join(" "))));
  }
  target.replaceChildren(...out);
}

// ------------------------------------------------------------------ TAR
const pct = (x, d = 2) => `${(x * 100).toFixed(d)}%`;
let memoText = "";
function intVal(id) { const v = $(id).value.trim(); return v === "" ? NaN : Number(v); }
function computeTar() {
  try {
    const r = E.elusion(intVal("t-null"), intVal("t-sample"), intVal("t-found"), intVal("t-resp"), Number($("t-conf").value));
    $("tar-error").textContent = "";
    const conf = `${Math.round(r.inputs.confidence * 100)}%`;
    $("t-recall").textContent = pct(r.recall, 1);
    $("t-recall-ci").textContent = `${conf} confidence interval: ${pct(r.recall_ci[0])} – ${pct(r.recall_ci[1])}`;
    $("t-missed").textContent = `${fmt(Math.round(r.est_missed))} (${fmt(Math.round(r.est_missed_ci[0]))} – ${fmt(Math.round(r.est_missed_ci[1]))})`;
    $("t-elusion").textContent = `${pct(r.elusion_rate)} (${pct(r.elusion_ci[0])} – ${pct(r.elusion_ci[1])})`;
    $("t-margin").textContent = `±${pct(r.margin_achieved)}`;
    drawBar(r);
    memoText = E.elusionMemo(r, $("t-matter").value.trim() || "[Matter]");
    renderMd(memoText, $("memo"));
  } catch (err) {
    $("tar-error").textContent = err.message.replace(/_/g, " ");
  }
}
function drawBar(r) {
  const svg = $("t-bar"), NS = "http://www.w3.org/2000/svg";
  const lo = Math.min(Math.floor(r.recall_ci[0] * 20) / 20, 0.8), X0 = 20, X1 = 380;
  const x = (v) => X0 + ((v - lo) / (1 - lo)) * (X1 - X0);
  const mk = (tag, attrs, text) => { const n = document.createElementNS(NS, tag); for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v); if (text) n.textContent = text; return n; };
  const kids = [mk("line", { x1: X0, x2: X1, y1: 30, y2: 30, stroke: "#22304f", "stroke-width": 6, "stroke-linecap": "round" }),
    mk("rect", { x: x(r.recall_ci[0]), y: 24, width: Math.max(2, x(r.recall_ci[1]) - x(r.recall_ci[0])), height: 12, rx: 6, fill: "#22d3ee", opacity: 0.35 }),
    mk("circle", { cx: x(r.recall), cy: 30, r: 6, fill: "#22d3ee" })];
  const steps = Math.round((1 - lo) / 0.05);
  for (let i = 0; i <= steps; i++) {
    const v = lo + i * 0.05;
    if (steps > 8 && i % 2) continue;
    kids.push(mk("text", { x: x(v), y: 58, "text-anchor": "middle" }, `${Math.round(v * 100)}%`));
  }
  svg.replaceChildren(...kids);
}
for (const id of ["t-null", "t-sample", "t-found", "t-resp", "t-conf", "t-matter"]) $(id).addEventListener("input", computeTar);
$("tar-form").addEventListener("submit", (e) => e.preventDefault());
$("dl-memo").addEventListener("click", () => download("elusion_memo.md", memoText, "text/markdown"));

function computeSs() {
  try {
    const pop = $("s-pop").value.trim() ? intVal("s-pop") : null;
    $("s-n").textContent = fmt(E.sampleSize(Number($("s-conf").value), Number($("s-margin").value) / 100, pop));
  } catch { $("s-n").textContent = "—"; }
}
for (const id of ["s-conf", "s-margin", "s-pop"]) $(id).addEventListener("input", computeSs);
$("ss-form").addEventListener("submit", (e) => e.preventDefault());

// ------------------------------------------------------------------ Estimate
let assumptions = structuredClone(E.ESTIMATE_DEFAULTS), estMd = "";
function renderAssumptions() {
  $("assumptions").tBodies[0].replaceChildren(...Object.entries(assumptions).map(([k, v]) => el("tr", {}, el("td", { text: k }),
    ...["low", "high"].map((s) => el("td", {}, el("input", { type: "number", step: "any", value: String(v[s]), "aria-label": `${k} ${s}`,
      oninput: (e) => { assumptions[k][s] = Number(e.target.value); computeEst(); } }))))));
}
function computeEst() {
  try {
    if (Object.values(assumptions).some((v) => !Number.isFinite(v.low) || !Number.isFinite(v.high))) throw new RangeError("Every assumption needs a number");
    const r = E.estimate(Number($("e-gb").value), intVal("e-months"), assumptions);
    $("est-error").textContent = "";
    const lo = r.scenarios.low, hi = r.scenarios.high;
    const rows = [["Documents collected", lo.docs_collected, hi.docs_collected], ["After de-duplication", lo.docs_after_dedupe, hi.docs_after_dedupe],
      ["Review set after culling", lo.docs_to_review, hi.docs_to_review], ["Review hours (incl. QC)", lo.review_hours, hi.review_hours],
      ["Processing cost", `$${fmt(lo.cost.processing)}`, `$${fmt(hi.cost.processing)}`], ["Hosting cost", `$${fmt(lo.cost.hosting)}`, `$${fmt(hi.cost.hosting)}`],
      ["Review cost", `$${fmt(lo.cost.review)}`, `$${fmt(hi.cost.review)}`]];
    $("est-table").tBodies[0].replaceChildren(
      ...rows.map(([k, a, b]) => el("tr", {}, el("td", { text: k }), el("td", { class: "num", text: typeof a === "number" ? fmt(a) : a }), el("td", { class: "num", text: typeof b === "number" ? fmt(b) : b }))),
      el("tr", { class: "total" }, el("td", { text: "Total" }), el("td", { class: "num", text: `$${fmt(lo.cost.total)}` }), el("td", { class: "num", text: `$${fmt(hi.cost.total)}` })));
    estMd = E.renderEstimateMarkdown(r);
  } catch (err) {
    $("est-error").textContent = err.message;
  }
}
for (const id of ["e-gb", "e-months"]) $(id).addEventListener("input", computeEst);
$("est-form").addEventListener("submit", (e) => e.preventDefault());
$("e-reset").addEventListener("click", () => { assumptions = structuredClone(E.ESTIMATE_DEFAULTS); renderAssumptions(); computeEst(); });
$("dl-est").addEventListener("click", () => download("estimate.md", estMd, "text/markdown"));

// ------------------------------------------------------------------ About
$("code-dictionary").replaceChildren(...Object.entries(CODE_INFO).map(([c, d]) => el("div", {}, el("code", { text: c }), el("span", { text: d }))));

computeTar();
computeSs();
renderAssumptions();
computeEst();
