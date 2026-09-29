"""Parsers and writers for Concordance DAT, Opticon OPT, and IPRO LFP load files."""
import codecs
import re
from pathlib import Path, PureWindowsPath

DAT_DELIM = "\x14"   # Concordance default field separator (ASCII 20, shown as ¶)
DAT_QUOTE = "\xfe"   # Concordance default text qualifier (þ)

# Image type codes used when writing LFP. Confirm against your review platform's spec.
LFP_IMAGE_TYPES = {".tif": "2", ".tiff": "2", ".jpg": "4", ".jpeg": "4", ".pdf": "7"}


class LoadFileError(Exception):
    pass


def read_text(path):
    """Decode a load file. Returns (text, encoding, warning_or_None). Raises if undecodable."""
    raw = Path(path).read_bytes()
    for bom, enc in ((codecs.BOM_UTF8, "utf-8-sig"), (codecs.BOM_UTF16_LE, "utf-16"), (codecs.BOM_UTF16_BE, "utf-16")):
        if raw.startswith(bom):
            return raw.decode(enc), enc, None
    try:
        return raw.decode("utf-8"), "utf-8", None
    except UnicodeDecodeError as e:
        utf8_error = e
    try:
        return raw.decode("cp1252"), "cp1252", f"Not valid UTF-8 ({utf8_error}); decoded as Windows-1252"
    except UnicodeDecodeError as e:
        raise LoadFileError(f"{path}: not decodable as UTF-8 or Windows-1252: {e}") from e


def parse_dat(text, delim=DAT_DELIM, quote=DAT_QUOTE):
    """Qualifier-aware parse. Returns (records, unterminated) where records = [(line_no, [fields])]."""
    records, fields, buf = [], [], []
    in_q, line, start = False, 1, 1
    for ch in text:
        if in_q:
            if ch == quote:
                in_q = False
            else:
                buf.append(ch)
                if ch == "\n":
                    line += 1
        elif ch == quote:
            in_q = True
        elif ch == delim:
            fields.append("".join(buf))
            buf = []
        elif ch == "\n":
            fields.append("".join(buf))
            records.append((start, fields))
            fields, buf = [], []
            line += 1
            start = line
        elif ch != "\r":
            buf.append(ch)
    if buf or fields:
        fields.append("".join(buf))
        records.append((start, fields))
    records = [r for r in records if r[1] != [""]]
    return records, in_q


def parse_opt(text):
    """Opticon: ImageKey,Volume,Path,DocBreak,FolderBreak,BoxBreak,PageCount."""
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        cols = line.split(",")
        cols += [""] * (7 - len(cols))
        out.append({
            "line": n, "key": cols[0].strip(), "volume": cols[1].strip(), "path": cols[2].strip(),
            "docbreak": cols[3].strip().upper() == "Y", "pagecount": cols[6].strip(), "ncols": len(line.split(",")),
        })
    return out


def parse_lfp(text):
    """IPRO LFP image lines: IM,Key,DocBreak,Offset,@Volume;Dir;File;Type. Non-IM lines are counted, not parsed."""
    out, other = [], 0
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        parts = line.split(",", 4)
        if parts[0].strip().upper() != "IM":
            other += 1
            continue
        rec = {"line": n, "key": "", "volume": "", "path": "", "docbreak": False, "pagecount": "", "ncols": len(parts)}
        if len(parts) == 5 and parts[4].startswith("@"):
            loc = parts[4][1:].split(";")
            rec.update(key=parts[1].strip(), docbreak=parts[2].strip().upper() == "D", volume=loc[0])
            if len(loc) >= 3:
                rec["path"] = str(PureWindowsPath(loc[1], loc[2]))
        out.append(rec)
    return out, other


def opt_to_lfp(records):
    lines = []
    for r in records:
        p = PureWindowsPath(r["path"])
        lines.append(f"IM,{r['key']},{'D' if r['docbreak'] else ' '},0,@{r['volume']};{p.parent};{p.name};"
                     f"{LFP_IMAGE_TYPES.get(p.suffix.lower(), '2')}")
    return "\r\n".join(lines) + "\r\n"


def lfp_to_opt(records):
    """Page counts are recomputed from document breaks."""
    counts, current = {}, None
    for r in records:
        if r["docbreak"]:
            current = r["key"]
            counts[current] = 0
        if current is not None:
            counts[current] += 1
    lines = [f"{r['key']},{r['volume']},{r['path']},{'Y' if r['docbreak'] else ''},,,"
             f"{counts[r['key']] if r['docbreak'] else ''}" for r in records]
    return "\r\n".join(lines) + "\r\n"


BATES_RE = re.compile(r"(.*?)(\d+)")


def split_bates(value):
    """'ABC000123' -> ('ABC', 123, 6). Returns None if there is no trailing number."""
    m = BATES_RE.fullmatch(value.strip())
    return (m.group(1), int(m.group(2)), len(m.group(2))) if m else None
