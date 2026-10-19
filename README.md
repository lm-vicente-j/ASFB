# Agent Secure Filter Bridge (ASFB)

> Show the agent what is public. Censor what is yours.


---

## What it is

ASFB is a library, CLI tool and MCP server, written in Python (>= 3.12), that sits between private content and a coding agent.

It reads HTML, code bases, .txt files, databases, markdown files and logs, and converts them into a secure representation for the agent, in a versioned, closed schema. In this representation, all free-form content (text, attribute values, URLs, class names, multimedia content) is replaced by:

- Keyed pseudonyms (HMAC tokens, hexadecimal strings)

The ASFB workflow:

Agent -> ASFB -> ToolCalling -> Output -> ASFB -> Agent

### Problems it addresses

- **Exposure to sensitive or offensive content.** The model never receives the source text.
- **Prompt injection.** No natural-language text from the source reaches the model, so instructions embedded in the source have no direct vehicle.
- **Reuse.** The layer is independent of any specific project and can be used by other agents or workflows that consume untrusted projects.

---

## Censor, not obfuscate

ASFB does not hide the source. Public content stays visible: HTML tags, language keywords, standard and framework APIs, and public classes such as Bootstrap's `btn btn-primary`. Only user content is censored: text, strings, identifiers you defined, file names, values, URLs and personal data. In JavaScript, the agent sees `fetch` but reads `t_4a7d…` instead of `sendInvoice`.

---

## The idea in 30 seconds with an HTML example

Input (HTML):

```html
<h1>Welcome, Ana García</h1>
<form class="send-invoice" action="/invoices/send">
  <input type="email" name="email" value="ana@example.com">
  <button type="submit" class="btn btn-primary">Send invoice</button>
</form>
<script>function sendInvoice(email) { fetch("/invoices/send"); }</script>
```

What the agent reads with the `strict` profile and a per-session key. **Illustrative values:** the tokens are made up.

```html
<h1>t_9c4e1a07d3b52f68</h1>
<form class="t_8ec1d2a47b9f3e60" action="/t_5e0b7d21a9c3f4e6/t_b71f3e8c02d9a546">
  <input type="email" name="t_c2d75e1a90f34b8e" value="t_6a2d9e4c1b08f753">
  <button type="submit" class="btn btn-primary">t_0f8c3b5e7a1d96f2</button>
</form>
<script>function t_4a7d93e01c8b5f26(t_c2d75e1a90f34b8e) { fetch("t_e5b09a3d71c4f268"); }</script>
```

Public content passes: tags, attribute names, standard values (`type="email"`), Bootstrap classes (`btn btn-primary`) and Web APIs (`fetch`). User content becomes tokens: the heading, custom class, URL path, field name, value, button text, function name and string. `email` gets the same token in the form and in the script, so the agent can link them. The view is rendered from a closed, versioned schema (`asfb.doc/1`).

---

## How the agent reads and writes

- **Read.** User content becomes tokens. The agent reads `t_8ec1d2a47b9f3e60` instead of `send-invoice`.
- **Write.** When the agent writes `t_8ec1d2a47b9f3e60` in an edit or a tool call, ASFB translates it back to `send-invoice` before applying it. Unknown tokens are rejected.
- **Tool calls.** ASFB runs the tool locally with the originals. Its output (errors, stack traces, test results) goes through a parser for that tool (pytest, mypy, compilers) and is censored before it returns to the agent. Output that cannot be parsed is dropped (fail closed).
- **Single access path.** The agent must not read protected files directly. ASFB ships agent settings (for Claude Code: permission deny rules and a `PreToolUse` hook) that block direct reads, edits and shell commands on protected paths, so the MCP server is the only way in.

Originals and the token → original map never leave the machine.

---

## Sources

| Source | Visible (public) | Censored (user content) |
|---|---|---|
| HTML | Tags, attribute names, standard attribute values, public framework classes (e.g. Bootstrap) | Text, custom class and id names, URLs, form values, script names and strings |
| Code | Syntax, keywords, builtins, standard library and public dependency APIs | User-defined names, strings, comments, file names |
| Markdown | Headings, lists, tables, code blocks | Text, URLs, alt text |
| Text (`.txt`) | Lines and paragraphs | All text |
| Logs | Levels, timestamp buckets, standard fields | Messages, custom fields and values |
| Databases | Schema structure, column types | Table and column names, values |

---

## What an agent can and cannot do with the output

| Can | Cannot |
|---|---|
| Read all public content: tags, keywords, standard APIs, structure | Read user content: text, strings, user-defined names, values, personal data |
| Follow references: under one key, the same value always gives the same token | Recover an original from its token |
| Edit files and call tools using tokens; ASFB translates them back | Receive natural-language instructions from the source |
| Compare versions with the `diffable` profile | Get tokens comparable across sessions with the `strict` profile |

---

## Guarantees and limits

The intended guarantee: **no user content from the source appears in the output, per the schema, and this is verified by tests** (no-leak invariant, canary corpus, property-based tests). It is not a formal proof.

Limits:

- **Not zero information.** Public content, structure, approximate length, frequency and order still reveal information. Quantization and caps reduce this; they do not remove it.
- **Classification errors.** User content wrongly treated as public leaks. The public vocabulary is versioned and allowlist-only to keep this small.
- **No semantics.** No summaries, topic classification, or natural-language or intent detection.
- **Parser bugs.** A bug in a parser or decoder can break a guarantee. That is why there is process isolation and a single egress gate.
- **No content certification.** Passing censorship does not mean the source is safe or trustworthy.

---

## Evaluation

Three metrics, tracked in CI:

- **Leak rate.** Canary values planted in a test corpus must never appear in any output. Target: zero. Each canary is `cnry_` plus 16 random hex digits, fresh for each test, planted in the templates of `tests/corpus/canary/carriers.json`.
- **Task success.** Share of agent tasks solved with the censored view, compared with the original source.
- **Token cost.** Tokens the model spends per task with the censored view, compared with the original source.

Current numbers are in [Results](#results).

---

## Results

This section has two tables. The first is filled by the parity suite in `tests/parity/`: each model solves the same coding tasks once with the original source and once with the ASFB censored view, and the suite compares task success and tokens spent. The second is public reference data about the same models; this project does not measure it.

### Task success and token cost, with and without ASFB

<!-- asfb-results:start -->
| Model | Model id | Runs | Pass rate without ASFB | Pass rate with ASFB | Δ (pp) | McNemar p | Canary leaks | Tokens without ASFB | Tokens with ASFB | Token overhead | Run date |
|---|---|---|---|---|---|---|---|---|---|---|---|
| GPT-6.1 Sol | `gpt-6.1-sol` | — | — | — | — | — | — | — | — | — | not run |
| Claude Opus 5.5 | `claude-opus-5-5` | — | — | — | — | — | — | — | — | — | not run |
| Gemini 4 Argon | not public | — | — | — | — | — | — | — | — | — | not run |
<!-- asfb-results:end -->

`python -m tests.parity.report` generates this table from `tests/parity/results/`; do not edit it by hand. A model passes when no canary leaks, its pass rate with ASFB is at most 5 percentage points below the rate without it, and its token overhead is at most 20%. The run with ASFB needs the `asfb` package, so until code support lands (milestone M4) only the columns without ASFB can be filled.

Run it:

```bash
pip install -r tests/parity/requirements.txt
export OPENAI_API_KEY=... ANTHROPIC_API_KEY=... GEMINI_API_KEY=...
ASFB_RUN_PARITY=1 pytest tests/parity -m parity   # calls paid model APIs
python -m tests.parity.report                    # rewrites the table above
```

On Windows PowerShell, set variables with `$env:ASFB_RUN_PARITY = "1"` before running `pytest`. Without `ASFB_RUN_PARITY=1`, `pytest` runs only the offline checks of the task corpus and the harness. The suite runs model-generated code locally, in a temporary directory with a timeout.

| Variable | Default | Meaning |
|---|---|---|
| `ASFB_PARITY_SAMPLES` | `1` | Runs per task and arm |
| `ASFB_PARITY_MARGIN` | `0.05` | Largest allowed drop in pass rate |
| `ASFB_TOKEN_OVERHEAD_MAX` | `0.20` | Largest allowed increase in total tokens |
| `ASFB_MODEL_GPT`, `ASFB_MODEL_CLAUDE`, `ASFB_MODEL_GEMINI` | `gpt-6.1-sol`, `claude-opus-5-5`, unset | Model id per vendor |
| `ASFB_EFFORT` | `high` | Reasoning effort sent to Claude |
| `ASFB_OPENAI_REASONING_EFFORT` | unset | Reasoning effort sent to GPT, if set |

Gemini 4 Argon has no public API id yet (Google showed it to a restricted group on 2026-09-30), so its row is skipped until `ASFB_MODEL_GEMINI` is set.


## Planned usage

All interfaces are **planned**.

```python
import asfb

doc = asfb.sanitize("page.html", profile="strict")
print(doc.views.censored())
```

```text
asfb sanitize page.html --profile strict
asfb sanitize ./src --profile strict
```

MCP server tools:

| Tool | Purpose |
|---|---|
| `sanitize` | Read a source and return its censored view |
| `edit` | Apply an edit written with tokens: translate back and write the file |
| `run` | Run a tool with token arguments: translate back, execute, censor the output |
| `diff` | Compare two documents built with the `diffable` profile |

---

## Design principles

1. **Allowlist, never blocklist.** Only what is in a versioned public vocabulary (languages, web standards, frameworks such as Bootstrap) passes; everything else is censored. Each vocabulary has an id made of lowercase letters and digits, separated by dots, and an integer version (1 to 65535). A document lists the id and version of each vocabulary it uses, and egress rejects unknown ids, version mismatches and terms that are not in the declared vocabulary. Terms are 1 to 64 printable ASCII characters, without spaces.
2. **Closed output schema.** Pydantic v2 models with `extra="forbid"`. Only enums, bounded integers, booleans, tokens and public vocabulary. No free `str` from the source. The first bucket scheme is `log2`: the index is the bit length of a non-negative integer (0 to 64).
3. **Taint typing.** Source text lives in `Untrusted[str]` and can only become a `Token`, `Bucket` or `EnumValue` through sanitizer functions. Checked with strict mypy; **Python gives no compile-time guarantee**.
4. **Defense in depth.** A single egress gate (`egress.emit()`) revalidates every output against JSON Schema and rejects any string that is neither public vocabulary nor a token. It fails closed, with no partial output.
5. **Process isolation.** Parsing and sanitizing run in a worker with no network and no writes, with size, depth, CPU and memory limits. Only validated JSON crosses the boundary.
6. **Local vault.** Originals and the token → original map stay on the machine.
7. **Content-free logs and exceptions.** Static codes, no local variables in tracebacks.
8. **Determinism.** Same input and same key give the same tokens (HMAC-SHA256, 256-bit key, per session by default). A token is `t_` plus the first 16 lowercase hex digits of the HMAC of `asfb.token/1`, a NUL byte and the UTF-8 bytes of the value, with no extra normalization. There is no per-context salt: under one key, a value gets the same token everywhere. The session key is 32 bytes from `secrets.token_bytes`, kept only in process memory.

---

## Roadmap

- **M0.** Scaffolding, types, egress gate and canary harness.
- **M1.** HTML sanitizer and censored view.
- **M2.** Vault, token translation, `edit` and `run`, tool-output parsers, agent access settings, CLI and MCP server: first end-to-end version, with HTML.
- **M3.** Text and markdown.
- **M4.** Code: Python first, then other languages.
- **M5.** Logs.
- **M6.** Databases.

---

## Requirements

- Python >= 3.12.
- Windows, Linux or macOS.
- Planned stack: pydantic v2, jsonschema (egress check against the committed JSON Schema), pytest, hypothesis, ruff, mypy `--strict`.

**Isolation note:** isolation depends on the OS. Linux gets stronger isolation (seccomp). Windows has no seccomp; it uses a child process with Job Objects and resource limits, which is weaker.

---

## Planned repository layout

```text
ASFB/
├── README.md
├── pyproject.toml
├── docs/
├── src/asfb/
│   ├── types/                 Untrusted, Token, Bucket, EnumValue types
│   ├── vocab/                 versioned public vocabularies (data)
│   ├── ingest/                readers per source (HTML first)
│   ├── normalize/             NFC, control characters and bidi marks
│   ├── sanitize/              per-source censoring
│   ├── schema/                pydantic models and JSON Schema
│   ├── egress/                egress gate
│   ├── vault/                 token ↔ original store and translation
│   ├── runner/                tool execution and output parsers
│   ├── agents/                access settings for agents (Claude Code first)
│   ├── sandbox/               isolated worker
│   ├── cli/
│   └── mcp/
└── tests/
    ├── unit/
    ├── property/
    ├── canary/
    ├── fuzz/
    ├── corpus/
    └── parity/                task success and token cost, with and without ASFB
```

---