// Port of samples/generate.py. Builds the same synthetic productions, byte for byte, in memory.
import { sha256Hex } from "./engine.js";

const D = "\x14", Q = "\xfe";
const TIFF_PLACEHOLDER = new Uint8Array([0x49, 0x49, 0x2a, 0x00, ...new Array(60).fill(0)]);
const PAGES = [3, 1, 2, 1, 4, 1, 2, 1, 1, 3, 2, 1];
const FAMILIES = { 0: [0, 2], 1: [0, 2], 2: [0, 2], 5: [5, 6], 6: [5, 6] };
const NATIVES = { 3: "Budget_FY2021.xlsx", 8: "Vendor_List.xlsx" };
const CUSTODIANS = ["Smith, Jordan", "Lee, Casey", "Patel, Riley"];
const HEADER = ["BegBates", "EndBates", "BegAttach", "EndAttach", "Custodian", "DateSent",
  "FileName", "PageCount", "NativeLink", "TextLink", "SHA256"];

export const DEFECTS = [
  ["Bates gap", "Documents 8-12 start 5 pages later than expected (withheld range not logged)"],
  ["Bates overlap", "Document 10's EndBates runs one page into document 11"],
  ["Missing image", "One TIFF referenced in the OPT is absent from disk"],
  ["Field count", "Row for document 5 is missing one delimiter"],
  ["Bad date", "Document 6 has DateSent 13/45/2021"],
  ["Hash mismatch", "Native for document 4 was altered after its SHA-256 was recorded"],
  ["Broken family", "Document 3's EndAttach points to a page that is not a document end"],
  ["Empty text", "Document 12 has a zero-byte extracted text file"],
  ["Path case", "Document 2's TextLink uses lowercase 'text\\' while the folder is TEXT"],
];

const utf8 = (s) => new TextEncoder().encode(s);
const bates = (n) => `ABC${String(n).padStart(7, "0")}`;

/** Returns Map<relative path, Uint8Array> for the clean or defective production. */
export async function buildSample(defective) {
  const files = new Map();
  const starts = [];
  let n = 1;
  PAGES.forEach((pages, i) => {
    if (defective && i === 7) n += 5;
    starts.push(n);
    n += pages;
  });

  const datRows = [HEADER], optLines = [];
  for (let i = 0; i < PAGES.length; i++) {
    const pages = PAGES[i];
    const beg = bates(starts[i]);
    let end = bates(starts[i] + pages - 1);
    for (let p = 0; p < pages; p++) {
      const key = bates(starts[i] + p);
      optLines.push(`${key},PROD001,IMAGES\\001\\${key}.tif,${p === 0 ? "Y" : ""},,,${p === 0 ? pages : ""}`);
      if (!(defective && key === bates(starts[4] + 2))) files.set(`IMAGES/001/${key}.tif`, TIFF_PLACEHOLDER);
    }
    files.set(`TEXT/001/${beg}.txt`, defective && i === 11 ? new Uint8Array() : utf8(`Synthetic extracted text for ${beg}.\r\n`));
    const textlink = `${defective && i === 1 ? "text" : "TEXT"}\\001\\${beg}.txt`;

    let native = "", sha = "";
    if (i in NATIVES) {
      let content = utf8(`Synthetic spreadsheet placeholder ${NATIVES[i]}`);
      sha = await sha256Hex(content);
      if (defective && i === 3) content = utf8(`Synthetic spreadsheet placeholder ${NATIVES[i]} (modified)`);
      native = `NATIVES\\001\\${beg}.xlsx`;
      files.set(`NATIVES/001/${beg}.xlsx`, content);
    }
    let ba = "", ea = "";
    if (i in FAMILIES) {
      const [a, b] = FAMILIES[i];
      ba = bates(starts[a]);
      ea = bates(starts[b] + PAGES[b] - 1);
      if (defective && i === 2) ea = bates(starts[b] + PAGES[b] - 2);
    }
    if (defective && i === 9) end = bates(starts[i] + pages);
    const date = defective && i === 5 ? "13/45/2021" : `0${1 + (i % 9)}/1${i % 10}/2021`;
    let row = [beg, end, ba, ea, CUSTODIANS[i % 3], date, NATIVES[i] || `Email_${String(i + 1).padStart(3, "0")}.msg`,
      String(pages), native, textlink, sha];
    if (defective && i === 4) row = [...row.slice(0, 4), ...row.slice(5)];
    datRows.push(row);
  }
  const dat = datRows.map((r) => r.map((v) => `${Q}${v}${Q}`).join(D)).join("\r\n") + "\r\n";
  files.set("DATA/PROD001.dat", new Uint8Array([0xef, 0xbb, 0xbf, ...utf8(dat)]));
  files.set("DATA/PROD001.opt", utf8(optLines.join("\r\n") + "\r\n"));
  return files;
}
