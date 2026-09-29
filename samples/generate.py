"""Builds two fully synthetic productions: one clean, one with known, documented defects.

No real case data. Output is deterministic: rerunning produces byte-identical files.
Usage: python samples/generate.py
"""
import hashlib
import shutil
from pathlib import Path

HERE = Path(__file__).parent
D, Q = "\x14", "\xfe"
TIFF_PLACEHOLDER = b"II*\x00" + b"\x00" * 60  # TIFF magic bytes; image content is irrelevant to QC
PAGES = [3, 1, 2, 1, 4, 1, 2, 1, 1, 3, 2, 1]
FAMILIES = {0: (0, 2), 1: (0, 2), 2: (0, 2), 5: (5, 6), 6: (5, 6)}
NATIVES = {3: "Budget_FY2021.xlsx", 8: "Vendor_List.xlsx"}
CUSTODIANS = ["Smith, Jordan", "Lee, Casey", "Patel, Riley"]
HEADER = ["BegBates", "EndBates", "BegAttach", "EndAttach", "Custodian", "DateSent",
          "FileName", "PageCount", "NativeLink", "TextLink", "SHA256"]

# Each defect is listed in the README with the finding it should trigger.
DEFECTS = {
    "gap": "Documents 8-12 start 5 pages later than expected (withheld range not logged)",
    "overlap": "Document 10's EndBates runs one page into document 11",
    "missing_image": "One TIFF referenced in the OPT is absent from disk",
    "field_count": "Row for document 5 is missing one delimiter",
    "bad_date": "Document 6 has DateSent 13/45/2021",
    "hash_mismatch": "Native for document 4 was altered after its SHA-256 was recorded",
    "family": "Document 3's EndAttach points to a page that is not a document end",
    "empty_text": "Document 12 has a zero-byte extracted text file",
    "case": "Document 2's TextLink uses lowercase 'text\\' while the folder is TEXT",
}


def bates(n):
    return f"ABC{n:07d}"


def build(root, defective):
    if root.exists():
        shutil.rmtree(root)
    for sub in ("DATA", "IMAGES/001", "NATIVES/001", "TEXT/001"):
        (root / sub).mkdir(parents=True)

    starts, n = [], 1
    for i, pages in enumerate(PAGES):
        if defective and i == 7:
            n += 5
        starts.append(n)
        n += pages

    dat_rows, opt_lines = [HEADER], []
    for i, pages in enumerate(PAGES):
        beg, end = bates(starts[i]), bates(starts[i] + pages - 1)
        for p in range(pages):
            key = bates(starts[i] + p)
            opt_lines.append(f"{key},PROD001,IMAGES\\001\\{key}.tif,{'Y' if p == 0 else ''},,,{pages if p == 0 else ''}")
            if not (defective and key == bates(starts[4] + 2)):
                (root / f"IMAGES/001/{key}.tif").write_bytes(TIFF_PLACEHOLDER)

        text = b"" if defective and i == 11 else f"Synthetic extracted text for {beg}.\r\n".encode()
        (root / f"TEXT/001/{beg}.txt").write_bytes(text)
        textlink = f"{'text' if defective and i == 1 else 'TEXT'}\\001\\{beg}.txt"

        native, sha = "", ""
        if i in NATIVES:
            content = f"Synthetic spreadsheet placeholder {NATIVES[i]}".encode()
            sha = hashlib.sha256(content).hexdigest()
            if defective and i == 3:
                content += b" (modified)"
            native = f"NATIVES\\001\\{beg}.xlsx"
            (root / f"NATIVES/001/{beg}.xlsx").write_bytes(content)

        ba = ea = ""
        if i in FAMILIES:
            a, b = FAMILIES[i]
            ba, ea = bates(starts[a]), bates(starts[b] + PAGES[b] - 1)
            if defective and i == 2:
                ea = bates(starts[b] + PAGES[b] - 2)
        if defective and i == 9:
            end = bates(starts[i] + pages)
        date = "13/45/2021" if defective and i == 5 else f"0{1 + i % 9}/1{i % 10}/2021"
        row = [beg, end, ba, ea, CUSTODIANS[i % 3], date, NATIVES.get(i, f"Email_{i + 1:03d}.msg"),
               str(pages), native, textlink, sha]
        if defective and i == 4:
            row = row[:4] + row[5:]
        dat_rows.append(row)

    dat = "\r\n".join(D.join(f"{Q}{v}{Q}" for v in r) for r in dat_rows) + "\r\n"
    (root / "DATA/PROD001.dat").write_bytes(b"\xef\xbb\xbf" + dat.encode("utf-8"))
    (root / "DATA/PROD001.opt").write_bytes(("\r\n".join(opt_lines) + "\r\n").encode("utf-8"))


if __name__ == "__main__":
    build(HERE / "clean_production", defective=False)
    build(HERE / "defective_production", defective=True)
    print("Built samples/clean_production and samples/defective_production")
