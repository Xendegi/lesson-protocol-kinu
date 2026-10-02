#!/usr/bin/env python3
"""
lesson.py — ledger tooling for the Lesson Protocol (kinu).

Commands:
  add      Append a new lesson with the next free number (interactive or flags)
  lint     Check ledger integrity: duplicate ids, gaps, ordering, four-part shape
  digest   Render a one-screen index (number | date | title | change)

The ledger is canonical; this tool never rewrites existing entries.
"""

import argparse
import datetime
import re
import sys
from pathlib import Path

LEDGER = Path(__file__).resolve().parent / "ledger.md"
DATE_FMT = "%Y-%m-%d"

# --- parsing -----------------------------------------------------------------

LESSON_RE = re.compile(
    r"^## Lesson\s+(\d+)\s*[-–—·]\s*(\d{4}-\d{2}-\d{2}(?:/\d{2})?)\s*[-–—·]\s*(.+?)\s*$"
)
PARTS = ("His words", "The reveal", "The change", "The worth")
PART_RE = {p: re.compile(rf"^- \*\*{re.escape(p)}[: ]", re.M) for p in PARTS}


def parse(text: str):
    """Split ledger into (number, date, title, body) entries in file order."""
    entries = []
    lines = text.splitlines()
    starts = [i for i, ln in enumerate(lines) if LESSON_RE.match(ln)]
    for idx, start in enumerate(starts):
        m = LESSON_RE.match(lines[start])
        end = starts[idx + 1] if idx + 1 < len(starts) else len(lines)
        body = "\n".join(lines[start + 1 : end])
        entries.append(
            {
                "n": int(m.group(1)),
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
    entries = parse(path.read_text(encoding="utf-8"))
    if not entries:
        print("lint: no lessons found — wrong file?", file=sys.stderr)
        return 2

    errors, warnings = [], []
    seen = {}
    for e in entries:
        where = f"line {e['line']}"
        # duplicate ids
        if e["n"] in seen:
            errors.append(f"DUPLICATE lesson id {e['n']} ({where}; first at line {seen[e['n']]})")
        else:
            seen[e["n"]] = e["line"]
        # date validity
        try:
            datetime.date.fromisoformat(e["date"].split("/")[0])
        except ValueError:
            errors.append(f"lesson {e['n']}: invalid date '{e['date']}' ({where})")
        # four-part shape
        for part, rx in PART_RE.items():
            if not rx.search(e["body"]):
                errors.append(f"lesson {e['n']}: missing part '{part}' ({where})")
        # empty 'The change'
        m = re.search(r"^- \*\*The change:\*\*\s*(.+?)(?=^- \*\*|^## |\Z)", e["body"], re.M | re.S)
        if m and not m.group(1).strip(" .\n-*"):
            errors.append(f"lesson {e['n']}: 'The change' is empty ({where})")
        # ordering (append-only: should increase)
        if entries.index(e) and e["n"] <= entries[entries.index(e) - 1]["n"]:
            warnings.append(
                f"lesson {e['n']} at {where} does not follow "
                f"{entries[entries.index(e) - 1]['n']} (append order broken)"
            )

    # gaps (informational)
    nums = sorted(seen)
    missing = [n for n in range(nums[0], nums[-1] + 1) if n not in seen]
    if missing:
        warnings.append(f"gap in numbering: {', '.join(map(str, missing))}")

    for w in warnings:
        print(f"WARN  {w}")
    for err in errors:
        print(f"ERROR {err}")
    print(f"\n{len(entries)} lessons checked — {len(errors)} error(s), {len(warnings)} warning(s)")
    return 1 if errors else 0


def cmd_digest(args) -> int:
    path = Path(args.ledger)
    if not path.exists():
        print(f"digest: ledger not found: {path}", file=sys.stderr)
        return 2
    entries = parse(path.read_text(encoding="utf-8"))
    out = [
        "# LEDGER DIGEST",
        "",
        f"_{len(entries)} lessons · generated {datetime.date.today().isoformat()}_",
        "",
        "| # | Date | Title |",
        "|---|------|-------|",
    ]
    for e in entries:
        out.append(f"| {e['n']} | {e['date']} | {e['title']} |")
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
    text = path.read_text(encoding="utf-8")
    entries = parse(text)
    n = (max(e["n"] for e in entries) + 1) if entries else 1
    date = args.date or datetime.date.today().isoformat()

    # gather the four parts: flags if given, else prompt
    def get(flag_val, label):
        if flag_val:
            return flag_val
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
            print(f"add: aborted — '{label}' is empty (anti-noise gate: no empty change)", file=sys.stderr)
            return 1

    entry = [f"\n## Lesson {n} — {date} — {args.title}\n"]
    for label in PARTS:
        entry.append(f"- **{label}:** {parts[label]}")
    entry.append("")

    # append at end of file
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
    p.set_defaults(func=cmd_lint)

    p = sub.add_parser("digest", parents=[common], help="render one-screen index")
    p.add_argument("-o", "--output", help="write digest to file instead of stdout")
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
