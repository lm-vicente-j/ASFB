# ASFB architecture



## Purpose and scope

ASFB is a library, CLI tool and MCP server, written in Python (>= 3.12). It sits between private content and a coding agent. It reads HTML, code bases, .txt files, databases, markdown files and logs, and converts them into a secure representation for the agent, in a versioned, closed schema.

In this representation, all free-form content (text, attribute values, URLs, class names, multimedia content) is replaced by keyed pseudonyms (HMAC tokens, hexadecimal strings).

ASFB addresses three problems:

- **Exposure to sensitive or offensive content.** The model never receives the source text.
- **Prompt injection.** No natural-language text from the source reaches the model, so instructions embedded in the source have no direct vehicle.
- **Reuse.** The layer is independent of any specific project and can be used by other agents or workflows that consume untrusted projects.

ASFB is **censor, not obfuscate**. It does not hide the source. Public content stays visible: HTML tags, language keywords, standard and framework APIs, and public classes such as Bootstrap's `btn btn-primary`. Only user content is censored: text, strings, identifiers you defined, file names, values, URLs and personal data. In JavaScript, the agent sees `fetch` but reads `t_4a7d…` instead of `sendInvoice`.

---

## System overview

The ASFB workflow:

```text
Agent -> ASFB -> ToolCalling -> Output -> ASFB -> Agent
```

Originals and the token → original map never leave the machine. See [Security design](#security-design).

---

## Data paths

### Read

User content becomes tokens. The agent reads `t_8ec1d2a47b9f3e60` instead of `send-invoice`.

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

### Write-back

When the agent writes `t_8ec1d2a47b9f3e60` in an edit or a tool call, ASFB translates it back to `send-invoice` before applying it. Unknown tokens are rejected.

### Tool runs

ASFB runs the tool locally with the originals. Its output (errors, stack traces, test results) goes through a parser for that tool (pytest, mypy, compilers) and is censored before it returns to the agent. Output that cannot be parsed is dropped (fail closed).

---

## Components

The planned modules under `src/asfb/`, with the README description of each:

| Module | Description | Role in the data paths |
|---|---|---|
| `types/` | `Untrusted`, `Token`, `Bucket`, `EnumValue` types | Marks source text as untrusted and defines the only types allowed in output |
| `vocab/` | Versioned public vocabularies (data) | Holds the allowlist of public content |
| `ingest/` | Readers per source (HTML first) | Reads each source type |
| `normalize/` | NFC, control characters and bidi marks | Normalizes text |
| `sanitize/` | Per-source censoring | Produces the censored content |
| `schema/` | Pydantic models and JSON Schema | Defines the closed output schema |
| `egress/` | Egress gate | Revalidates every output before it leaves (`egress.emit()`) |
| `vault/` | Token ↔ original store and translation | Keeps originals locally and translates tokens for write-back and tool runs |
| `runner/` | Tool execution and output parsers | Runs tools with originals and censors their output |
| `agents/` | Access settings for agents (Claude Code first) | Blocks direct access to protected paths |
| `sandbox/` | Isolated worker | Runs parsing and sanitizing with no network and no writes |
| `cli/` | Command-line interface | `asfb` command |
| `mcp/` | MCP server | Exposes the tools to the agent |

The README gives the order of the workflow and the roles above; it does not spell out the exact call order between modules. See [Not specified in the README](#not-specified-in-the-readme).

---

## Data model and censoring

### Taint types

Source text lives in `Untrusted[str]`. It can only become a `Token`, `Bucket` or `EnumValue` through sanitizer functions. This is checked with strict mypy. **Python gives no compile-time guarantee.**

### Visible and censored content

| Source | Visible (public) | Censored (user content) |
|---|---|---|
| HTML | Tags, attribute names, standard attribute values, public framework classes (e.g. Bootstrap) | Text, custom class and id names, URLs, form values, script names and strings |
| Code | Syntax, keywords, builtins, standard library and public dependency APIs | User-defined names, strings, comments, file names |
| Markdown | Headings, lists, tables, code blocks | Text, URLs, alt text |
| Text (`.txt`) | Lines and paragraphs | All text |
| Logs | Levels, timestamp buckets, standard fields | Messages, custom fields and values |
| Databases | Schema structure, column types | Table and column names, values |

### Output values

The output contains only these kinds of value:

- **Tokens**: keyed pseudonyms (`t_…` in the examples).
- **Buckets**: `Bucket` values, such as timestamp buckets in logs.
- **Enum values**: `EnumValue`. The README lists log levels among the visible log content.
- **Public vocabulary**: versioned, allowlist-only.

### Closed schema

The output schema is a closed, versioned schema, identified as `asfb.doc/1`.

- Pydantic v2 models with `extra="forbid"`.
- Only enums, bounded integers, booleans, tokens and public vocabulary. No free `str` from the source.

### Views and profiles

A document exposes its content through views, for example `doc.views.censored()`.

The README names two profiles:

| Profile | What it allows |
|---|---|
| `strict` | Per-session key. Tokens are not comparable across sessions. |
| `diffable` | Comparing two documents built with this profile, through the `diff` tool. |

---

## Tokens and keys

- Tokens are **HMAC-SHA256** values with a **256-bit key**. The key is per session by default.
- Tokens are **deterministic**: under one key, the same value always gives the same token. This is how the agent follows references.
- With the `strict` profile, tokens are not comparable across sessions.
- Tokens are hexadecimal strings. The README examples show them with a `t_` prefix, such as `t_4a7d…`.

---

## Sources

The README lists these sources, with what is visible and what is censored (see [Visible and censored content](#visible-and-censored-content)):

- HTML
- Code
- Markdown
- Text (`.txt`)
- Logs
- Databases

---

## Interfaces

All interfaces are **planned**.

### Python API

```python
import asfb

doc = asfb.sanitize("page.html", profile="strict")
print(doc.views.censored())
```

### CLI

```text
asfb sanitize page.html --profile strict
asfb sanitize ./src --profile strict
```

### MCP server tools

| Tool | Purpose |
|---|---|
| `sanitize` | Read a source and return its censored view |
| `edit` | Apply an edit written with tokens: translate back and write the file |
| `run` | Run a tool with token arguments: translate back, execute, censor the output |
| `diff` | Compare two documents built with the `diffable` profile |

---

## Agent integration

- **Single access path.** The agent must not read protected files directly. The MCP server is the only way in.
- ASFB ships agent settings. For Claude Code, these are permission deny rules and a `PreToolUse` hook. They block direct reads, edits and shell commands on protected paths.

### Agent capabilities

| Can | Cannot |
|---|---|
| Read all public content: tags, keywords, standard APIs, structure | Read user content: text, strings, user-defined names, values, personal data |
| Follow references: under one key, the same value always gives the same token | Recover an original from its token |
| Edit files and call tools using tokens; ASFB translates them back | Receive natural-language instructions from the source |
| Compare versions with the `diffable` profile | Get tokens comparable across sessions with the `strict` profile |

---

## Security design

### Design principles

1. **Allowlist, never blocklist.** Only what is in a versioned public vocabulary (languages, web standards, frameworks such as Bootstrap) passes; everything else is censored.
2. **Closed output schema.** Pydantic v2 models with `extra="forbid"`. Only enums, bounded integers, booleans, tokens and public vocabulary. No free `str` from the source.
3. **Taint typing.** Source text lives in `Untrusted[str]` and can only become a `Token`, `Bucket` or `EnumValue` through sanitizer functions. Checked with strict mypy; **Python gives no compile-time guarantee**.
4. **Defense in depth.** A single egress gate (`egress.emit()`) revalidates every output against JSON Schema and rejects any string that is neither public vocabulary nor a token. It fails closed, with no partial output.
5. **Process isolation.** Parsing and sanitizing run in a worker with no network and no writes, with size, depth, CPU and memory limits. Only validated JSON crosses the boundary.
6. **Local vault.** Originals and the token → original map stay on the machine.
7. **Content-free logs and exceptions.** Static codes, no local variables in tracebacks.
8. **Determinism.** Same input and same key give the same tokens (HMAC-SHA256, 256-bit key, per session by default).

### Guarantees

The intended guarantee: **no user content from the source appears in the output, per the schema, and this is verified by tests** (no-leak invariant, canary corpus, property-based tests). It is not a formal proof.

### Limits

- **Not zero information.** Public content, structure, approximate length, frequency and order still reveal information. Quantization and caps reduce this; they do not remove it.
- **Classification errors.** User content wrongly treated as public leaks. The public vocabulary is versioned and allowlist-only to keep this small.
- **No semantics.** No summaries, topic classification, or natural-language or intent detection.
- **Parser bugs.** A bug in a parser or decoder can break a guarantee. That is why there is process isolation and a single egress gate.
- **No content certification.** Passing censorship does not mean the source is safe or trustworthy.

### Isolation and platforms

- Isolation depends on the OS.
- Linux gets stronger isolation (seccomp).
- Windows has no seccomp. It uses a child process with Job Objects and resource limits, which is weaker.
- The README does not describe the macOS isolation mechanism. See [Not specified in the README](#not-specified-in-the-readme).

---

## Evaluation and testing

### Metrics

Three metrics, tracked in CI:

- **Leak rate.** Canary values planted in a test corpus must never appear in any output. Target: zero.
- **Task success.** Share of agent tasks solved with the censored view, compared with the original source.
- **Token cost.** Tokens the model spends per task with the censored view, compared with the original source.

Current numbers are in [Results](README.md#results).

### Planned test layout

```text
tests/
├── unit/
├── property/
├── canary/
├── fuzz/
├── corpus/
└── parity/                task success and token cost, with and without ASFB
```

### Parity suite

The `tests/parity/` suite fills the first table in the README Results section. Each model solves the same coding tasks once with the original source and once with the ASFB censored view. The suite compares task success and tokens spent.

A model passes when:

- no canary leaks,
- its pass rate with ASFB is at most 5 percentage points below the rate without it,
- its token overhead is at most 20%.

The table is generated by `python -m tests.parity.report` from `tests/parity/results/`; it is not edited by hand. The run with ASFB needs the `asfb` package, so until code support lands (milestone M4) only the columns without ASFB can be filled.

Run it:

```bash
pip install -r tests/parity/requirements.txt
export OPENAI_API_KEY=... ANTHROPIC_API_KEY=... GEMINI_API_KEY=...
ASFB_RUN_PARITY=1 pytest tests/parity -m parity   # calls paid model APIs
python -m tests.parity.report                    # rewrites the table above
```

On Windows PowerShell, set variables with `$env:ASFB_RUN_PARITY = "1"` before running `pytest`. Without `ASFB_RUN_PARITY=1`, `pytest` runs only the offline checks of the task corpus and the harness. The suite runs model-generated code locally, in a temporary directory with a timeout.

Environment variables:

| Variable | Default | Meaning |
|---|---|---|
| `ASFB_PARITY_SAMPLES` | `1` | Runs per task and arm |
| `ASFB_PARITY_MARGIN` | `0.05` | Largest allowed drop in pass rate |
| `ASFB_TOKEN_OVERHEAD_MAX` | `0.20` | Largest allowed increase in total tokens |
| `ASFB_MODEL_GPT`, `ASFB_MODEL_CLAUDE`, `ASFB_MODEL_GEMINI` | `gpt-6.1-sol`, `claude-opus-5-5`, unset | Model id per vendor |
| `ASFB_EFFORT` | `high` | Reasoning effort sent to Claude |
| `ASFB_OPENAI_REASONING_EFFORT` | unset | Reasoning effort sent to GPT, if set |

Gemini 4 Argon has no public API id yet, so its row is skipped until `ASFB_MODEL_GEMINI` is set.

The second table in the README Results section is public reference data about the same models. This project does not measure it.

---

## Repository layout

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
- Planned stack: pydantic v2, pytest, hypothesis, ruff, mypy `--strict`.

---

## Not specified in the README

- Token length and exact token format beyond hexadecimal digits and the `t_` prefix in the examples.
- What exactly the HMAC is computed over.
- Where the key is stored, and how a per-session key is created and discarded.
- How the `diffable` profile handles keys, and whether its tokens are stable across sessions.
- Edit and write-back message format, and the error shown for rejected unknown tokens.
- How new text without tokens is introduced into a file.
- Resource limit values (size, depth, CPU, memory, timeout) for the worker.
- Quantization and cap values that reduce information leaks.
- Bucket definitions, such as timestamp bucket width.
- Which tools `run` may execute, and its network policy.
- Exact call order between ingest, normalize, sanitize, schema and egress.
- Which output parsers exist beyond pytest, mypy and compilers, and how a parser is chosen for a tool.
- Contents and versioning scheme of the public vocabulary.
- How `sanitize` handles a directory (such as `./src`): which files it reads and how it treats each one.
- Config format.
- Supported model providers for ASFB itself. The parity suite names only the OpenAI, Anthropic and Gemini models.
- macOS isolation mechanism.
- Database source details: drivers, connection and how the schema is read.
- Vault storage format and location.
- Format and patterns of the Claude Code deny rules and `PreToolUse` hook, and the set of protected paths.
- Output format of the `diff` tool.
- Transport used by the MCP server.
- Contents of the canary corpus and the parity task corpus.
- How the parity suite's offline checks are run apart from the `pytest` command.
