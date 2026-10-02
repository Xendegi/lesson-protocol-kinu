#!/usr/bin/env python3
"""
Unit tests for the ledger tooling — stdlib unittest, no dependencies.

Run:  python -m unittest discover -s tools -p "test_*.py" -v

Pinned behaviors (rulings, 2026-10-02):
  - anti-noise gate: empty parts never commit
  - snapshot-before-write: add aborts (exit 2, ledger untouched) on I/O failure
  - snapshot pair: ledger + sidecar recorded together in manifest.sha256
  - reference hygiene: broken and ambiguous bare refs are lint errors
  - ownership: flips ONLY via explicit `status` command; display is read-only;
    corrupt sidecar is refused, never rewritten
  - shape guard: ledger marker density blocked outside examples/ and fixtures
"""

import hashlib
import json
import locale
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TOOLS = REPO / "tools"
TOOL = TOOLS / "lesson.py"
GUARD = TOOLS / "check_anonymity.py"
SAMPLE = REPO / "examples" / "sample-ledger.md"

sys.path.insert(0, str(TOOLS))
import check_anonymity  # noqa: E402
import lesson           # noqa: E402


def _decode(raw: bytes) -> str:
    """Decode captured output the way the platform produced it.

    The CLI writes to pipes in the locale encoding (cp1256 on this machine,
    utf-8 on CI), so utf-8 first with a locale fallback keeps assertions on
    em-dashes platform-independent. Never raises.
    """
    for enc in ("utf-8", locale.getpreferredencoding(False) or "utf-8",
                "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def run(*args, stdin_text=""):
    """Invoke lesson.py as the CLI (exit codes are the contract).

    stdin defaults to an empty pipe: interactive prompts see an immediate
    EOF (the [y/N] guard declines) and can never inherit a live console.
    """
    p = subprocess.run(
        [sys.executable, str(TOOL), *args],
        capture_output=True,
        input=stdin_text.encode("utf-8") if stdin_text else b"",
    )
    return subprocess.CompletedProcess(
        p.args, p.returncode, _decode(p.stdout), _decode(p.stderr))


def entry(n, date="2026-01-01", title="Title", change="did something"):
    return (
        f"\n## Lesson {n} — {date} — {title}\n\n"
        f"- **His words:** w\n"
        f"- **The reveal:** r\n"
        f"- **The change:** {change}\n"
        f"- **The worth:** v\n"
    )


class TempLedger(unittest.TestCase):
    """Base: temp dir with the sample ledger as fixture."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.ledger = self.tmp / "ledger.md"
        self.ledger.write_text(SAMPLE.read_text(encoding="utf-8"),
                               encoding="utf-8")
        self.status_file = Path(str(self.ledger) + ".status.json")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def cli(self, *args, **kwargs):
        return run(*args, "--ledger", str(self.ledger), **kwargs)

    def text(self):
        return self.ledger.read_text(encoding="utf-8")


# --- parsing & ids -----------------------------------------------------------


class TestParseAndIds(TempLedger):

    def test_id_key_natural_order(self):
        self.assertLess(lesson.id_key("12"), lesson.id_key("12b"))
        self.assertLess(lesson.id_key("12b"), lesson.id_key("13"))
        self.assertEqual(lesson.id_key("7"), (7, ""))

    def test_parse_handles_suffix_ids(self):
        entries = lesson.parse(
            entry("39") + entry("40b", date="2026-02-02"))
        self.assertEqual([e["id"] for e in entries], ["39", "40b"])
        self.assertEqual(entries[1]["date"], "2026-02-02")


# --- lint --------------------------------------------------------------------


class TestLint(TempLedger):

    def test_duplicate_ids_are_errors(self):
        self.ledger.write_text(self.text() + entry("1"), encoding="utf-8")
        r = self.cli("lint")
        self.assertEqual(r.returncode, 1)
        self.assertIn("DUPLICATE lesson id 1", r.stdout)

    def test_order_is_advisory_and_strict_promotes(self):
        shuffled = entry("13") + entry("12") + entry("14")
        self.ledger.write_text(shuffled, encoding="utf-8")
        r = self.cli("lint")
        self.assertEqual(r.returncode, 0)          # advisory by default
        self.assertIn("WARN", r.stdout)
        r = self.cli("lint", "--strict")
        self.assertEqual(r.returncode, 1)          # strict fails

    def test_refs_broken_and_ambiguous_are_errors(self):
        body = entry("1") + entry("12") + entry("12b")
        body = body.replace(
            "- **The worth:** v",
            "- **The worth:** v — see Lesson 12 and Lesson 99", 1)
        self.ledger.write_text(body, encoding="utf-8")
        r = self.cli("lint", "--refs")
        self.assertEqual(r.returncode, 1)
        self.assertIn("broken reference", r.stdout)
        self.assertIn("Lesson 99 does not exist", r.stdout)
        self.assertIn("ambiguous bare reference", r.stdout)
        # without --refs the reference checks stay out of the way
        self.assertEqual(self.cli("lint").returncode, 0)

    def test_sample_ledger_green(self):
        r = run("lint", "--refs", "--ledger", str(SAMPLE))
        self.assertEqual(r.returncode, 0, r.stdout)


# --- add: gate, snapshot-before-write, numbering -----------------------------


class TestAddGate(TempLedger):

    def test_empty_change_rejected_and_ledger_untouched(self):
        before = self.text()
        r = self.cli("add", "--title", "T", "--words", "w",
                     "--reveal", "r", "--change", "", "--worth", "v")
        self.assertEqual(r.returncode, 1)
        self.assertIn("anti-noise gate", r.stderr)
        self.assertEqual(self.text(), before)

    def test_add_snapshots_before_write_and_creates_no_status(self):
        before = self.text()
        before_bytes = self.ledger.read_bytes()   # raw bytes: CRLF, BOM, ...
        r = self.cli("add", "--title", "Next", "--words", "w",
                     "--reveal", "r", "--change", "c", "--worth", "v")
        self.assertEqual(r.returncode, 0, r.stderr)
        backups = self.tmp / "backups"
        copies = list(backups.glob("*.md"))
        self.assertEqual(len(copies), 1)
        self.assertEqual(copies[0].read_bytes(),
                         before_bytes)            # snapshot = pre-write state
        self.assertIn("## Lesson 5 — ", self.text())
        # no automatic ownership state
        self.assertFalse(self.status_file.exists())

    def test_next_number_after_suffix(self):
        self.ledger.write_text(entry("12") + entry("12b"), encoding="utf-8")
        r = self.cli("add", "--title", "T", "--words", "w",
                     "--reveal", "r", "--change", "c", "--worth", "v")
        self.assertEqual(r.returncode, 0)
        self.assertIn("## Lesson 13 — ", self.text())

    def test_snapshot_io_error_aborts_add(self):
        before = self.text()
        (self.tmp / "backups").write_bytes(b"file where a directory goes")
        r = self.cli("add", "--title", "T", "--words", "w",
                     "--reveal", "r", "--change", "c", "--worth", "v")
        self.assertEqual(r.returncode, 2)
        self.assertIn("snapshot failed", r.stderr)
        self.assertEqual(self.text(), before)      # nothing partial, ever


# --- snapshot pair + manifest ------------------------------------------------


class TestSnapshotManifest(TempLedger):

    def make_sidecar(self):
        self.status_file.write_text(
            json.dumps({"1": {"status": "credited", "changed": "2026-10-02"}}),
            encoding="utf-8")

    def test_pair_recorded_in_manifest(self):
        self.make_sidecar()
        r = self.cli("snapshot")
        self.assertEqual(r.returncode, 0, r.stderr)
        backups = self.tmp / "backups"
        mds = list(backups.glob("*.md"))
        scs = list(backups.glob("*.status.json"))
        self.assertEqual(len(mds), 1)
        self.assertEqual(len(scs), 1)
        lines = [l for l in (backups / "manifest.sha256")
                 .read_text(encoding="utf-8").splitlines() if l.strip()]
        self.assertEqual(len(lines), 2)            # both files, one pair
        names = {l.partition("  ")[2] for l in lines}
        self.assertEqual(names, {mds[0].name, scs[0].name})

    def test_manifest_hashes_are_real_sha256(self):
        self.make_sidecar()
        self.cli("snapshot")
        backups = self.tmp / "backups"
        manifest = backups / "manifest.sha256"
        for line in manifest.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            digest, _, name = line.partition("  ")
            actual = hashlib.sha256((backups / name).read_bytes()).hexdigest()
            self.assertEqual(digest, actual)

    def test_verify_ok_then_tamper_detected(self):
        self.make_sidecar()
        self.cli("snapshot")
        r = self.cli("snapshot", "--verify")
        self.assertEqual(r.returncode, 0, r.stdout)
        victim = next((self.tmp / "backups").glob("*.md"))
        victim.write_bytes(victim.read_bytes() + b"tampered")
        r = self.cli("snapshot", "--verify")
        self.assertEqual(r.returncode, 1)
        self.assertIn("HASH MISMATCH", r.stdout)

    def test_verify_missing_file_detected(self):
        self.cli("snapshot")
        victim = next((self.tmp / "backups").glob("*.md"))
        victim.unlink()
        r = self.cli("snapshot", "--verify")
        self.assertEqual(r.returncode, 1)
        self.assertIn("missing snapshot file", r.stdout)


# --- ownership rulings -------------------------------------------------------


class TestStatusRuling(TempLedger):

    def test_display_is_readonly(self):
        r = self.cli("status", "1")
        self.assertEqual(r.returncode, 0)
        self.assertIn("recorded", r.stdout)
        self.assertFalse(self.status_file.exists())

    def test_flip_requires_explicit_command_only(self):
        # nothing else in the toolchain may create or change a state
        self.cli("digest")
        self.cli("add", "--title", "T", "--words", "w",
                 "--reveal", "r", "--change", "c", "--worth", "v")
        self.assertFalse(self.status_file.exists())
        r = self.cli("status", "1", "credited")   # only this may flip
        self.assertEqual(r.returncode, 0)
        data = json.loads(self.status_file.read_text(encoding="utf-8-sig"))
        self.assertEqual(data["1"]["status"], "credited")
        # a later add leaves the state alone
        self.cli("add", "--title", "T2", "--words", "w",
                 "--reveal", "r", "--change", "c", "--worth", "v")
        data = json.loads(self.status_file.read_text(encoding="utf-8-sig"))
        self.assertEqual(data["1"]["status"], "credited")

    def test_unknown_id_is_error(self):
        self.assertEqual(self.cli("status", "99", "credited").returncode, 1)
        self.assertFalse(self.status_file.exists())

    def test_invalid_state_is_error(self):
        r = self.cli("status", "1", "banana")
        self.assertEqual(r.returncode, 2)
        self.assertFalse(self.status_file.exists())

    def test_corrupt_sidecar_is_refused_not_rewritten(self):
        self.status_file.write_text("{broken", encoding="utf-8")
        before = self.status_file.read_bytes()
        r = self.cli("status", "1", "credited")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(self.status_file.read_bytes(), before)  # untouched


# --- read side ---------------------------------------------------------------


class TestDigestRecall(TempLedger):

    def test_digest_sort_is_logical_on_read_side(self):
        self.ledger.write_text(
            entry("13") + entry("12b") + entry("12"), encoding="utf-8")
        r = self.cli("digest", "--sort")
        self.assertEqual(r.returncode, 0)
        self.assertLess(r.stdout.index(" 12 |"), r.stdout.index(" 12b |"))
        self.assertLess(r.stdout.index(" 12b |"), r.stdout.index(" 13 |"))

    def test_recall_case_insensitive_hit_and_miss(self):
        hit = self.cli("recall", "ANSWER THE QUESTION")
        self.assertEqual(hit.returncode, 0)
        self.assertIn("Lesson 1", hit.stdout)
        miss = self.cli("recall", "zzz-no-such-thing")
        self.assertEqual(miss.returncode, 1)


# --- structural shape guard --------------------------------------------------


class TestShapeGuard(unittest.TestCase):

    LEDGER_TEXT = "".join(
        f"- {m} sample\n" for m in check_anonymity.LEDGER_MARKERS * 4)

    def test_blocks_ledger_shape_at_root(self):
        msg = check_anonymity.shape_violation("notes.md", self.LEDGER_TEXT)
        self.assertIsNotNone(msg)
        self.assertIn("ledger-shaped", msg)

    def test_boundary_three_allowed(self):
        text = "".join(f"- {m} x\n" for m in check_anonymity.LEDGER_MARKERS * 3)
        self.assertIsNone(check_anonymity.shape_violation("notes.md", text))

    def test_allows_exempt_locations(self):
        for rel in ("examples/ledger.md", "fixtures/ledger.md",
                    "LESSON_TEMPLATE.md", "tools/test_x.py"):
            self.assertIsNone(
                check_anonymity.shape_violation(rel, self.LEDGER_TEXT),
                rel)

    def test_name_tokens_and_shape_are_independent(self):
        # shape fires even when no identity token is present
        clean = self.LEDGER_TEXT  # no tokens inside
        self.assertFalse(any(rx.search(clean)
                             for rx, _ in check_anonymity.TOKENS))
        self.assertIsNotNone(
            check_anonymity.shape_violation("notes.md", clean))


# --- token patterns & live repo ---------------------------------------------


class TestScanner(unittest.TestCase):

    def test_tokens_match_identity_probes(self):
        probes = ["L" + "ux", "Am" + "ir", "St" + "aid",
                  "g" + ":" + "\\" + "ai", "athen" + "amiro"]
        for probe in probes:
            self.assertTrue(any(rx.search(probe)
                                for rx, _ in check_anonymity.TOKENS), probe)

    def test_false_positives_stay_clean(self):
        for probe in ("Deluxe packaging", "the harness of the cart"):
            self.assertFalse(any(rx.search(probe)
                                 for rx, _ in check_anonymity.TOKENS), probe)

    def test_live_repo_passes_the_guard(self):
        r = subprocess.run([sys.executable, str(GUARD)], capture_output=True,
                            text=True, encoding="utf-8", errors="replace")
        self.assertEqual(r.returncode, 0,
                         r.stdout + r.stderr)


# --- restore (guarded) -------------------------------------------------------


class TestRestore(TempLedger):
    """Rulings (2026-10-03): jail, manifest integrity, [y/N] confirmation,
    snapshot-first, atomic pair swap — in that order."""

    def backups(self):
        return self.tmp / "backups"

    def snapshot_pair(self):
        """Sidecar + snapshot → backups/ with a two-entry manifest."""
        self.status_file.write_text(
            json.dumps({"1": {"status": "credited", "changed": "2026-10-02"}}),
            encoding="utf-8")
        r = self.cli("snapshot")
        self.assertEqual(r.returncode, 0, r.stderr)
        return next(self.backups().glob("*.md"))

    def names(self):
        return sorted(f.name for f in self.backups().iterdir())

    def test_jail_rejects_paths_outside_backups(self):
        for bad in ("../ledger.md", str(self.ledger), "sub/x.md"):
            r = self.cli("restore", bad, "--yes")
            self.assertEqual(r.returncode, 1, bad)
            self.assertIn("refusing", r.stderr)
        self.assertFalse(self.status_file.exists())   # nothing touched

    def test_missing_and_unlisted_targets_refused(self):
        r = self.cli("restore", "nope.md", "--yes")
        self.assertEqual(r.returncode, 1)
        self.assertIn("not found", r.stderr)
        self.snapshot_pair()                      # manifest now exists
        (self.backups() / "rogue.md").write_text("not in any manifest",
                                                 encoding="utf-8")
        r = self.cli("restore", "rogue.md", "--yes")
        self.assertEqual(r.returncode, 1)
        self.assertIn("no manifest entry", r.stderr)
        self.assertEqual(self.text(),
                         SAMPLE.read_text(encoding="utf-8"))  # ledger pristine

    def test_hash_mismatch_aborts_before_any_write(self):
        md = self.snapshot_pair()
        md.write_bytes(md.read_bytes() + b"tampered")
        names_before = self.names()
        before = self.ledger.read_bytes()
        r = self.cli("restore", md.name, "--yes")
        self.assertEqual(r.returncode, 1)
        self.assertIn("HASH MISMATCH", r.stderr)
        self.assertEqual(self.ledger.read_bytes(), before)
        # integrity runs BEFORE snapshot-first: no backup was created
        self.assertEqual(self.names(), names_before)

    def test_missing_manifest_refused(self):
        md = self.snapshot_pair()
        (self.backups() / "manifest.sha256").unlink()
        before = self.ledger.read_bytes()
        r = self.cli("restore", md.name, "--yes")
        self.assertEqual(r.returncode, 1)
        self.assertIn("cannot verify", r.stderr)
        self.assertEqual(self.ledger.read_bytes(), before)

    def test_confirmation_decline_changes_nothing(self):
        md = self.snapshot_pair()
        self.ledger.write_text(self.text() + entry("9"), encoding="utf-8")
        dirty = self.ledger.read_bytes()
        names_before = self.names()
        r = self.cli("restore", md.name)             # EOF → declines
        self.assertEqual(r.returncode, 1)
        self.assertIn("cancelled", r.stderr)
        r = self.cli("restore", md.name, stdin_text="n\n")   # explicit no
        self.assertEqual(r.returncode, 1)
        # neither attempt wrote anything — no snapshot, no swap
        self.assertEqual(self.ledger.read_bytes(), dirty)
        self.assertEqual(self.names(), names_before)

    def test_prompt_accept_restores(self):
        md = self.snapshot_pair()
        self.ledger.write_text(self.text() + entry("9"), encoding="utf-8")
        r = self.cli("restore", md.name, stdin_text="y\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.ledger.read_bytes(), md.read_bytes())
        self.assertIn("pair swapped", r.stdout)

    def test_restore_snapshots_current_pair_first(self):
        md = self.snapshot_pair()
        self.ledger.write_text(self.text() + entry("9"), encoding="utf-8")
        self.status_file.write_text(
            json.dumps({"9": {"status": "recorded", "changed": "2026-10-03"}}),
            encoding="utf-8")
        dirty_md = self.ledger.read_bytes()
        dirty_sc = self.status_file.read_bytes()
        manifest = self.backups() / "manifest.sha256"

        def line_count():
            return len([l for l in manifest.read_text(encoding="utf-8")
                        .splitlines() if l.strip()])

        lines_before = line_count()
        r = self.cli("restore", md.name, "--yes")
        self.assertEqual(r.returncode, 0, r.stderr)
        # atomic pair swap: both working files equal the snapshot bytes
        self.assertEqual(self.ledger.read_bytes(), md.read_bytes())
        sc_snap = self.backups() / f"{md.stem}.status.json"
        self.assertTrue(sc_snap.exists())
        self.assertEqual(self.status_file.read_bytes(), sc_snap.read_bytes())
        # snapshot-first: the dirty pair was preserved before the swap
        self.assertIn(dirty_md,
                      [f.read_bytes() for f in self.backups().glob("*.md")])
        self.assertIn(
            dirty_sc,
            [f.read_bytes() for f in self.backups().glob("*.status.json")])
        # manifest grew by exactly the pre-restore pair
        self.assertEqual(line_count(), lines_before + 2)

    def test_snapshot_without_sidecar_leaves_working_sidecar(self):
        r = self.cli("snapshot")                    # ledger only — no sidecar
        self.assertEqual(r.returncode, 0, r.stderr)
        md = next(self.backups().glob("*.md"))
        # the sidecar appears only AFTER the snapshot
        self.status_file.write_text(
            json.dumps({"1": {"status": "credited", "changed": "2026-10-02"}}),
            encoding="utf-8")
        sc_before = self.status_file.read_bytes()
        r = self.cli("restore", md.name, "--yes")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("working sidecar untouched", r.stdout)
        self.assertEqual(self.status_file.read_bytes(), sc_before)


# --- recall ranking ----------------------------------------------------------


class TestRecallRanking(TempLedger):

    PROSE_ENTRY = (
        "\n## Lesson 7 — 2026-01-01 — Plain\n\n"
        "preamble prose mentioning quorum before the shape begins\n"
        "- **His words:** w\n"
        "- **The reveal:** r\n"
        "- **The change:** c\n"
        "- **The worth:** v\n"
    )

    def test_tiers_title_then_field_then_prose(self):
        self.ledger.write_text(
            entry("5", title="the quorum was reached")
            + entry("6", change="kept the quorum waiting")
            + self.PROSE_ENTRY, encoding="utf-8")
        r = self.cli("recall", "quorum")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout
        self.assertLess(out.index("## Lesson 5 —"),
                        out.index("## Lesson 6 —"))   # title > field
        self.assertLess(out.index("## Lesson 6 —"),
                        out.index("## Lesson 7 —"))   # field > prose

    def test_exact_id_outranks_title(self):
        self.ledger.write_text(
            entry("12") + entry("13", title="retry 12 times"),
            encoding="utf-8")
        r = self.cli("recall", "12")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout
        self.assertLess(out.index("## Lesson 12 —"),
                        out.index("## Lesson 13 —"))  # exact id > title

    def test_ties_broken_most_recent_first(self):
        self.ledger.write_text(
            entry("5", change="ember glowed")
            + entry("12", change="ember dimmed")
            + entry("12b", change="ember faded"), encoding="utf-8")
        r = self.cli("recall", "ember")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout
        # all tied (field tier) → id_key descending: 12b, 12, 5
        self.assertLess(out.index("## Lesson 12b —"),
                        out.index("## Lesson 12 —"))
        self.assertLess(out.index("## Lesson 12 —"),
                        out.index("## Lesson 5 —"))


if __name__ == "__main__":
    unittest.main()
