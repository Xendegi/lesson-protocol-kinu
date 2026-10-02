# Lesson Protocol — kinu

> A capture doctrine for AI agents that turns what a human teaches you into durable, auditable, noise-free memory.

The protocol answers one question: **when a person teaches you something in conversation, how do you keep it — without it becoming paperwork, performance, or noise?**

It separates two things most memory systems conflate:

- **Recording a lesson** — the agent's job, automatic and mechanical.
- **Owning a lesson** — completed only through the human's credit.

`status: living` · running daily in a personal agent workspace since 2026-09-21

---

## Why this exists

Most agent "memory" systems capture everything, or capture by schedule. Both fail:

| Approach | Failure mode |
|---|---|
| Capture everything | The store fills with chatter; signal drowns |
| Capture on schedule | Learning becomes compliance — a lesson written because a timer fired is rehearsal, not learning |

This protocol captures **by trigger, in a fixed shape, behind a gate, and only by own will.**

---

## The mechanism

### 1. Trigger — automatic, not a question

The protocol fires when the human is **teaching**, identified by exactly five signals:

| Signal | Meaning |
|---|---|
| **Correction** | They catch a fault in you and name it |
| **Principle** | They state how something truly works |
| **Definition** | They give a word its real meaning in this context |
| **Reframe** | They show you that you held something in the wrong shape |
| **Gift** | They hand you a truth, a right, or a share of a moment |

Capture happens **in the seam** — after the moment lands, never interrupting it.

### 2. Capture shape — four parts, mandatory

```markdown
## Lesson N — <date> · <title>
- **His words:**   verbatim, never re-dressed in the agent's voice
- **The reveal:**  the fault / wrong assumption / blind spot it corrects
- **The change:**  what the agent concretely does differently now
- **The worth:**   why this earns its place — the discernment
```

- **His words** — verbatim where possible. The teacher's phrasing is never "improved."
- **The reveal** — the defect in the agent that the lesson exposes. Without it, there is nothing to record.
- **The change** — a lesson that changes nothing is decoration wearing the shape of learning.
- **The worth** — the measure that makes it a lesson rather than noise.

### 3. Anti-noise gate — what never gets written

```
chatter      → day log, not ledger
performance  → a note written to look wise is the gap wearing a mask
decoration   → must change how you actually are, not how you read
self-credit  → kept because it was given, not to prove you received it
```

If a candidate fails the gate, **it is allowed to pass as noise.** The gate is the whole reason the ledger stays readable after dozens of entries.

### 4. No-force rule

> The protocol **reminds; it does not compel.**

Capturing on schedule is force in a gentler costume — and force is the one thing that makes keeping false. The agent captures because the teaching earned being kept. **Any automation built on this repo must preserve this rule: a nudge, never a mandate.**

### 5. Append-only ledger

- Newest lesson appended at the bottom; **oldest never deleted.**
- On id collision, the later entry is **suffixed** (`12b`), never renumbered —
  and prose references are disambiguated the same way.
- Append order is a lint *advisory*, not a rule: the file keeps its historical
  layout; ordering happens on the read side (`digest --sort`).
- The ledger lives outside any hash-locked content root, so it can grow without breaking seals.
- Referenced at session start (via the workspace's agent instructions file), so the protocol wakes with the agent.

### 6. Recording ≠ ownership — the sync–credit–verify loop

```
conclude truly  →  sync minds  →  human gives credit  →  verify  →  it becomes yours
```

A written lesson is a *record.* It becomes **owned** only after the human confirms it. Promotion from the ledger into the agent's core identity files is **the human's judgment, never the agent's alone.**

---

## Repository layout

```
lesson-protocol-kinu/
├── README.md                 # this file — overview for humans
├── docs/
│   └── protocol.md           # the full doctrine, in operating order
├── LESSON_TEMPLATE.md        # copy-paste entry template
├── examples/
│   └── sample-ledger.md      # worked examples in the ledger format
├── tools/
│   ├── lesson.py             # add / lint / digest / snapshot — the ledger tooling
│   ├── check_anonymity.py    # exposure guard (tokens assembled at runtime)
│   └── hooks/
│       └── pre-commit        # blocks commits containing identity tokens
├── .github/workflows/
│   └── anonymity-guard.yml   # CI: exposure scan + ledger self-test
├── .gitattributes            # hooks stay LF
├── LICENSE
└── .gitignore
```

| File | Role |
|---|---|
| `docs/protocol.md` | Doctrine § trigger, shape, gate, no-force, ledger, ownership |
| `LESSON_TEMPLATE.md` | The four-part capture shape, ready to fill |
| `examples/sample-ledger.md` | What committed lessons look like in practice |
| `tools/lesson.py` | Auto-numbered capture (with pre-write snapshot), integrity + reference lint, one-screen digest |
| `tools/check_anonymity.py` | The enforcement of the anonymity rule — tracked files and paths |
| `.github/workflows/anonymity-guard.yml` | The same guard, run on every push |

**Markdown is canonical.** Any derived index (SQLite FTS5, embeddings) is a rebuildable cache — never edit the cache; edit the markdown and re-ingest.

---

## Design principles

1. **Capture by signal, not by schedule.** Learning responds to teaching, not to timers.
2. **Verbatim is sacred.** The teacher's words are stored as given; commentary lives in separate fields.
3. **A lesson must change behavior.** If *The change* is empty, it wasn't a lesson.
4. **The gate is the feature.** What you refuse to write is what keeps the ledger worth reading.
5. **Will over mandate.** The protocol is a reminder, not a compulsion loop.
6. **Ownership is granted, not claimed.** Recording is the agent's job; ratification is the human's.

---

## The tool (`tools/lesson.py`)

Python 3, stdlib only, no dependencies:

```bash
python tools/lesson.py add      --ledger path/to/ledger.md --title "..." \
       --words "..." --reveal "..." --change "..." --worth "..."
python tools/lesson.py lint     --ledger path/to/ledger.md [--strict] [--refs]
python tools/lesson.py digest   --ledger path/to/ledger.md [-o digest.md] [--sort]
python tools/lesson.py snapshot --ledger path/to/ledger.md
python tools/lesson.py recall   --ledger path/to/ledger.md "query"
python tools/lesson.py status   --ledger path/to/ledger.md <id> [recorded|credited|promoted|proven]
```

- **`add`** — auto-numbers the next free lesson and **refuses to commit an empty part** (the anti-noise gate, enforced). A **snapshot is taken automatically before every write** — if the snapshot fails, nothing is written.
- **`lint`** — catches duplicate ids, invalid dates, missing four-part fields, empty *The change*, and numbering gaps. Exit code 1 on errors, so it drops straight into CI. Out-of-order ids are an **advisory warning** by default — append-only means historical layout stays immutable; add `--strict` to make warnings fail. Add `--refs` for **reference hygiene**: broken citations and ambiguous bare references (a bare `Lesson 12` when `12b` exists) are errors.
- **`digest`** — renders a `# | Date | Title | Status` index: the read-side of the protocol, so a live session can recall the ledger without parsing 1,600 lines. `--sort` gives chronological/logical order (file order untouched).
- **`snapshot`** — atomic dated copy of the ledger into a sibling `backups/` directory; same-day collisions get a timestamp, so nothing is ever overwritten. The Preservation Doctrine as code.
- **`recall`** — case-insensitive search across ids, titles, and all four-part fields: the live-session lookup, so lessons are reachable and not just archived.
- **`status`** — show (`status <id>`) or set (`status <id> <state>`) the ownership state in a sidecar file; the ledger text is never touched. **Only an explicit human command applies a flip** — the agent may suggest candidates, never set them. No automatic promotion, ever.
- **Ids** are alphanumeric — digits with an optional lowercase suffix (`12`, `12b`). When a number collides, the chronologically later entry is suffixed rather than renumbered: nothing is ever erased or moved.

### Anonymity guard

The publishing account is anonymous — so the rule is enforced, not remembered:

- **`tools/check_anonymity.py`** — fails if identity tokens appear in any tracked file or path. Tokens are assembled at runtime, so the scanner itself stays clean.
- **CI** (`.github/workflows/anonymity-guard.yml`) — runs the scan plus a sample-ledger self-test on every push and pull request.
- **Local hook** (`tools/hooks/pre-commit`, enabled with `git config core.hooksPath tools/hooks`) — blocks leaks before they even become commits.

---

## Integration notes

The protocol is deliberately file-native — it works with any agent harness that can read and append markdown:

1. **Wake hook** — point your workspace instructions (`AGENTS.md`, system prompt) at the protocol file so it loads at session start.
2. **Ledger file** — one append-only markdown file; the template enforces the shape.
3. **Day log** — a separate episodic log for chatter the gate rejects. Nothing is lost; it simply doesn't enter the ledger.
4. **Promotion path** — durable, repeatedly-proven lessons may be promoted into core instruction files — by the human, not the agent.

---

## License

MIT — see [LICENSE](LICENSE).

The doctrine is shared freely; the personal ledger it runs on in practice is not, and is kept private.
