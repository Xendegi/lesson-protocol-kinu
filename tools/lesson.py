#!/usr/bin/env python3
"""
lesson.py — ledger tooling for the Lesson Protocol (kinu).

Commands:
  add      Append a new lesson with the next free number (interactive or flags)
  lint     Check ledger integrity: duplicate ids, gaps, ordering, four-part shape
  digest   Render a one-screen index (number | date | title)

Ids are alphanumeric: digits with an optional single lowercase suffix
(e.g. 12, 12b) — suffixes mark chronologically later duplicates of a
collided number without renumbering anything (append-only stays intact).

The ledger is canonical; this tool never rewrites existing entries.
"""

import argparse
import datetime
import re
import sys
from pathlib import Path

LEDGER = Path(__file__).resolve().parent / "ledger.md"
ENCODING = "utf-8-sig"  # tolerate BOM; writes stay UTF-8 (BOM stripped on read)

# --- parsing -----------------------------------------------------------------

# id: digits + optional single-letter suffix ("12", "12b")
LESSON_RE = re.compile(
    r"^## Lesson\s+(\d+[a-z]?)\s*[-–—·]\s*"
    r"(\d{4}-\d{2}-\d{2}(?:/\d{2})?)\s*[-–—·]\s*(.+?)\s*$"
)
ID_RE = re.compile(r"^(\d+)([a-z]?)$")
PARTS = ("His words", "The reveal", "The change", "The worth")
PART_RE = {p: re.compile(rf"^- \*\*{re.escape(p)}[: ]", re.M) for p in PARTS}


def id_key(idstr: str):
    """Sort key for alphanumeric ids: (12, "") < (12, "b") < (13, "")."""
    m = ID_RE.match(idstr)
    if not m:
        return (10**9, idstr)  # malformed ids sort last
    return (int(m.group(1)), m.group(2))


def parse(text: str):
    """Split ledger into entries in file order."""
    entries = []
    lines = text.splitlines()
    starts = [i for i, ln in enumerate(lines) if LESSON_RE.match(ln)]
    for idx, start in enumerate(starts):
        m = LESSON_RE.match(lines[start])
        end = starts[idx + 1] if idx + 1 < len(starts) else len(lines)
        body = "\n".join(lines[start + 1 : end])
        entries.append(
            {
                "id": m.group(1),
                "date": m.group(2),
                "title": m.group(3),
                "body": body,
                "line": start + 1,
            }
        )
    return entries


# --- commands ----------------------------------------------------------------


def cmd_lint(args) -> int:
    path = Path(args.ledger)
    if not path.exists():
        print(f"lint: ledger not found: {path}", file=sys.stderr)
        return 2
    entries = parse(path.read_text(encoding=ENCODING))
    if not entries:
        print("lint: no lessons found — wrong file?", file=sys.stderr)
        return 2

    errors, warnings = [], []
    seen = {}
    prev = None
    for e in entries:
        lid, where = e["id"], f"line {e['line']}"

        # malformed id (should not happen — parser enforces the shape)
        if not ID_RE.match(lid):
            errors.append(f"malformed lesson id '{lid}' ({where})")

        # duplicate ids
        if lid in seen:
            errors.append(f"DUPLICATE lesson id {lid} ({where}; first at line {seen[lid]})")
        else:
            seen[lid] = e["line"]

        # date validity
        try:
            datetime.date.fromisoformat(e["date"].split("/")[0])
        except ValueError:
            errors.append(f"lesson {lid}: invalid date '{e['date']}' ({where})")

        # four-part shape
        for part, rx in PART_RE.items():
            if not rx.search(e["body"]):
                errors.append(f"lesson {lid}: missing part '{part}' ({where})")

        # empty 'The change'
        m = re.search(
            r"^- \*\*The change:\*\*\s*(.+?)(?=^- \*\*|^## |\Z)",
            e["body"],
            re.M | re.S,
        )
        if m and not m.group(1).strip(" .\n-*"):
            errors.append(f"lesson {lid}: 'The change' is empty ({where})")

        # ordering — ADVISORY only (append-only ledgers keep historical layout;
        # the read side sorts via `digest --sort`)
        if prev is not None and id_key(lid) <= id_key(prev["id"]):
            warnings.append(
                f"lesson {lid} at {where} does not follow "
                f"{prev['id']} (append order — advisory, historical layout kept)"
            )
        prev = e

    # gaps in numbering (informational)
    nums = sorted({id_key(i)[0] for i in seen})
    missing = [n for n in range(nums[0], nums[-1] + 1) if n not in set(nums)]
    if missing:
        warnings.append(f"gap in numbering: {', '.join(map(str, missing))}")

    for w in warnings:
        print(f"WARN  {w}")
    for err in errors:
        print(f"ERROR {err}")
    print(
        f"\n{len(entries)} lessons checked — "
        f"{len(errors)} error(s), {len(warnings)} warning(s)"
    )

    if errors:
        return 1
    if args.strict and warnings:
        print("strict mode: warnings treated as errors")
        return 1
    return 0


def cmd_digest(args) -> int:
    path = Path(args.ledger)
    if not path.exists():
        print(f"digest: ledger not found: {path}", file=sys.stderr)
        return 2
    entries = parse(path.read_text(encoding=ENCODING))
    if args.sort:
        # chronological/logical order on the read side — file order untouched
        entries.sort(key=lambda e: (e["date"].split("/")[0], id_key(e["id"])))
    out = [
        "# LEDGER DIGEST",
        "",
        f"_{len(entries)} lessons · generated {datetime.date.today().isoformat()}"
        + (" · sorted" if args.sort else " · file order") + "_",
        "",
        "| # | Date | Title |",
        "|---|------|-------|",
    ]
    for e in entries:
        out.append(f"| {e['id']} | {e['date']} | {e['title']} |")
    text = "\n".join(out) + "\n"
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"digest → {args.output} ({len(entries)} lessons)")
    else:
        print(text)
    return 0


def cmd_add(args) -> int:
    path = Path(args.ledger)
    if not path.exists():
        print(f"add: ledger not found: {path}", file=sys.stderr)
        return 2
    text = path.read_text(encoding=ENCODING)
    entries = parse(text)
    # next plain number = highest numeric part + 1 (suffixes share their number)
    n = (max(id_key(e["id"])[0] for e in entries) + 1) if entries else 1
    date = args.date or datetime.date.today().isoformat()

    # gather the four parts: flags if given, else prompt
    def get(flag_val, label):
        if flag_val is not None:
            return flag_val.strip()
        print(f"{label}:")
        lines = []
        while True:
            line = input()
            if line == "":
                break
            lines.append(line)
        return " ".join(lines).strip()

    parts = {
        "His words": get(args.words, "His words (verbatim)"),
        "The reveal": get(args.reveal, "The reveal"),
        "The change": get(args.change, "The change"),
        "The worth": get(args.worth, "The worth"),
    }

    for label, value in parts.items():
        if not value:
            print(
                f"add: aborted — '{label}' is empty "
                f"(anti-noise gate: no empty parts)",
                file=sys.stderr,
            )
            return 1

    entry = [f"\n## Lesson {n} — {date} — {args.title}\n"]
    for label in PARTS:
        entry.append(f"- **{label}:** {parts[label]}")
    entry.append("")

    with path.open("a", encoding="utf-8") as f:
        f.write("\n".join(entry))
    print(f"committed: Lesson {n} — {args.title}")
    return 0


def main():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--ledger", default=str(LEDGER), help="path to ledger.md")

    ap = argparse.ArgumentParser(prog="lesson", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("lint", parents=[common], help="check ledger integrity")
    p.add_argument(
        "--strict",
        action="store_true",
        help="treat advisory warnings (order, gaps) as errors",
    )
    p.set_defaults(func=cmd_lint)

    p = sub.add_parser("digest", parents=[common], help="render one-screen index")
    p.add_argument("-o", "--output", help="write digest to file instead of stdout")
    p.add_argument(
        "--sort",
        action="store_true",
        help="chronological/logical order on the read side (file order untouched)",
    )
    p.set_defaults(func=cmd_digest)

    p = sub.add_parser("add", parents=[common], help="append a lesson (auto-numbered)")
    p.add_argument("--title", required=True)
    p.add_argument("--date", help="YYYY-MM-DD (default: today)")
    p.add_argument("--words", help="His words, verbatim")
    p.add_argument("--reveal", help="The reveal")
    p.add_argument("--change", help="The change")
    p.add_argument("--worth", help="The worth")
    p.set_defaults(func=cmd_add)

    args = ap.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
