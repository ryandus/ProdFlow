// ProdFlow browser engine. A line-for-line port of the Python package: given the same
// inputs it produces byte-identical QC reports (verified in tests/test_web_parity.py).
// Pure functions only: no DOM, no network. Runs in browsers and Node 18+.

export const VERSION = "0.1.0";
export const GENESIS = "0".repeat(64);

// ---------------------------------------------------------------- utilities
const enc = new TextEncoder();
const hex = (buf) => [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
export async function sha256Hex(bytes) {
  return hex(await crypto.subtle.digest("SHA-256", typeof bytes === "string" ? enc.encode(bytes) : bytes));
}

const PY_WS = "\\t\\n\\v\\f\\r\\x1c-\\x1f \\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000";
const STRIP_RE = new RegExp(`^[${PY_WS}]+|[${PY_WS}]+$`, "g");
const strip = (s) => s.replace(STRIP_RE, "");
const rstripChars = (s, chars) => { let i = s.length; while (i && chars.includes(s[i - 1])) i--; return s.slice(0, i); };

// str.splitlines() semantics
const LINE_BREAK = /\r\n|[\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029]/;
function splitlines(text) {
  const out = text.split(LINE_BREAK);
  if (out.length && out[out.length - 1] === "") out.pop();
  return out;
}
// str.split(sep, maxsplit) semantics
function pySplit(s, sep, max) {
  const parts = s.split(sep);
  return parts.length <= max + 1 ? parts : [...parts.slice(0, max), parts.slice(max).join(sep)];
}
const cmp = (a, b) => (a < b ? -1 : a > b ? 1 : 0);
const cmpTuple = (a, b) => { for (let i = 0; i < a.length; i++) { const c = cmp(a[i], b[i]); if (c) return c; } return 0; };

function sortDeep(v) {
  if (Array.isArray(v)) return v.map(sortDeep);
  if (v && typeof v === "object") return Object.fromEntries(Object.keys(v).sort(cmp).map((k) => [k, sortDeep(v[k])]));
  return v;
}
export const canonical = (obj) => JSON.stringify(sortDeep(obj));        // json.dumps(sort_keys, (",", ":"))
export const prettyJson = (obj) => JSON.stringify(sortDeep(obj), null, 2) + "\n"; // json.dumps(indent=2, sort_keys)

const commas = (n) => String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
function roundHalfEven(x) {
  const r = Math.round(x);
  return Math.abs(x % 1) === 0.5 ? 2 * Math.round(x / 2) : r;
}

// ---------------------------------------------------------------- decoding
export class LoadFileError extends Error {}

const CP1252_UNDEFINED = new Set([0x81, 0x8d, 0x8f, 0x90, 0x9d]);

// Offset of the first invalid UTF-8 sequence, as CPython reports it (start of the sequence), or -1.
function firstInvalidUtf8(b) {
  let i = 0;
  while (i < b.length) {
    const c = b[i];
    if (c < 0x80) { i++; continue; }
    let need, lo = 0x80, hi = 0xbf;
    if (c >= 0xc2 && c <= 0xdf) need = 1;
    else if (c >= 0xe0 && c <= 0xef) { need = 2; if (c === 0xe0) lo = 0xa0; if (c === 0xed) hi = 0x9f; }
    else if (c >= 0xf0 && c <= 0xf4) { need = 3; if (c === 0xf0) lo = 0x90; if (c === 0xf4) hi = 0x8f; }
    else return i;
    for (let k = 1; k <= need; k++) {
      const x = b[i + k];
      if (x === undefined) return i;
      if (k === 1 ? x < lo || x > hi : x < 0x80 || x > 0xbf) return i;
    }
    i += need + 1;
  }
  return -1;
}

export function readText(bytes, label) {
  const b = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  const boms = [[[0xef, 0xbb, 0xbf], "utf-8-sig", "utf-8"], [[0xff, 0xfe], "utf-16", "utf-16le"], [[0xfe, 0xff], "utf-16", "utf-16be"]];
  for (const [bom, name, codec] of boms) {
    if (bom.every((x, i) => b[i] === x)) {
      if (codec === "utf-8" && firstInvalidUtf8(b.subarray(3)) >= 0) {
        throw new LoadFileError(`${label}: invalid ${name} data at byte offset ${firstInvalidUtf8(b.subarray(3)) + 3}`);
      }
      try {
        return [new TextDecoder(codec, { fatal: true }).decode(b.subarray(bom.length)), name, null];
      } catch {
        throw new LoadFileError(`${label}: invalid ${name} data`);
      }
    }
  }
  const bad = firstInvalidUtf8(b);
  if (bad < 0) return [new TextDecoder("utf-8").decode(b), "utf-8", null];
  const undef = b.findIndex((x) => CP1252_UNDEFINED.has(x));
  if (undef >= 0) throw new LoadFileError(`${label}: not decodable as UTF-8 or Windows-1252 (byte offset ${undef})`);
  const warn = `Not valid UTF-8 (byte 0x${b[bad].toString(16).toUpperCase().padStart(2, "0")} at offset ${bad}); decoded as Windows-1252`;
  return [new TextDecoder("windows-1252").decode(b), "cp1252", warn];
}

// ---------------------------------------------------------------- load files
export const DAT_DELIM = "\x14";
export const DAT_QUOTE = "\xfe";
const LFP_IMAGE_TYPES = { ".tif": "2", ".tiff": "2", ".jpg": "4", ".jpeg": "4", ".pdf": "7" };

export function parseDat(text, delim = DAT_DELIM, quote = DAT_QUOTE) {
  let records = [], fields = [], buf = [], inQ = false, line = 1, start = 1;
  for (const ch of text) {
    if (inQ) {
      if (ch === quote) inQ = false;
      else { buf.push(ch); if (ch === "\n") line++; }
    } else if (ch === quote) inQ = true;
    else if (ch === delim) { fields.push(buf.join("")); buf = []; }
    else if (ch === "\n") {
      fields.push(buf.join("")); records.push([start, fields]);
      fields = []; buf = []; line++; start = line;
    } else if (ch !== "\r") buf.push(ch);
  }
  if (buf.length || fields.length) { fields.push(buf.join("")); records.push([start, fields]); }
  records = records.filter((r) => !(r[1].length === 1 && r[1][0] === ""));
  return [records, inQ];
}

export function parseOpt(text) {
  const out = [];
  splitlines(text).forEach((line, idx) => {
    if (!strip(line)) return;
    const cols = line.split(",");
    const ncols = cols.length;
    while (cols.length < 7) cols.push("");
    out.push({ line: idx + 1, key: strip(cols[0]), volume: strip(cols[1]), path: strip(cols[2]),
      docbreak: strip(cols[3]).toUpperCase() === "Y", pagecount: strip(cols[6]), ncols });
  });
  return out;
}

export function joinWinPath(dir, name) { return dir ? rstripChars(dir, "\\/") + "\\" + name : name; }

export function splitWinPath(raw) {
  const s = strip(raw);
  const absolute = /^[A-Za-z]:/.test(s) || s.startsWith("\\\\") || s.startsWith("//");
  return [absolute, s.split(/[\\/]/).filter((p) => p !== "" && p !== ".")];
}

export function parseLfp(text) {
  const out = []; let other = 0;
  splitlines(text).forEach((line, idx) => {
    if (!strip(line)) return;
    const parts = pySplit(line, ",", 4);
    if (strip(parts[0]).toUpperCase() !== "IM") { other++; return; }
    const rec = { line: idx + 1, key: "", volume: "", path: "", docbreak: false, pagecount: "", ncols: parts.length };
    if (parts.length === 5 && parts[4].startsWith("@")) {
      const loc = parts[4].slice(1).split(";");
      Object.assign(rec, { key: strip(parts[1]), docbreak: strip(parts[2]).toUpperCase() === "D", volume: loc[0] });
      if (loc.length >= 3) rec.path = joinWinPath(loc[1], loc[2]);
    }
    out.push(rec);
  });
  return [out, other];
}

export function optToLfp(records) {
  const lines = records.map((r) => {
    const cut = Math.max(r.path.lastIndexOf("\\"), r.path.lastIndexOf("/"));
    const parent = r.path.slice(0, Math.max(cut, 0)), name = r.path.slice(cut + 1);
    const dot = name.lastIndexOf(".");
    const suffix = dot >= 0 ? "." + name.slice(dot + 1).toLowerCase() : "";
    return `IM,${r.key},${r.docbreak ? "D" : " "},0,@${r.volume};${parent};${name};${LFP_IMAGE_TYPES[suffix] || "2"}`;
  });
  return lines.join("\r\n") + "\r\n";
}

export function lfpToOpt(records) {
  const counts = new Map(); let current = null;
  for (const r of records) {
    if (r.docbreak) { current = r.key; counts.set(current, 0); }
    if (current !== null) counts.set(current, counts.get(current) + 1);
  }
  return records.map((r) => `${r.key},${r.volume},${r.path},${r.docbreak ? "Y" : ""},,,${r.docbreak ? counts.get(r.key) : ""}`)
    .join("\r\n") + "\r\n";
}

export function splitBates(value) {
  const m = /^(.*?)(\d+)$/.exec(strip(value));
  return m ? [m[1], Number(m[2]), m[2].length] : null;
}

// ---------------------------------------------------------------- dates (datetime.strptime semantics)
const DIRECTIVES = {
  d: "(3[0-1]|[1-2]\\d|0[1-9]|[1-9]| [1-9])", m: "(1[0-2]|0[1-9]|[1-9])", Y: "(\\d\\d\\d\\d)",
  H: "(2[0-3]|[0-1]\\d|\\d)", M: "([0-5]\\d|\\d)", S: "(6[0-1]|[0-5]\\d|\\d)", I: "(1[0-2]|0[1-9]|[1-9])", p: "(am|pm)",
};
const DATE_FORMATS = ["%m/%d/%Y", "%Y-%m-%d", "%Y%m%d", "%m/%d/%Y %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%m/%d/%Y %I:%M %p"];
const compiled = DATE_FORMATS.map((fmt) => {
  const order = [];
  let src = fmt.replace(/([\\.^$*+?(){}[\]|])/g, "\\$1").replace(/\s+/g, "\\s+");
  src = src.replace(/%([a-zA-Z])/g, (_, d) => { order.push(d); return DIRECTIVES[d]; });
  return { re: new RegExp("^" + src, "i"), order };
});
function parsesAsDate(value) {
  return compiled.some(({ re, order }) => {
    const m = re.exec(value);
    if (!m || m[0].length !== value.length) return false;
    const v = {};
    order.forEach((d, i) => { v[d] = m[i + 1]; });
    const year = Number(v.Y), month = Number(v.m), day = Number(v.d);
    let hour = v.H !== undefined ? Number(v.H) : 0;
    if (v.I !== undefined) {
      const i = Number(v.I);
      hour = v.p.toLowerCase() === "am" ? (i === 12 ? 0 : i) : (i === 12 ? 12 : i + 12);
    }
    const sec = v.S !== undefined ? Number(v.S) : 0;
    const dim = [31, (year % 4 === 0 && year % 100 !== 0) || year % 400 === 0 ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
    return year >= 1 && day <= dim[month - 1] && hour <= 23 && sec <= 59;
  });
}

// ---------------------------------------------------------------- QC
const ROLE_ALIASES = {
  beg: ["begbates", "begdoc", "begno", "beginbates", "prodbeg", "prodbegbates", "batesbegin", "startbates"],
  end: ["endbates", "enddoc", "endno", "prodend", "prodendbates", "batesend"],
  begattach: ["begattach", "begfamily", "familybeg", "begattachment"],
  endattach: ["endattach", "endfamily", "familyend", "endattachment"],
  native: ["nativelink", "nativepath", "nativefile", "native"],
  text: ["textlink", "textpath", "extractedtext", "textfile", "ocrpath"],
  sha256: ["sha256", "sha256hash", "hashsha256"],
  pagecount: ["pagecount", "pages", "pgcount"],
};
const SEVERITY_ORDER = { error: 0, warning: 1 };
const norm = (name) => name.toLowerCase().replace(/[^a-z0-9]/g, "");

/**
 * A production is a Map of posix-relative path -> async () => Uint8Array.
 * Directory listings are derived from the file paths.
 */
function buildListing(files) {
  const dirs = new Map();
  const add = (dir, name) => {
    if (!dirs.has(dir)) dirs.set(dir, new Map());
    const m = dirs.get(dir), k = name.toLowerCase();
    if (!m.has(k)) m.set(k, []);
    if (!m.get(k).includes(name)) m.get(k).push(name);
  };
  for (const p of files.keys()) {
    const parts = p.split("/");
    parts.forEach((name, i) => add(parts.slice(0, i).join("/"), name));
  }
  for (const m of dirs.values()) for (const list of m.values()) list.sort(cmp);
  return dirs;
}

function resolve(ctx, raw) {
  const [absolute, parts] = splitWinPath(raw);
  if (absolute) return [null, "ABSOLUTE_PATH"];
  if (parts.includes("..")) return [null, "PATH_OUTSIDE_ROOT"];
  let cur = "", mismatch = false;
  for (const part of parts) {
    const names = ctx.listing.get(cur)?.get(part.toLowerCase());
    if (!names || !names.length) return [null, "FILE_MISSING"];
    const actual = names.includes(part) ? part : names[0];
    mismatch ||= actual !== part;
    cur = cur ? `${cur}/${actual}` : actual;
  }
  if (!ctx.files.has(cur)) return [null, "FILE_MISSING"];
  return [cur, mismatch ? "CASE_MISMATCH" : null];
}

function checkFile(ctx, raw, kind, source, line, bates) {
  const [path, problem] = resolve(ctx, raw);
  if (problem === "CASE_MISMATCH") ctx.add("warning", "CASE_MISMATCH", `${kind} path case differs from disk: ${raw}`, source, line, bates);
  else if (problem) ctx.add("error", problem, `${kind} not usable: ${raw}`, source, line, bates);
  return path;
}

async function load(ctx, rel) {
  const [text, encoding, warn] = readText(await ctx.files.get(rel)(), rel);
  if (warn) ctx.add("warning", "ENCODING", warn, rel);
  return [text, encoding];
}

async function checkDat(ctx, rel, overrides) {
  const [text, encoding] = await load(ctx, rel);
  const [records, unterminated] = parseDat(text);
  if (unterminated) ctx.add("error", "UNTERMINATED_QUALIFIER", "File ends inside a text qualifier; records after this point are unreliable", rel);
  if (!records.length) {
    ctx.add("error", "EMPTY_LOADFILE", "DAT contains no header", rel);
    return [{ path: rel, encoding }, []];
  }
  const header = records[0][1], rows = records.slice(1);
  const normed = header.map(norm);
  const dupes = [...new Set(normed.filter((h, i) => normed.indexOf(h) !== i))].sort(cmp);
  for (const h of dupes) ctx.add("error", "DUPLICATE_FIELD", `Header field repeated: ${h}`, rel, 1);
  const roles = {};
  for (const [role, aliases] of Object.entries(ROLE_ALIASES)) {
    const wanted = role in overrides ? [norm(overrides[role])] : aliases;
    const idx = normed.findIndex((h) => wanted.includes(h));
    if (idx >= 0) roles[role] = idx;
  }
  for (const role of ["beg", "end"]) {
    if (!(role in roles)) ctx.add("error", "MISSING_FIELD", `No ${role} Bates field found (use --field ${role}=NAME)`, rel, 1);
  }
  const dateCols = normed.map((h, i) => (h.includes("date") ? i : -1)).filter((i) => i >= 0);

  const docs = [];
  for (const [line, vals] of rows) {
    if (vals.length !== header.length) {
      ctx.add("error", "FIELD_COUNT", `${vals.length} fields, header has ${header.length}`, rel, line);
      continue;
    }
    const get = (role) => (role in roles ? strip(vals[roles[role]]) : "");
    const beg = get("beg"), end = get("end");
    const doc = { line, beg, end, b: beg ? splitBates(beg) : null, e: end ? splitBates(end) : null };
    if ("beg" in roles && "end" in roles) {
      if (!doc.b || !doc.e) {
        ctx.add("error", "BATES_FORMAT", `Unparseable Bates range '${beg}'-'${end}'`, rel, line, beg);
        doc.b = doc.e = null;
      } else if (doc.b[0] !== doc.e[0]) {
        ctx.add("error", "BATES_PREFIX", `Beg/End prefixes differ: ${beg} / ${end}`, rel, line, beg);
        doc.b = doc.e = null;
      } else if (doc.e[1] < doc.b[1]) {
        ctx.add("error", "BATES_REVERSED", `End Bates precedes Beg Bates: ${beg} / ${end}`, rel, line, beg);
        doc.b = doc.e = null;
      }
    }
    const span = doc.b ? doc.e[1] - doc.b[1] + 1 : null;
    doc.span = span;
    if (span && get("pagecount") && get("pagecount") !== String(span)) {
      ctx.add("warning", "PAGECOUNT_FIELD", `PageCount ${get("pagecount")} but Bates span is ${span}`, rel, line, beg);
    }
    for (const i of dateCols) {
      const v = strip(vals[i]);
      if (v && !parsesAsDate(v)) ctx.add("error", "DATE_FORMAT", `${header[i]} value '${v}' is not a recognized date`, rel, line, beg);
    }
    if (get("native")) {
      const native = checkFile(ctx, get("native"), "Native", rel, line, beg);
      const expected = get("sha256").toLowerCase();
      if (native && expected) {
        if (!/^[0-9a-f]{64}$/.test(expected)) {
          ctx.add("error", "HASH_FORMAT", `SHA-256 field is not 64 hex characters: ${expected}`, rel, line, beg);
        } else if ((await sha256Hex(await ctx.files.get(native)())) !== expected) {
          ctx.add("error", "HASH_MISMATCH", `Native SHA-256 does not match load file: ${get("native")}`, rel, line, beg);
        }
      }
    }
    if (get("text")) {
      const txt = checkFile(ctx, get("text"), "Text", rel, line, beg);
      if (txt && (await ctx.files.get(txt)()).length === 0) {
        ctx.add("warning", "EMPTY_TEXT", `Extracted text file is empty: ${get("text")}`, rel, line, beg);
      }
    }
    doc.begattach = get("begattach"); doc.endattach = get("endattach");
    docs.push(doc);
  }
  checkBatesSequence(ctx, docs.filter((d) => d.b), rel);
  checkFamilies(ctx, docs.filter((d) => d.b), rel);
  return [{ path: rel, encoding, records: rows.length }, docs];
}

function checkBatesSequence(ctx, docs, rel) {
  const seen = new Map();
  for (const d of docs) {
    if (seen.has(d.beg)) ctx.add("error", "DUPLICATE_BATES", `Beg Bates also used on line ${seen.get(d.beg)}`, rel, d.line, d.beg);
    if (!seen.has(d.beg)) seen.set(d.beg, d.line);
  }
  const byPrefix = new Map();
  for (const d of docs) { if (!byPrefix.has(d.b[0])) byPrefix.set(d.b[0], []); byPrefix.get(d.b[0]).push(d); }
  for (const [prefix, group] of byPrefix) {
    const widths = [...new Set([...group.map((d) => d.b[2]), ...group.map((d) => d.e[2])])].sort((a, b) => a - b);
    if (widths.length > 1) ctx.add("warning", "BATES_PADDING", `Prefix '${prefix}' uses mixed number widths [${widths.join(", ")}]`, rel);
    group.sort((a, b) => a.b[1] - b.b[1] || a.line - b.line);
    for (let i = 1; i < group.length; i++) {
      const prev = group[i - 1], cur = group[i];
      if (cur.b[1] <= prev.e[1] && cur.beg !== prev.beg) {
        ctx.add("error", "BATES_OVERLAP", `Overlaps ${prev.beg}-${prev.end}`, rel, cur.line, cur.beg);
      } else if (cur.b[1] > prev.e[1] + 1) {
        const w = prev.e[2], pad = (n) => String(n).padStart(w, "0");
        ctx.add("warning", "BATES_GAP", `Gap ${prefix}${pad(prev.e[1] + 1)}-${prefix}${pad(cur.b[1] - 1)} ` +
          `(${cur.b[1] - prev.e[1] - 1} pages unaccounted for)`, rel, cur.line, cur.beg);
      }
    }
  }
}

function checkFamilies(ctx, docs, rel) {
  const begs = new Set(docs.map((d) => d.beg)), ends = new Set(docs.map((d) => d.end));
  for (const d of docs) {
    const ba = d.begattach, ea = d.endattach;
    if (!ba && !ea) continue;
    const b = ba ? splitBates(ba) : null, e = ea ? splitBates(ea) : null;
    if (!b || !e) {
      ctx.add("error", "FAMILY_FORMAT", `Unparseable family range '${ba}'-'${ea}'`, rel, d.line, d.beg);
      continue;
    }
    if (!(b[0] === d.b[0] && d.b[0] === e[0] && b[1] <= d.b[1] && d.e[1] <= e[1])) {
      ctx.add("error", "FAMILY_RANGE", `Document falls outside its family range ${ba}-${ea}`, rel, d.line, d.beg);
    }
    if (!begs.has(ba)) ctx.add("error", "FAMILY_PARENT", `BegAttach ${ba} is not the Beg Bates of any produced document`, rel, d.line, d.beg);
    if (!ends.has(ea)) ctx.add("error", "FAMILY_END", `EndAttach ${ea} is not the End Bates of any produced document`, rel, d.line, d.beg);
  }
}

async function checkImages(ctx, rel, kind) {
  const [text, encoding] = await load(ctx, rel);
  let recs;
  if (kind === "opt") {
    recs = parseOpt(text);
    for (const r of recs) if (r.ncols < 3) ctx.add("error", "OPT_FORMAT", `Expected 7 columns, found ${r.ncols}`, rel, r.line);
  } else {
    recs = parseLfp(text)[0];
    for (const r of recs) if (!r.path) ctx.add("error", "LFP_FORMAT", "IM line missing @Volume;Dir;File;Type", rel, r.line);
  }
  recs = recs.filter((r) => r.key);
  if (recs.length && !recs[0].docbreak) {
    ctx.add("error", "NO_FIRST_DOCBREAK", "First image is not flagged as a document break", rel, recs[0].line, recs[0].key);
  }
  const seen = new Map(), pages = new Map(); let current = null;
  for (const r of recs) {
    if (seen.has(r.key)) ctx.add("error", "DUPLICATE_IMAGE_KEY", `Image key also on line ${seen.get(r.key)}`, rel, r.line, r.key);
    if (!seen.has(r.key)) seen.set(r.key, r.line);
    if (r.path) checkFile(ctx, r.path, "Image", rel, r.line, r.key);
    if (r.docbreak) { current = r.key; pages.set(current, { count: 0, line: r.line, declared: r.pagecount }); }
    if (current) pages.get(current).count++;
  }
  for (const [key, p] of pages) {
    if (p.declared && p.declared !== String(p.count)) {
      ctx.add("warning", "OPT_PAGECOUNT", `Declares ${p.declared} pages, ${p.count} image lines follow`, rel, p.line, key);
    }
  }
  return [{ path: rel, encoding }, pages, recs.length];
}

function reconcile(ctx, docs, pages, imgRel, datRel) {
  const datBegs = new Set();
  for (const d of docs) {
    if (!d.b) continue;
    datBegs.add(d.beg);
    const p = pages.get(d.beg);
    if (!p) ctx.add("error", "NO_IMAGE_DOC", `No document break in ${imgRel} for this document`, datRel, d.line, d.beg);
    else if (p.count !== d.span) {
      ctx.add("error", "PAGE_MISMATCH", `Bates span is ${d.span} pages, ${imgRel} has ${p.count} images`, datRel, d.line, d.beg);
    }
  }
  for (const key of [...pages.keys()].filter((k) => !datBegs.has(k)).sort(cmp)) {
    ctx.add("error", "ORPHAN_IMAGE_DOC", "Image document not present in the DAT", imgRel, pages.get(key).line, key);
  }
}

export function discover(files, suffix) {
  return [...files.keys()].filter((p) => p.toLowerCase().endsWith(suffix) && !p.split("/").pop().startsWith("._")).sort(cmp);
}

export async function runQc(files, datRel = null, imageRel = null, overrides = {}) {
  const findings = [];
  const ctx = {
    files, listing: buildListing(files),
    add: (severity, code, message, source = "", line = 0, bates = "") => findings.push({ severity, code, message, source, line, bates }),
  };
  const loadFiles = []; let docs = [], pages = null, nImages = 0;
  if (datRel) {
    const [info, d] = await checkDat(ctx, datRel, overrides);
    docs = d; loadFiles.push(info);
  }
  if (imageRel) {
    const kind = imageRel.toLowerCase().endsWith(".lfp") ? "lfp" : "opt";
    const [info, p, n] = await checkImages(ctx, imageRel, kind);
    pages = p; nImages = n; loadFiles.push(info);
    if (datRel) reconcile(ctx, docs, pages, info.path, loadFiles[0].path);
  }
  for (const lf of loadFiles) lf.sha256 = await sha256Hex(await files.get(lf.path)());

  findings.sort((a, b) => cmpTuple([SEVERITY_ORDER[a.severity], a.code, a.source, a.line, a.bates, a.message],
    [SEVERITY_ORDER[b.severity], b.code, b.source, b.line, b.bates, b.message]));
  const valid = docs.filter((d) => d.b);
  const ranges = new Map();
  for (const d of [...valid].sort((x, y) => cmp(x.b[0], y.b[0]) || x.b[1] - y.b[1])) {
    if (!ranges.has(d.b[0])) ranges.set(d.b[0], [d.beg, d.end]);
    ranges.get(d.b[0])[1] = d.end;
  }
  const codes = {};
  for (const x of findings) codes[x.code] = (codes[x.code] || 0) + 1;
  const byCode = Object.fromEntries(Object.entries(codes).sort((a, b) => cmp(a[0], b[0])));
  const errors = findings.filter((x) => x.severity === "error").length;
  return {
    tool: "prodflow", tool_version: VERSION,
    load_files: loadFiles,
    summary: {
      dat_records: loadFiles.reduce((s, lf) => s + (lf.records || 0), 0),
      documents_validated: valid.length, image_records: nImages, bates_pages: valid.reduce((s, d) => s + d.span, 0),
      bates_ranges: [...ranges.entries()].sort((a, b) => cmp(a[0], b[0])).map(([k, v]) => ({ prefix: k, first: v[0], last: v[1] })),
      errors, warnings: findings.length - errors, by_code: byCode, result: errors ? "FAIL" : "PASS",
    },
    findings,
  };
}

export function renderQcMarkdown(report) {
  const s = report.summary;
  const out = [`# Production QC Report — ${s.result}`, "",
    `Generated by prodflow ${report.tool_version}. Deterministic: identical inputs produce an identical report.`, "",
    "## Load files", "", "| File | Encoding | SHA-256 |", "|---|---|---|"];
  for (const lf of report.load_files) out.push(`| \`${lf.path}\` | ${lf.encoding} | \`${lf.sha256}\` |`);
  out.push("", "## Summary", "", "| Measure | Value |", "|---|---|",
    `| DAT records | ${s.dat_records} |`, `| Documents with valid Bates | ${s.documents_validated} |`, `| Image records | ${s.image_records} |`,
    `| Pages by Bates span | ${s.bates_pages} |`, `| Errors | ${s.errors} |`, `| Warnings | ${s.warnings} |`);
  for (const r of s.bates_ranges) out.push(`| Range \`${r.prefix}\` | ${r.first} – ${r.last} |`);
  const codes = Object.entries(s.by_code);
  if (codes.length) {
    out.push("", "## Findings by type", "", "| Code | Count |", "|---|---|");
    for (const [c, n] of codes) out.push(`| ${c} | ${n} |`);
    out.push("", "## Findings", "", "| Severity | Code | Bates / Key | Source:Line | Detail |", "|---|---|---|---|---|");
    for (const x of report.findings) {
      out.push(`| ${x.severity} | ${x.code} | ${x.bates} | ${x.source}:${x.line} | ${x.message.replaceAll("|", "/")} |`);
    }
  } else out.push("", "No findings.");
  return out.join("\n") + "\n";
}

// ---------------------------------------------------------------- TAR statistics
function binomCdf(k, n, p) {
  if (p <= 0) return 1;
  if (p >= 1) return k >= n ? 1 : 0;
  const lp = Math.log(p), lq = Math.log1p(-p);
  let logC = 0, total = 0;
  for (let i = 0; i <= k; i++) {
    if (i > 0) logC += Math.log(n - i + 1) - Math.log(i);
    total += Math.exp(logC + i * lp + (n - i) * lq);
  }
  return Math.min(total, 1);
}
function bisect(fn, target, increasing) {
  let lo = 0, hi = 1;
  for (let i = 0; i < 200; i++) {
    const mid = (lo + hi) / 2;
    if ((fn(mid) < target) === increasing) lo = mid; else hi = mid;
  }
  return (lo + hi) / 2;
}
export function clopperPearson(k, n, confidence = 0.95) {
  if (!(Number.isInteger(k) && Number.isInteger(n)) || k < 0 || k > n || n <= 0) throw new RangeError("Require 0 <= k <= n and n > 0");
  const a = (1 - confidence) / 2;
  const lower = k === 0 ? 0 : bisect((p) => 1 - binomCdf(k - 1, n, p), a, true);
  const upper = k === n ? 1 : bisect((p) => binomCdf(k, n, p), a, false);
  return [lower, upper];
}

// Wichura AS241, identical to Python's statistics.NormalDist.inv_cdf
function normInv(p) {
  const q = p - 0.5;
  if (Math.abs(q) <= 0.425) {
    const r = 0.180625 - q * q;
    const num = (((((((2.5090809287301226727e+3 * r + 3.3430575583588128105e+4) * r + 6.7265770927008700853e+4) * r +
      4.5921953931549871457e+4) * r + 1.3731693765509461125e+4) * r + 1.9715909503065514427e+3) * r +
      1.3314166789178437745e+2) * r + 3.3871328727963666080e+0) * q;
    const den = ((((((5.2264952788528545610e+3 * r + 2.8729085735721942674e+4) * r + 3.9307895800092710610e+4) * r +
      2.1213794301586595867e+4) * r + 5.3941960214247511077e+3) * r + 6.8718700749205790830e+2) * r +
      4.2313330701600911252e+1) * r + 1.0;
    return num / den;
  }
  let r = q <= 0 ? p : 1 - p;
  r = Math.sqrt(-Math.log(r));
  let num, den;
  if (r <= 5) {
    r -= 1.6;
    num = ((((((7.7454501427834140764e-4 * r + 2.2723844989269184583e-2) * r + 2.4178072517745061177e-1) * r +
      1.2704582524523683826e+0) * r + 3.6478483247632045605e+0) * r + 5.7694972214606914055e+0) * r +
      4.6303378461565452959e+0) * r + 1.4234371107496835773e+0;
    den = ((((((1.0507500716444168432e-9 * r + 5.4759380849953449460e-4) * r + 1.5198666563616457197e-2) * r +
      1.4810397642748007459e-1) * r + 6.8976733498510000455e-1) * r + 1.6763848301838038494e+0) * r +
      2.0531916266377588219e+0) * r + 1.0;
  } else {
    r -= 5;
    num = ((((((2.0103343992922881327e-7 * r + 2.7115555687434875782e-5) * r + 1.2426609473880784386e-3) * r +
      2.6532189526576123093e-2) * r + 2.9656057182850489123e-1) * r + 1.7848265399172913358e+0) * r +
      5.4637849111641143699e+0) * r + 6.6579046435011037772e+0;
    den = ((((((2.0442631033899397856e-15 * r + 1.4215117583164458887e-7) * r + 1.8463183175100546818e-5) * r +
      7.8686913114561325910e-4) * r + 1.4875361290850614853e-2) * r + 1.3692988092273580531e-1) * r +
      5.9983220655588793769e-1) * r + 1.0;
  }
  const x = num / den;
  return q < 0 ? -x : x;
}

export function sampleSize(confidence = 0.95, margin = 0.02, population = null, p = 0.5) {
  if (!(confidence > 0 && confidence < 1 && margin > 0 && margin < 1)) throw new RangeError("confidence and margin must be between 0 and 1");
  const z = normInv(1 - (1 - confidence) / 2);
  const n0 = (z * z * p * (1 - p)) / (margin * margin);
  return Math.ceil(population ? n0 / (1 + (n0 - 1) / population) : n0);
}

const r6 = (x) => { const s = x * 1e6; const r = Math.round(s); return (Math.abs(s % 1) === 0.5 ? 2 * Math.round(s / 2) : r) / 1e6; };

export function elusion(nullSetSize, sampleN, sampleResponsive, responsiveFound, confidence = 0.95) {
  for (const [name, v] of [["null_set_size", nullSetSize], ["sample_n", sampleN], ["sample_responsive", sampleResponsive], ["responsive_found", responsiveFound]]) {
    if (!Number.isInteger(v) || v < 0) throw new RangeError(`${name} must be a non-negative integer`);
  }
  if (sampleN > nullSetSize) throw new RangeError("sample_n cannot exceed null_set_size");
  const [lo, hi] = clopperPearson(sampleResponsive, sampleN, confidence);
  const rate = sampleResponsive / sampleN;
  const missed = [rate * nullSetSize, lo * nullSetSize, hi * nullSetSize];
  const recall = (m) => (responsiveFound + m ? responsiveFound / (responsiveFound + m) : 0);
  return {
    inputs: { null_set_size: nullSetSize, sample_n: sampleN, sample_responsive: sampleResponsive, responsive_found: responsiveFound, confidence },
    elusion_rate: r6(rate), elusion_ci: [r6(lo), r6(hi)],
    est_missed: r6(missed[0]), est_missed_ci: [r6(missed[1]), r6(missed[2])],
    recall: r6(recall(missed[0])), recall_ci: [r6(recall(missed[2])), r6(recall(missed[1]))],
    margin_achieved: r6(Math.max(rate - lo, hi - rate)),
  };
}

const pct = (x) => `${(x * 100).toFixed(2)}%`;
const int0 = (x) => commas(Math.abs(roundHalfEven(x)) === 0 ? 0 : roundHalfEven(x));
const pyG = (x) => String(Number(x.toPrecision(6)));

export function elusionMemo(r, matter = "[Matter]") {
  const i = r.inputs, c = `${pyG(i.confidence * 100)}%`;
  return `# Elusion Test and Recall Estimate — ${matter}

## Result
Estimated recall is **${pct(r.recall)}** (${c} CI: ${pct(r.recall_ci[0])} – ${pct(r.recall_ci[1])}).
An estimated **${int0(r.est_missed)}** responsive documents remain in the null set
(${c} CI: ${int0(r.est_missed_ci[0])} – ${int0(r.est_missed_ci[1])}).

## Inputs
| Input | Value |
|---|---|
| Null set (documents not produced) | ${commas(i.null_set_size)} |
| Simple random sample drawn from null set | ${commas(i.sample_n)} |
| Responsive documents found in sample | ${commas(i.sample_responsive)} |
| Responsive documents identified by review | ${commas(i.responsive_found)} |
| Confidence level | ${c} |

## Method
1. A simple random sample of ${commas(i.sample_n)} documents was drawn from the null set.
2. Sample documents were reviewed blind to their predicted classification.
3. Elusion rate = responsive in sample / sample size = ${i.sample_responsive} / ${i.sample_n} = ${pct(r.elusion_rate)}.
   The interval (${pct(r.elusion_ci[0])} – ${pct(r.elusion_ci[1])}) is an exact Clopper-Pearson binomial interval.
4. Estimated missed = elusion rate × null set size.
5. Recall = responsive identified / (responsive identified + estimated missed). The recall interval is obtained by
   substituting the elusion interval bounds.

## Limitations
- Assumes the sample was drawn uniformly at random from the entire null set.
- Treats the count of responsive documents identified by review as exact; reviewer error in that
  population is not modeled.
- No finite-population correction is applied to the interval, which makes it slightly conservative.
- Achieved margin of error on the elusion rate: ±${pct(r.margin_achieved)}.

*Generated by prodflow. All figures are reproducible from the inputs above.*
`;
}

// ---------------------------------------------------------------- matter estimate
export const ESTIMATE_DEFAULTS = {
  docs_per_gb: { low: 4000, high: 6000 },
  dedupe_reduction: { low: 0.30, high: 0.20 },
  culling_reduction: { low: 0.65, high: 0.50 },
  review_docs_per_hour: { low: 55, high: 45 },
  reviewer_rate_per_hour: { low: 60, high: 80 },
  qc_overhead: { low: 0.10, high: 0.20 },
  processing_per_gb: { low: 30, high: 75 },
  hosting_per_gb_month: { low: 6, high: 12 },
};

export function estimate(gb, months = 6, assumptions = ESTIMATE_DEFAULTS) {
  if (!(gb > 0) || !(months >= 0)) throw new RangeError("gb must be > 0 and months >= 0");
  const scenarios = {};
  for (const s of ["low", "high"]) {
    const v = Object.fromEntries(Object.entries(assumptions).map(([k, x]) => [k, x[s]]));
    const collected = gb * v.docs_per_gb;
    const afterDedupe = collected * (1 - v.dedupe_reduction);
    const reviewSet = afterDedupe * (1 - v.culling_reduction);
    const hours = (reviewSet / v.review_docs_per_hour) * (1 + v.qc_overhead);
    const hostedGb = gb * (1 - v.dedupe_reduction);
    const cost = { processing: gb * v.processing_per_gb, hosting: hostedGb * v.hosting_per_gb_month * months, review: hours * v.reviewer_rate_per_hour };
    cost.total = cost.processing + cost.hosting + cost.review;
    scenarios[s] = {
      docs_collected: roundHalfEven(collected), docs_after_dedupe: roundHalfEven(afterDedupe),
      docs_to_review: roundHalfEven(reviewSet), review_hours: roundHalfEven(hours),
      cost: Object.fromEntries(Object.entries(cost).map(([k, x]) => [k, roundHalfEven(x)])),
    };
  }
  return { inputs: { gb, months }, assumptions, scenarios };
}

const pyFloat = (x) => { if (Number.isInteger(x)) return commas(x) + ".0"; const [a, b] = String(x).split("."); return commas(a) + (b ? "." + b : ""); };

export function renderEstimateMarkdown(r) {
  const lo = r.scenarios.low, hi = r.scenarios.high;
  const rows = [["Documents collected", "docs_collected"], ["After de-duplication", "docs_after_dedupe"],
    ["Review set after culling", "docs_to_review"], ["Review hours (incl. QC)", "review_hours"]];
  const out = [`# Matter Estimate — ${pyFloat(r.inputs.gb)} GB, ${r.inputs.months} months hosting`, "",
    "| Measure | Low | High |", "|---|---:|---:|"];
  for (const [label, k] of rows) out.push(`| ${label} | ${commas(lo[k])} | ${commas(hi[k])} |`);
  for (const k of ["processing", "hosting", "review"]) {
    out.push(`| ${k[0].toUpperCase() + k.slice(1)} cost | $${commas(lo.cost[k])} | $${commas(hi.cost[k])} |`);
  }
  out.push(`| **Total** | **$${commas(lo.cost.total)}** | **$${commas(hi.cost.total)}** |`, "",
    "## Assumptions", "", "| Assumption | Low | High |", "|---|---:|---:|");
  for (const [k, v] of Object.entries(r.assumptions)) out.push(`| ${k} | ${v.low} | ${v.high} |`);
  out.push("", "Planning ranges only; not a vendor quote. Low and High apply every assumption's favorable or unfavorable end together, " +
    "so the spread is a bounding range, not a confidence interval. Override with `--assumptions file.json`.");
  return out.join("\n") + "\n";
}

// ---------------------------------------------------------------- audit trail (same format as the CLI's audit.jsonl)
export async function auditEntry({ command, inputs, outputs, operator, runtime, prev = GENESIS }) {
  const entry = {
    timestamp_utc: new Date().toISOString().replace(/\.\d{3}Z$/, "+00:00"),
    tool: "prodflow-web", tool_version: VERSION, runtime, host: "browser",
    operator: operator || "unspecified", command,
    inputs: [...inputs].sort((a, b) => cmp(a.path, b.path)),
    outputs: [...outputs].sort((a, b) => cmp(a.path, b.path)),
    prev_entry_sha256: prev,
  };
  entry.entry_sha256 = await sha256Hex(canonical(entry));
  return entry;
}
