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
import hashlib
import json
import os
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


# --- status (ownership) ------------------------------------------------------
# Ruling (2026-10-02): NO automatic promotion. Credit and verification are
# granted, never assumed. Only an explicit `lesson.py status <id> <state>` by
# the human applies a flip; the agent may suggest, never set.

STATES = ("recorded", "credited", "promoted", "proven")


def status_file(path: Path) -> Path:
    """Sidecar: status is metadata, the ledger text stays append-only."""
    return Path(str(path) + ".status.json")


def load_status(path: Path, strict: bool) -> dict:
    sf = status_file(path)
    if not sf.exists():
        return {}
    try:
        data = json.loads(sf.read_text(encoding=ENCODING))
    except (json.JSONDecodeError, OSError) as exc:
        if strict:
            print(f"status: sidecar unreadable ({exc}) — refusing to touch it",
                  file=sys.stderr)
            sys.exit(2)
        print(f"warning: status sidecar unreadable ({exc}); showing none",
              file=sys.stderr)
        return {}
    if not isinstance(data, dict):
        if strict:
            print("status: sidecar malformed — refusing to touch it", file=sys.stderr)
            sys.exit(2)
        return {}
    return data


def cmd_status(args) -> int:
    path = Path(args.ledger)
    if not path.exists():
        print(f"status: ledger not found: {path}", file=sys.stderr)
        return 2
    entries = parse(path.read_text(encoding=ENCODING))
    ids = {e["id"] for e in entries}
    if args.id not in ids:
        print(f"status: unknown lesson id '{args.id}'", file=sys.stderr)
        return 1

    data = load_status(path, strict=True)
    current = data.get(args.id, {}).get("status", "recorded")

    # display mode — read-only, never creates or writes the sidecar
    if args.state is None:
        meta = data.get(args.id, {})
        extra = f" (since {meta['changed']})" if meta.get("changed") else ""
        print(f"Lesson {args.id}: {current}{extra}")
        return 0

    if args.state == current:
        print(f"Lesson {args.id}: already '{current}' — no change")
        return 0

    data[args.id] = {
        "status": args.state,
        "changed": datetime.date.today().isoformat(),
        "previous": current,
    }
    sf = status_file(path)
    tmp = sf.with_name(sf.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")  # reads tolerate BOM; writes never add one
    os.replace(tmp, sf)
    print(f"Lesson {args.id}: {current} -> {args.state} "
          f"({datetime.date.today().isoformat()}, by explicit command)")
    return 0


# --- commands ----------------------------------------------------------------


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def snapshot_copy(path: Path) -> Path:
    """Atomic dated copy of the ledger — plus its status sidecar, if any —
    into a sibling backups/ directory, recorded in manifest.sha256.

    Preservation Doctrine as code: siblings, never overwrites. Same-day
    collisions get a timestamp (then a counter), so nothing is ever replaced.
    Each file is written via temp + atomic rename; the manifest append comes
    last and records the pair as a unit — a crash mid-pair leaves an orphan
    that `snapshot --verify` reports instead of a silent half-backup.
    """
    if not path.exists():
        raise FileNotFoundError(path)
    backups = path.parent / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    now = datetime.datetime.now()
    stem = path.stem
    base = f"{stem}.{now.strftime('%Y-%m-%d')}"
    target = backups / f"{base}.md"
    if target.exists():
        base = f"{stem}.{now.strftime('%Y-%m-%d.%H%M%S')}"
        target = backups / f"{base}.md"
        i = 1
        while target.exists():
            target = backups / f"{base}.{i}.md"
            i += 1

    manifest_lines = []

    data = path.read_bytes()          # exact bytes — no re-encoding
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_bytes(data)             # write beside target, then atomic swap
    os.replace(tmp, target)
    manifest_lines.append(f"{_sha256(data)}  {target.name}")

    sc = status_file(path)            # the pair: ownership sidecar, if present
    if sc.exists():
        sc_data = sc.read_bytes()
        sc_target = backups / f"{target.stem}.status.json"
        tmp = sc_target.with_name(sc_target.name + ".tmp")
        tmp.write_bytes(sc_data)
        os.replace(tmp, sc_target)
        manifest_lines.append(f"{_sha256(sc_data)}  {sc_target.name}")

    manifest = backups / "manifest.sha256"
    with manifest.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(manifest_lines) + "\n")
    return target


def verify_manifest(path: Path) -> int:
    """Check every manifest entry: file exists and hash matches. Orphans warn."""
    backups = path.parent / "backups"
    manifest = backups / "manifest.sha256"
    if not manifest.exists():
        print("verify: no manifest yet (no snapshots since manifest support)")
        return 0
    errors, warnings, listed = [], [], set()
    for line in manifest.read_text(encoding=ENCODING).splitlines():
        if not line.strip():
            continue
        digest, sep, name = line.partition("  ")
        if not sep:
            errors.append(f"manifest line malformed: {line!r}")
            continue
        listed.add(name)
        f = backups / name
        if not f.exists():
            errors.append(f"missing snapshot file: {name}")
        elif _sha256(f.read_bytes()) != digest:
            errors.append(f"HASH MISMATCH (corrupted or altered): {name}")
    for f in sorted(backups.iterdir()):
        if f.name == "manifest.sha256" or f.name.endswith(".tmp"):
            continue
        if f.name not in listed:
            warnings.append(f"unlisted file (orphan or failed pair): {f.name}")
    for w in warnings:
        print(f"WARN  {w}")
    for e in errors:
        print(f"ERROR {e}")
    print(f"verify: {len(listed)} entr(ies) checked — "
          f"{len(errors)} error(s), {len(warnings)} warning(s)")
    return 1 if errors else 0


# recall ranking (deterministic): exact id > title > four-part field > prose
S_ID, S_TITLE, S_FIELD, S_PROSE = 400, 300, 200, 100


def match_score(q: str, e: dict) -> int:
    """Rank tier of one entry against a casefolded query; 0 = no match.

    Ties are broken by id_key descending (most recent lesson first) at sort
    time — this stays a pure function so each tier is testable on its own.
    """
    if not q:
        return 0
    if q == e["id"].casefold():
        return S_ID
    best = 0
    if q in e["title"].casefold():
        best = S_TITLE
    folded = e["body"].casefold()
    positions = [m.start() for m in re.finditer(re.escape(q), folded)]
    if positions:
        # a hit at/after the first field marker lives inside the four-part
        # shape; anything before it (or a markerless body) is general prose
        first_marker = folded.find("- **")
        in_field = first_marker != -1 and max(positions) >= first_marker
        best = max(best, S_FIELD if in_field else S_PROSE)
    if q in e["id"].casefold():
        best = max(best, S_PROSE)          # partial id ("12" inside "12b")
    if q in e["date"].casefold():
        best = max(best, S_PROSE)
    return best


def cmd_recall(args) -> int:
    """Ranked search: exact id > title > four-part field > prose, ties newest."""
    path = Path(args.ledger)
    if not path.exists():
        print(f"recall: ledger not found: {path}", file=sys.stderr)
        return 2
    entries = parse(path.read_text(encoding=ENCODING))
    q = args.query.casefold()
    hits = [t for t in ((match_score(q, e), e) for e in entries) if t[0] > 0]
    if not hits:
        print(f"no lessons match {args.query!r}", file=sys.stderr)
        return 1
    # deterministic: most recent first within equal scores (two stable
    # passes — id desc, then score desc — keep id order inside every tie)
    hits.sort(key=lambda t: id_key(t[1]["id"]), reverse=True)
    hits.sort(key=lambda t: t[0], reverse=True)
    for _, e in hits:
        print(f"## Lesson {e['id']} — {e['date']} — {e['title']}")
        print(e["body"].strip())
        print()
    print(f"{len(hits)} of {len(entries)} lessons match {args.query!r}")
    return 0


def cmd_snapshot(args) -> int:
    path = Path(args.ledger)
    if not path.exists():
        print(f"snapshot: ledger not found: {path}", file=sys.stderr)
        return 2
    if args.verify:
        return verify_manifest(path)
    try:
        target = snapshot_copy(path)
    except Exception as exc:  # noqa: BLE001 — report, never traceback
        print(f"snapshot: failed ({exc}); nothing written", file=sys.stderr)
        return 2
    size = target.stat().st_size
    sc_target = path.parent / "backups" / f"{target.stem}.status.json"
    pair = f" + {sc_target.name}" if sc_target.exists() else ""
    print(f"snapshot → {target} ({size} bytes){pair}; manifest updated")
    return 0


def cmd_restore(args) -> int:
    """Restore a snapshot pair over the live ledger — jailed, verified,
    confirmed, and snapshot-first. Guardrails (ruling, 2026-10-03):

    1. Jail: the target must be a plain file name resolving inside backups/.
    2. Integrity: every file to be written must be listed in manifest.sha256
       and hash-match it; otherwise abort (no manifest = no trust).
    3. Confirmation: interactive [y/N], or --yes for non-interactive use.
    4. Snapshot-first: the current working pair is snapshotted before the
       restore touches either working file.
    5. Atomic pair swap: both targets staged via temp files, then swapped
       with os.replace; the ledger lands last. Re-verified after landing.
    """
    path = Path(args.ledger)
    if not path.exists():
        print(f"restore: ledger not found: {path}", file=sys.stderr)
        return 2
    backups = path.parent / "backups"

    # 1) jail — plain name only; resolves strictly inside backups/
    target = Path(args.target)
    if target.is_absolute() or len(target.parts) != 1 or ".." in target.parts:
        print(f"restore: refusing target — must be a plain snapshot name "
              f"inside backups/ ({args.target!r})", file=sys.stderr)
        return 1
    snap = backups / target.name
    if snap.resolve().parent != backups.resolve():
        print(f"restore: refusing target — must be a plain snapshot name "
              f"inside backups/ ({args.target!r})", file=sys.stderr)
        return 1
    if not snap.is_file():
        print(f"restore: snapshot not found: {target.name}", file=sys.stderr)
        return 1

    # 2) integrity — manifest must list and match every file we would write
    manifest = backups / "manifest.sha256"
    if not manifest.exists():
        print("restore: manifest.sha256 missing — cannot verify; "
              "aborting", file=sys.stderr)
        return 1
    entries = {}
    for line in manifest.read_text(encoding=ENCODING).splitlines():
        if line.strip():
            digest, sep, name = line.partition("  ")
            if sep:
                entries[name] = digest
    if target.name not in entries:
        print(f"restore: no manifest entry for {target.name!r} — "
              f"unverifiable, aborting", file=sys.stderr)
        return 1
    if _sha256(snap.read_bytes()) != entries[target.name]:
        print(f"restore: HASH MISMATCH for {target.name!r} — "
              f"does not match manifest, aborting", file=sys.stderr)
        return 1
    # the pair's sidecar, if the snapshot carries one — same strictness
    sc_name = f"{snap.stem}.status.json"
    sc_snap = backups / sc_name
    pair_note = " (no sidecar in snapshot — working sidecar untouched)"
    if sc_snap.exists():
        if sc_name not in entries:
            print(f"restore: sidecar {sc_name!r} has no manifest entry — "
                  f"unverifiable, aborting", file=sys.stderr)
            return 1
        if _sha256(sc_snap.read_bytes()) != entries[sc_name]:
            print(f"restore: HASH MISMATCH for {sc_name!r} — "
                  f"does not match manifest, aborting", file=sys.stderr)
            return 1
        pair_note = " + sidecar (pair swapped)"

    # 3) confirmation guard — [y/N] unless --yes
    if not args.yes:
        try:
            answer = input(f"restore {target.name} over {path.name}? [y/N] ")
        except (EOFError, KeyboardInterrupt):
            print(file=sys.stderr)
            answer = ""
        if answer.strip().casefold() not in ("y", "yes"):
            print("restore: cancelled — nothing changed", file=sys.stderr)
            return 1

    # 4) snapshot-first — current working pair preserved before any write
    try:
        pre = snapshot_copy(path)
    except Exception as exc:  # noqa: BLE001 — any failure must block the write
        print(f"restore: pre-restore snapshot failed ({exc}); aborting",
              file=sys.stderr)
        return 2

    # 5) atomic pair swap — stage both, then swap; ledger lands last
    md_tmp = path.with_name(path.name + ".restore-tmp")
    md_tmp.write_bytes(snap.read_bytes())
    sc_working = status_file(path)
    sc_tmp = None
    if sc_snap.exists():
        sc_tmp = sc_working.with_name(sc_working.name + ".restore-tmp")
        sc_tmp.write_bytes(sc_snap.read_bytes())
    if sc_tmp is not None:
        os.replace(sc_tmp, sc_working)   # metadata first ...
    os.replace(md_tmp, path)             # ... ledger last = commit moment

    # 6) post-restore verification — what landed is what the manifest names
    if _sha256(path.read_bytes()) != entries[target.name]:
        print("restore: post-restore verification FAILED — pre-restore copy "
              f"holds at {pre.name}", file=sys.stderr)
        return 2
    print(f"restore: {target.name} → {path} —{pair_note} verified against "
          f"manifest (pre-restore copy: {pre.name})")
    return 0


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

    # reference hygiene (--refs): prose citations must exist and be unambiguous
    if args.refs:
        range_re = re.compile(r"Lessons\s+(\d+)\s*[–—-]\s*(\d+)", re.I)
        ref_re = re.compile(r"\bLessons?\s+(\d+[a-z]?)", re.I)
        lref_re = re.compile(r"(?<![A-Za-z0-9_-])L(\d+[a-z]?)\b")

        id_set = {e["id"] for e in entries}
        suffix_map = {}  # numeric part -> set of suffix letters in use
        for i in id_set:
            m = ID_RE.match(i)
            if m and m.group(2):
                suffix_map.setdefault(int(m.group(1)), set()).add(m.group(2))

        def check_ref(entry, ref, source):
            where = f"lesson {entry['id']} (line {entry['line']})"
            if ref not in id_set:
                errors.append(
                    f"{where}: broken reference '{source.strip()}' — "
                    f"Lesson {ref} does not exist"
                )
                return
            m = ID_RE.match(ref)
            if m and not m.group(2) and suffix_map.get(int(m.group(1))):
                variants = "".join(sorted(suffix_map[int(m.group(1))]))
                errors.append(
                    f"{where}: ambiguous bare reference '{source.strip()}' — "
                    f"variant(s) {ref}{variants} exist; qualify the exact id"
                )

        for e in entries:
            body = e["body"]
            for rm in range_re.finditer(body):
                a, b = int(rm.group(1)), int(rm.group(2))
                if a > b:
                    a, b = b, a
                for num in range(a, b + 1):
                    check_ref(e, str(num), rm.group(0))
            masked = range_re.sub(" ", body)
            for rx in (ref_re, lref_re):
                for rm in rx.finditer(masked):
                    check_ref(e, rm.group(1), rm.group(0))

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
    st = load_status(path, strict=False)  # display-only: warn, don't die
    if args.sort:
        # chronological/logical order on the read side — file order untouched
        entries.sort(key=lambda e: (e["date"].split("/")[0], id_key(e["id"])))
    out = [
        "# LEDGER DIGEST",
        "",
        f"_{len(entries)} lessons · generated {datetime.date.today().isoformat()}"
        + (" · sorted" if args.sort else " · file order") + "_",
        "",
        "| # | Date | Title | Status |",
        "|---|------|-------|--------|",
    ]
    for e in entries:
        status = st.get(e["id"], {}).get("status", "recorded")
        out.append(f"| {e['id']} | {e['date']} | {e['title']} | {status} |")
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

    # safety hook: snapshot BEFORE any write — if it fails, the ledger stays
    # untouched (Preservation Doctrine: archive before every new state)
    try:
        snap = snapshot_copy(path)
    except Exception as exc:  # noqa: BLE001 — any failure must block the write
        print(f"add: aborted — snapshot failed ({exc}); ledger untouched", file=sys.stderr)
        return 2
    print(f"snapshot → {snap}")

    entry = [f"\n## Lesson {n} — {date} — {args.title}\n"]
    for label in PARTS:
        entry.append(f"- **{label}:** {parts[label]}")
    entry.append("")

    with path.open("a", encoding="utf-8") as f:
        f.write("\n".join(entry))
    print(f"committed: Lesson {n} — {args.title}")
    return 0


def main():
    # never crash on a console that cannot encode our output (cp1256 etc.);
    # unencodable chars degrade to '?' instead of raising mid-command
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(errors="replace")
            except Exception:  # noqa: BLE001 — stream config is best-effort
                pass

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
    p.add_argument(
        "--refs",
        action="store_true",
        help="check prose references: broken ids and ambiguous bare refs are errors",
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

    p = sub.add_parser("recall", parents=[common],
                       help="case-insensitive search: ids, titles, four-part fields")
    p.add_argument("query", help="search string (case-insensitive)")
    p.set_defaults(func=cmd_recall)

    p = sub.add_parser("snapshot", parents=[common],
                       help="atomic dated copy into sibling backups/")
    p.add_argument("--verify", action="store_true",
                   help="check manifest.sha256: hashes match, files present")
    p.set_defaults(func=cmd_snapshot)

    p = sub.add_parser("restore", parents=[common],
                       help="restore a snapshot pair over the live ledger (guarded)")
    p.add_argument("target", help="snapshot file name inside backups/ "
                                  "(plain name — no paths)")
    p.add_argument("--yes", action="store_true",
                   help="skip the interactive [y/N] confirmation")
    p.set_defaults(func=cmd_restore)

    p = sub.add_parser("status", parents=[common],
                       help="show or set ownership status (explicit command only)")
    p.add_argument("id", help="lesson id (e.g. 12, 12b)")
    p.add_argument("state", nargs="?", choices=STATES, default=None,
                   help="new state — omitted: display current (read-only)")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("add", parents=[common], help="append a lesson (auto-numbered)")
    p.add_argument("--title", required=True)
    p.add_argument("--date", help="YYYY-MM-DD (default: today)")
    p.add_argument("--words", help="His words, verbatim")
    p.add_argument("--reveal", help="The reveal")
    p.add_argument("--change", help="The change")
    p.add_argument("--worth", help="The worth")
    p.set_defaults(func=cmd_add)

    args = ap.parse_args()
    try:
        code = args.func(args)
        sys.stdout.flush()          # flush while we can still handle a closed pipe
        sys.exit(code)
    except BrokenPipeError:
        # consumer closed the pipe early (e.g. `| head`): die quietly, not loudly
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except OSError:
            pass
        sys.exit(0)


if __name__ == "__main__":
    main()
