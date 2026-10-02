#!/usr/bin/env python3
"""
check_anonymity.py — exposure guard for the published repository.

Fails (exit 1) if identity tokens appear anywhere in git-tracked files or in
tracked file paths. Run manually before push, automatically by pre-commit
(tools/hooks/pre-commit) and by CI (.github/workflows/anonymity-guard.yml).

Tokens are assembled at runtime from fragments so that this scanner file
itself never contains a contiguous token — otherwise the guard would flag
its own source.
"""

import re
import subprocess
import sys
from pathlib import Path

MAX_FILE_BYTES = 1_000_000


def t(*parts: str) -> str:
    return "".join(parts)


def _tokens():
    """(literal, word_bounded, label) triples — identity must never appear here."""
    return [
        (t("L", "ux"), True, "agent identity"),
        (t("Am", "ir"), True, "owner name"),
        (t("St", "aid"), True, "machine user"),
        (t("athen", "amiro"), False, "prior account"),
        (t("Open", "Code"), True, "harness name"),
        (t("Mi", "Mo"), True, "model family"),
        (t("her", "mes"), True, "private tooling"),
        (t("g", ":", "\\", "ai"), False, "workspace path"),
        (t("g", ":", "/", "ai"), False, "workspace path"),
        (t("Projects", "\\", "O-", "P"), False, "workspace path"),
        (t("Projects", "/", "O-", "P"), False, "workspace path"),
        (t("users", "\\", "s", "taid"), False, "user profile path"),
    ]


TOKENS = []
for _lit, _bounded, _label in _tokens():
    _pat = re.escape(_lit)          # literals only — never raw regex
    if _bounded:
        _pat = r"\b" + _pat + r"\b"
    TOKENS.append((re.compile(_pat, re.I), _label))


def tracked_files() -> list[str]:
    r = subprocess.run(
        ["git", "ls-files", "-z"], capture_output=True, cwd=Path(__file__).resolve().parents[1]
    )
    if r.returncode != 0:
        print(f"git ls-files failed: {r.stderr.decode('utf-8', 'replace')}", file=sys.stderr)
        sys.exit(2)
    return [p.decode("utf-8", "replace") for p in r.stdout.split(b"\0") if p]


def decode(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        # latin-1 never fails — ASCII tokens remain matchable in legacy files
        return data.decode("latin-1", "replace")


def main() -> int:
    hits = []
    scanned = 0
    for rel in tracked_files():
        # 1) the path itself must be clean
        for rx, label in TOKENS:
            if rx.search(rel):
                hits.append((rel, 0, label, "(file path)"))
        path = Path(rel)
        if not path.is_file():
            continue
        data = path.read_bytes()
        if len(data) > MAX_FILE_BYTES:
            continue
        scanned += 1
        # 2) file contents line by line
        for lineno, line in enumerate(decode(data).splitlines(), 1):
            for rx, label in TOKENS:
                if rx.search(line):
                    snippet = line.strip()[:120]
                    hits.append((rel, lineno, label, snippet))

    if hits:
        print("EXPOSURE DETECTED — commit/push blocked:\n", file=sys.stderr)
        for rel, lineno, label, snippet in hits:
            loc = f"{rel}:{lineno}" if lineno else rel
            print(f"  {loc} [{label}] {snippet}", file=sys.stderr)
        print(f"\n{len(hits)} hit(s). Fix before committing.", file=sys.stderr)
        return 1
    print(f"anonymity guard: clean — {scanned} tracked file(s) scanned, 0 hits")
    return 0


if __name__ == "__main__":
    sys.exit(main())
