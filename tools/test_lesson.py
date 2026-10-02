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


def run(*args):
    """Invoke lesson.py as the CLI (exit codes are the contract)."""
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


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

    def cli(self, *args):
        return run(*args, "--ledger", str(self.ledger))

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


if __name__ == "__main__":
    unittest.main()
