"""SHA-256 hashing, read-only guards, and the hash-chained audit log."""
import getpass
import hashlib
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import __version__

GENESIS = "0" * 64


class IntegrityError(Exception):
    pass


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def snapshot(paths):
    return {str(p): sha256_file(p) for p in sorted(set(map(str, paths)))}


def confirm_unchanged(before):
    """Re-hash every input; raise if any source file changed while we worked on it."""
    after = {}
    for path, digest in before.items():
        after[path] = sha256_file(path)
        if after[path] != digest:
            raise IntegrityError(f"Source file changed during processing: {path} ({digest} -> {after[path]})")
    return after


def ensure_outside(out_dir, source_root):
    """Refuse to write outputs inside the source evidence tree."""
    out, src = Path(out_dir).resolve(), Path(source_root).resolve()
    if out == src or src in out.parents:
        raise IntegrityError(f"Output directory {out} is inside the source production {src}; choose another location.")


def _canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _last_entry_hash(log_path):
    p = Path(log_path)
    if not p.exists() or p.stat().st_size == 0:
        return GENESIS
    lines = p.read_text(encoding="utf-8").splitlines()
    return json.loads(lines[-1])["entry_sha256"]


def append_audit(log_path, command, inputs_before, inputs_after, outputs, operator=None):
    """Append one tamper-evident entry. Each entry commits to the previous entry's hash."""
    entry = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tool": "prodflow",
        "tool_version": __version__,
        "python": platform.python_version(),
        "host": platform.node(),
        "operator": operator or getpass.getuser(),
        "command": command,
        "inputs": [
            {"path": p, "sha256_before": inputs_before[p], "sha256_after": inputs_after[p]}
            for p in sorted(inputs_before)
        ],
        "outputs": [{"path": str(p), "sha256": sha256_file(p)} for p in sorted(map(str, outputs))],
        "prev_entry_sha256": _last_entry_hash(log_path),
    }
    entry["entry_sha256"] = hashlib.sha256(_canonical(entry).encode("utf-8")).hexdigest()
    Path(log_path).parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(_canonical(entry) + "\n")
    return entry


def verify_audit(log_path):
    """Return a list of problems; empty means the chain is intact."""
    problems, prev = [], GENESIS
    for n, line in enumerate(Path(log_path).read_text(encoding="utf-8").splitlines(), 1):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as e:
            problems.append(f"line {n}: not valid JSON ({e})")
            break
        claimed = entry.pop("entry_sha256", None)
        actual = hashlib.sha256(_canonical(entry).encode("utf-8")).hexdigest()
        if claimed != actual:
            problems.append(f"line {n}: entry hash mismatch (entry was altered)")
        if entry.get("prev_entry_sha256") != prev:
            problems.append(f"line {n}: chain broken (entry removed, reordered or inserted)")
        prev = claimed
    return problems


def command_line():
    return " ".join(sys.argv)
