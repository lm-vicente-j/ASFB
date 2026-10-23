# ASFB architecture



## Purpose and scope

ASFB is a library, CLI tool and MCP server, written in Python (>= 3.12). It sits between private content and a coding agent. It reads HTML, Python source files, .txt files, databases, markdown files, logs and test run reports, and converts them into a secure representation for the agent, in a versioned, closed schema.

In this representation, all free-form content (text, attribute values, URLs, class names, multimedia content) is replaced by keyed pseudonyms (HMAC tokens, hexadecimal strings).

ASFB addresses three problems:

- **Exposure to sensitive or offensive content.** The model never receives the source text.
- **Prompt injection.** No natural-language text from the source reaches the model, so instructions embedded in the source have no direct vehicle.
- **Reuse.** The layer is independent of any specific project and can be used by other agents or workflows that consume untrusted projects.

ASFB is **censor, not obfuscate**. It does not hide the source. Public content stays visible: HTML tags, language keywords, standard and framework APIs, and public classes such as Bootstrap's `btn btn-primary`. Only user content is censored: text, strings, identifiers you defined, file names, values, URLs and personal data. In JavaScript (not supported yet, planned), the agent would see `fetch` but read `t_4a7d…` instead of `sendInvoice`. Python is supported (M4).

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

**M1 note.** The HTML sanitizer turns the whole content of `<script>` and `<style>` into one token, so today the script line reads `<script>t_…</script>`, and `email` is not linked between the form and the script. Censoring JavaScript names and strings, with `fetch` visible, is not implemented: code support covers Python only (M4).

### Write-back

When the agent writes `t_8ec1d2a47b9f3e60` in an edit or a tool call, ASFB translates it back to `send-invoice` before applying it. Unknown tokens are rejected.

### Tool runs

ASFB runs the test tool locally with the originals. In M2 the only tool is pytest, and only its junit report is read: console output is discarded. The report is parsed in the isolated worker (kind `junit`) and censored; a missing, oversized, non-UTF-8 or malformed report is dropped (fail closed) and the summary says `report=dropped`. The package `asfb.runner` builds the argv from the resolved targets, runs the tool with a wall-clock limit that kills the whole process tree, and on Linux puts it in an empty network namespace when `unshare` is available.

---

## Components

The modules under `src/asfb/` (`api.py`, `session.py` and `editing.py` sit at the top level and are not listed here):

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
| `sandbox/` | Isolated worker | Runs parsing and sanitizing in a separate process under limits and an audit hook |
| `cli/` | Command-line interface | `asfb` command |
| `mcp/` | MCP server | Exposes the tools to the agent |

For HTML, text and markdown the call order is ingest, normalize, sanitize, schema, then egress: `egress.emit()` validates the document and `egress.render()` prints the censored view from the emitted JSON. Markdown adds a block scan (`ingest/markdown.py`) before the inline scan of `sanitize/markdown.py`. Python (`asfb.code/1`) reads with `ingest/code.py`, which normalizes and runs the stdlib tokenizer and returns only kinds and offsets; `sanitize/code.py` slices the text to build surfaces, and the worker returns the lexeme spans for edit. The tokenizer is named only in `ingest/code.py` and `sanitize/tokens.py` (an architecture test enforces it). Logs (`asfb.log/1`) follow the same stages: `ingest/log.py` splits and parses, `sanitize/log.py` censors, `schema/log.py` holds the model. Databases (`asfb.db/1`) follow the same stages, but the file is read only in the worker: `ingest/db.py` checks the header and size in the client, and its hardened reader loads the bytes with `deserialize` inside the worker. `sanitize/db.py` censors and `schema/db.py` holds the model.

---

## Data model and censoring

### Taint types

Source text lives in `Untrusted[str]`. It can only become a `Token`, `Bucket` or `EnumValue` through sanitizer functions. This is checked with strict mypy. **Python gives no compile-time guarantee.**

### Visible and censored content

| Source | Visible (public) | Censored (user content) |
|---|---|---|
| HTML | Tags, attribute names, standard attribute values, public framework classes (e.g. Bootstrap), URL schemes and URL shape | Text, custom element, attribute, class and id names, URL hosts, paths, queries and fragments, form values, comments, script and style contents |
| Python (`.py`, `.pyi`) | Keywords, soft keywords, builtins, standard library names, operators, the numbers 0 and 1, indentation depth and line numbers, string prefix classes | User-defined names (third-party names too, in this version), other numbers, strings and docstrings, f-strings whole, comments, file names |
| Markdown (`.md`, `.markdown`) | Heading levels, list kinds and depth, task marks, table shape and alignment, code fences and public language names, block quote depth, rules, link and image shape | Text, code, URL hosts, segments, queries and fragments, titles, alt text, labels, embedded HTML, front matter, non-public language names |
| Text (`.txt`) | Paragraphs, line endings and indentation width | Every line |
| Logs (`.log`, `.jsonl`, `.ndjson`) | Levels, timestamp buckets, standard fields | Messages, custom fields and values |
| Databases (`.db`, `.sqlite`, `.sqlite3`) | Table, column, index, view and trigger structure, declared type names from a closed list, affinities, foreign keys, row count and page size buckets, whether a WAL file exists | Names (except SQLite's own tables), the SQL text of views, triggers, checks and defaults, values, comments |
| Run results (`asfb.run/1`) | The tool name, the exit status, the totals and each case's outcome | Suite, class and test names and parameters; tracebacks and console output are never kept |

The HTML sanitizer (M1) runs ingest, normalize, sanitize, schema and egress, in that order:

- **Input.** One `.html` or `.htm` file, or a string. UTF-8 only. At most 1,048,576 bytes, 64 levels of element nesting, 20,000 nodes and 64 attributes per element; for a repeated attribute the first one wins. Anything larger is refused with a static error code.
- **Public terms.** Known element and attribute names, listed values of enumerated attributes (such as `type`, `method`, `rel`, `dir` and `role`), URL schemes and Bootstrap classes stay visible. They come from the vocabularies `html.elements`, `html.attributes`, `html.values`, `url.schemes` and `css.bootstrap` (a curated subset of Bootstrap 5.3 classes), all version 1, in `src/asfb/vocab/data/`. Enumerated values match exactly or ASCII case-insensitively.
- **Tokens.** Other element and attribute names (custom elements, `data-*`) become tokens. Each text node, comment, CDATA section, declaration and processing instruction becomes one token, and so does the whole text of `<script>`, `<style>`, `<title>` and `<textarea>`. Whitespace-only text is dropped.
- **Attribute values.** `class`, `id`, `name`, `for`, `headers` and similar attributes get one token per space-separated part, so `for="email"` links to `id="email"`; Bootstrap classes stay visible. Other values, including `style`, `srcset` and `on*` handlers, become one token. An attribute with no value or an empty value prints bare.
- **URLs.** In `href`, `src`, `action`, `formaction`, `cite`, `poster`, `data` and `background`, an `http`, `https`, `ws`, `wss`, `ftp`, `file` or scheme-less URL keeps its shape: the host, each path segment, the query and the fragment become one token each, as in `/t_…/t_…?t_…#t_…`. For `mailto`, `tel`, `data`, `javascript` and `blob`, everything after the scheme is one token. Any other URL is one token.
- **Other.** The doctype only shows whether it is `<!DOCTYPE html>`. Character references are decoded before tokenizing.

### Output values

The output contains only these kinds of value:

- **Tokens**: keyed pseudonyms (`t_…` in the examples).
- **Buckets**: `Bucket` values, such as timestamp buckets in logs. The first bucket scheme is `log2`: the index is the bit length of a non-negative integer (0 to 64).
- **Enum values**: `EnumValue`, a vocabulary id plus a term. The README lists log levels among the visible log content.
- **Public vocabulary**: versioned, allowlist-only. Each vocabulary has an id made of lowercase letters and digits, separated by dots, and an integer version (1 to 65535). A document lists the id and version of each vocabulary it uses, and egress rejects unknown ids, version mismatches and terms that are not in the declared vocabulary. Terms are 1 to 64 printable ASCII characters, without spaces.

### Closed schema

The output schema is a closed, versioned schema, identified as `asfb.doc/1`.

- Pydantic v2 models with `extra="forbid"`.
- Only enums, bounded integers, booleans, tokens and public vocabulary. No free `str` from the source.
- The JSON Schema is committed as `src/asfb/schema/asfb.doc-1.schema.json`. Egress validates every output against it with `jsonschema`.
- The Python model is `asfb.code/1` (`src/asfb/schema/code.py`, committed as `asfb.code-1.schema.json`): a flat list of lexemes with a line, a closed kind and a value (a vocabulary term, a closed operator, `0` or `1`, or a token), plus the prefix class of a string and the depth of an indent or dedent.
- A document has `schema_id`, `profile`, `source` (`html`), `vocab` and `body`. The body is a tree of `Element`, `Text`, `Opaque` (comment, CDATA, processing instruction or declaration) and `Doctype` nodes. An attribute value is `true`, a token, an enum value, a `Url` or a list of tokens and enum values.

### Views and profiles

A document exposes its content through views, for example `doc.views.censored()`. `egress.render()` prints the censored view from the emitted JSON after validating it again: tokens, vocabulary terms and tag syntax, indented two spaces per level, with void elements without an end tag.

The README names two profiles:

| Profile | What it allows |
|---|---|
| `strict` | Per-session key. Tokens are not comparable across sessions. |
| `diffable` | Comparing two documents built with this profile, through the `diff` tool. |

`diffable` requires a key: a caller-supplied `SessionKey`, or a key file made with `asfb keygen`. Tokens stay equal across calls and sessions that use the same key. Inside one session, `diff` compares any two stored revisions with any profile; across sessions it is the key file that keeps tokens equal.

---

### Python source policies (`asfb.code/1`)

**Validation and speed.** `egress/schema_check.py` compiles each committed JSON Schema into a tree of closures once per process. The metaschema is still checked with `jsonschema` when a schema loads. The compiler accepts only the keywords the committed schemas use and refuses any other keyword at load (`SchemaError`), so a schema that adds a keyword fails loudly and is never skipped. The checker agrees with `jsonschema` on random JSON and on mutated emitted documents of each model the API builds (`tests/unit/test_schema_check.py`). Each document is checked in one call. Nothing is sampled and no result is cached between documents. A sanitize still checks the document twice: the first exit (`emit_before_render`) and `render()`, which runs the full gate again on the same text. For `asfb.code/1` the first exit runs only the structural check (serialization and the typed model); the full gate runs in `render()` before any caller receives the text. Every other model takes the full gate at both exits.

Measured in development (Linux, CPython 3.14 venv, one run per input; the minimum of three for the validator):

| Input | Before | After |
|---|---|---|
| 1,600 lines (mixed repo sources) | 9.0 s | 1.2 s |
| 2,000 lines (mixed repo sources) | 10.7 s | 1.5 s |
| One validation of a 2,000-line document | 4.4 s (`jsonschema`) | 0.13 s (compiled) |
| 26,000 lines | about 160 s (reported, not re-measured) | refused at read, `E_CODE_TOO_LARGE` |
| 20,000 lines, 80,000 lexemes | not measured | 9.3 s |
| 192,000 lexemes (16,000 lines of 12) | not measured | 32 s |

The 10 s ceiling was not met at the lexeme limit of that measurement (192,000 lexemes, 32 s): the cost is linear, about 150 µs per lexeme, and five typed validations, the string walk and the declaration walk are all required. The limit is now 50,000 lexemes; the measurements below are for that limit.

**Python source, asfb.code/1 (decisions of the architect).** The same two inputs were timed before and after the change: 2,000 realistic lines (14,895 lexemes) and 50,000 lexemes on 3,004 lines, end to end through `sanitize_python` (censor, emit, validate, render). Linux, CPython 3.14 venv, a shared machine: the runs vary by about 15 %.

| Input | Before | After |
|---|---|---|
| 2,000 lines, in process | 1.84 s (median of 3) | 1.71 s (median of 5, minimum 1.65 s) |
| 50,000 lexemes, in process | 5.83 s (median of 3) | 5.20 s (median of 5, minimum 5.17 s) |
| 2,000 lines, isolated worker | 3.11 s (median of 3) | 2.93 s (median of 3) |
| 50,000 lexemes, isolated worker | 10.73 s (median of 3) | 8.82 s (median of 3) |

An earlier "after" run under higher load gave 2.12 s, 6.24 s, 3.39 s and 10.38 s for the same rows. The memo and the batch did not cut the time measurably. The memo on and off differed by less than the spread between runs; the batch builds 50,000 lexemes in about 0.58 s against 0.60 s one by one, with the same dumps. The cost is the passes the gate and the censor make over the whole document (the typed validations, the JSON Schema checker, the vocabulary and string walks, and the two full gate passes), and none of them is removed. **Known deviation:** 2,000 lines stays above 1 s (1.65 to 2.1 s in process) and is registered as such. The isolated 50,000-lexeme run takes 8.8 to 10.4 s on this machine, against the 15 s wall-clock limit of a code job (`KIND_WALL_SECONDS` in `sandbox/client.py`); the margin is 1.4 times at the slower end.

**Code limits.** A Python source is refused as a whole with a static code and never truncated:

- 524,288 bytes of UTF-8 (512 KiB): `E_CODE_TOO_LARGE`. A `.py` or `.pyi` file over the limit is refused at read with the same code.
- 20,000 lines: `E_CODE_TOO_LARGE`. A final newline ends the last line and does not start another.
- 50,000 lexemes: `E_CODE_TOO_MANY_LEXEMES`, at lexeme 50,001. An f-string is one lexeme from its first to its last token. Ingest counts the lexemes while it tokenizes and stops there, before any validation; the censor checks the same bound before it mints the next lexeme. The order is bytes, then lines, then lexemes, and each refuses the whole document.
- 10,000 characters per line, 64 KiB per lexeme surface and 100 indentation levels keep their codes (`E_CODE_LINE_TOO_LONG`, `E_CODE_TOO_LARGE`, `E_CODE_INDENT_TOO_DEEP`).

**Edit on raw text.** An edit of `asfb.code/1` replaces one raw byte range of the file. `normalize.code_spans` gives each normalized character the raw span that produced it (a CRLF pair is one LF with the span of both bytes). The first and last matched lexemes give the normalized range, and `normalize.code_raw_range` maps it to the raw range. Every byte outside that range is written as it was read: dropped Cf and Cc characters, CRLF, mixed line endings and a BOM. The new text takes the file's line ending only when the whole file is CRLF. The edit is refused, and the file untouched, when:

- the range touches a dropped control character (the one just before it or the one just after it) or contains one (`E_EDIT_CONTROL_CHARACTER`);
- the raw result does not normalize to the expected text, so the mapping is ambiguous (`E_EDIT_AMBIGUOUS`).

The write stays atomic and guarded by the digest of the bytes that were read.

**Python vocabularies.** `tools/gen_py_vocab.py` derives `py.keywords`, `py.builtins`, `py.stdlib` and `py.exceptions` from CPython 3.12.15 (`PINNED_PATCH`), refuses any other interpreter, and writes the exact version, the vocabulary version (`VOCAB_VERSIONS`, per vocabulary) and the sha256 of the canonical terms. Names that exist only in later releases, and modules that 3.12 ships but later releases remove, are excluded; `pytest` names are not in `py.exceptions`, which is built from the builtins only. The loader checks `sha256` when the file has one. Version history: `py.keywords` and `py.builtins` stay at 1 (same terms); `py.stdlib` and `py.exceptions` are version 2 (stdlib terms and exception names changed from the 3.14 runtime used before). The byte-for-byte test runs when a 3.12.15 interpreter is found: `ASFB_PY312`, or the uv default under `~/.local/share/uv/python`.

### Log policies (`asfb.log/1`)

**Limits.** A log is refused as a whole with a static code and never truncated. `ingest/limits.py` holds the values: 16 MiB of UTF-8 (`E_LOG_TOO_LARGE`, checked at read in the session and in the API), 200,000 records (`E_LOG_TOO_MANY_RECORDS`), 64 KiB per physical line (`E_LOG_LINE_TOO_LONG`), 256 fields per record counting nested fields (`E_LOG_TOO_MANY_FIELDS`), 128 characters per field name (`E_LOG_FIELD_NAME_TOO_LONG`) and 16 levels of JSON nesting kept as fields. The isolated request is a JSON body of up to 32 MiB (`protocol.MAX_REQUEST`), because control characters take six bytes each in JSON; a log whose escaped body is larger is refused with `E_SOURCE_SIZE` in the isolated path only.

**Parsing.** Ingest reads each line in one pass: JSON Lines with the stdlib `json` module (duplicate keys, `NaN`, infinities and non-object lines are raw), and plain text with two anchored patterns, one for the ISO 8601 header and one for the level word. The level word is checked against `log.levels` here, because the check decides where the header ends and the message begins; nothing else is folded (`vocab.log_level`). A continuation line (starting with a space or a tab) joins the record before it. Ingest keeps every string Untrusted and the instants only as milliseconds, for deltas.

**Censoring.** `sanitize/log.py` is the only place that turns entries into output. A time key or header stamp is consumed: its instant gives the bucket and the value is never emitted, not even as digits. The first valid instant of a record is the one kept. A time value that is not a valid instant is an ordinary value and so a token. Numbers are log2 buckets of their absolute value with a sign. Arrays and objects past the depth limit are one token of their compact JSON.

**Decisions to review.**
- A JSON number in a time field is read as Unix seconds. A log in milliseconds gets deltas a thousand times too large; the output stays free of absolute values either way.
- The first timed record of a log has no time bucket, since it has no previous record.
- Diff compares the full censored line of each record, not only the level and message. The design asked for alignment by level and message; the full line also shows field and time changes, and it reveals no more than the view does.
- The format is chosen by the suffix: `.log` is text, `.jsonl` and `.ndjson` are JSON Lines. A `.log` file holding JSON lines therefore gives raw records. The worker refuses any other name with `E_WORKER_PROTOCOL`.
- Blank lines are skipped in both formats. A blank line inside a traceback therefore ends nothing; the next indented line still joins the record.

**Edit.** Not supported. `Session.edit` on a log refuses with `E_EDIT_UNSUPPORTED_KIND` before any other check, so a stale revision of a log gives the same answer.

### Database policies (`asfb.db/1`)

**Reading.** `ingest/db.py` is the only module that opens a database, and it never gives SQLite a path: `sqlite3.connect(":memory:")` followed by `deserialize`. The connection is hardened before the image is loaded (`query_only`, `trusted_schema` off, defensive mode when available, `temp_store` in memory, `ATTACH` limit 0, extension loading off), and the authorizer is installed right after the load, because SQLite's own loading statements would be refused. The authorizer allows reads of `sqlite_schema`, reads of tables that are not virtual, `count()`, the pragmas `table_xinfo`, `foreign_key_list`, `index_list`, `index_info`, and `quick_check` only when no virtual table exists (`quick_check` connects every virtual table, which runs module code). Anything else is denied and marked. Each query has a budget of progress callbacks; `quick_check` has a larger one. A row count whose budget runs out is `unknown`; any other budget exhaustion is `E_DB_BUDGET_EXCEEDED`.

**Errors.** SQLite's exception messages are never read or propagated: a failure is mapped by the guard state (denied, budget, or corrupt). A name with an embedded NUL cannot be quoted into SQL and is `E_DB_CORRUPT`.

**Audit.** `sandbox/audit.py` refuses, during a database job only, `sqlite3.connect` to any path but `:memory:` and every extension load. Ordinary SQLite use is otherwise not blocked by the hook.

**Decisions to review.**
- The file limit is 16 MiB (`MAX_DB_BYTES`), not the 256 MiB of the design. A file travels as base64 in one request frame capped at 32 MiB, and the worker holds the file, its base64 text and its SQLite copy under a 512 MiB job limit on Windows.
- A database is always read in a worker, even with `isolate=False`, since the in-process path would parse hostile input in the caller's process.
- Sample rows are not implemented; the design made them optional and off by default.
- A count of the rows of a plain table is one btree call in SQLite, so the budget rarely runs out; the budget is still enforced per query.
- The `WITHOUT ROWID` flag is read from the tail of the stored `CREATE` text, after its last parenthesis. It is a boolean fact, so a misreading is not a leak, but it is an approximation.
- A WAL-mode database without its WAL file is `E_DB_CORRUPT`, not a separate code.

## Tokens and keys

- Tokens are **HMAC-SHA256** values with a **256-bit key**. The key is per session by default.
- Tokens are **deterministic**: under one key, the same value always gives the same token. This is how the agent follows references.
- With the `strict` profile, tokens are not comparable across sessions.
- A token is `t_` plus the first 16 lowercase hex digits of the HMAC (pattern `^t_[0-9a-f]{16}$`).
- The HMAC is computed over `asfb.token/1`, a NUL byte and the UTF-8 bytes of the normalized value. Normalization (`normalize/`) removes format characters (bidi marks, zero-width characters, BOM, soft hyphen) and control characters except TAB, LF and CR, turns CR and CRLF into LF, applies NFC and strips outer ASCII whitespace. There is no per-context salt: under one key, a value gets the same token everywhere.
- The sanitizer mints tokens with `token_for()` in `sanitize/tokens.py`, which normalizes the value and then calls `tokenize()`. A value that cannot be encoded as UTF-8 is rejected with the static code `E_TOKEN_ENCODING`.
- The session key is 32 bytes from `secrets.token_bytes`, kept only in process memory. It is a `SessionKey` object in `types/` that never hands out its bytes, cannot be pickled and has a redacted repr.

---

## Sources

The README lists these sources, with what is visible and what is censored (see [Visible and censored content](#visible-and-censored-content)):

- HTML
- Python (`.py`, `.pyi`)
- Markdown
- Text (`.txt`)
- Logs (`.log`, `.jsonl`, `.ndjson`)
- Databases (`.db`, `.sqlite`, `.sqlite3`)
- Run results (`asfb.run/1`): the output of `Session.run`, which runs pytest and reads only its junit report (`ingest/junit.py`, `sanitize/junit.py`, `schema/run.py`)

---

## Interfaces

The Python API (`asfb.sanitize`, `asfb.sanitize_html`, `asfb.sanitize_text`, `asfb.sanitize_markdown`, `asfb.sanitize_python`, `asfb.Session`), the `asfb` command and the MCP server take HTML, text, markdown, Python, logs and databases. The full description is in the README section *Usage*; this section lists the components.

### Python API

```python
import asfb

doc = asfb.sanitize("page.html", profile="strict")
print(doc.views.censored())
```

`asfb.sanitize(path, *, profile="strict", key=None, isolate=False)` reads a file by its suffix (HTML, text, markdown, Python, log or database). `asfb.sanitize_html(text, *, profile="strict", key=None, isolate=False)` takes a string, as do the other `sanitize_*` functions. Without `key`, each call creates a new session key; pass the same `asfb.SessionKey` to get equal tokens across calls. `profile="diffable"` requires a caller-supplied key. `asfb.Session` defaults to `isolate=True` and adds `edit`, `diff` and `run`. `doc.json` is the emitted JSON of the document's schema (`asfb.doc/1`, `asfb.code/1`, and so on). `doc.views.censored()` is text printed from that JSON: tokens, vocabulary terms and syntax, indented two spaces per level. Errors raise `asfb.AsfbError` with a static code.

### CLI

Package `asfb.cli` (`asfb = "asfb.cli:main"`, also `python -m asfb.cli`). Subcommands: `sanitize`, `keygen`, `serve` (MCP over stdio) and `agents claude` / `agents mcp-config`, which print JSON for Claude Code and write nothing. Each command builds a `Session` with `isolate=True`, except `sanitize --no-isolate`. Errors print the code alone on stderr; usage errors print `E_USAGE` and exit 2. The CLI does not import the vault, the sanitizers, ingest, normalize or the untrusted type.

### MCP server tools

Package `asfb.mcp`: JSON-RPC 2.0 over stdio, written here with no SDK. `server.py` holds the framing (one message per line, at most 4 MiB), strict parsing and the methods. `tools.py` holds the four tools and their argument checks. Both import nothing from the vault, sanitizers, ingest, normalize or the untrusted type; answers are built only from censored views, references, static codes and fixed literals.

| Tool | Purpose |
|---|---|
| `sanitize` | Read a file or list a directory and return the censored view |
| `edit` | Replace one occurrence of old text by new text, written with tokens, in the latest revision |
| `run` | Run pytest on targets under one root; return the censored summary of its junit report |
| `diff` | Compare two stored revisions in their censored views |

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
| Compare two revisions of this session with `diff`; with `diffable` and a key file, tokens stay equal across sessions | Get tokens comparable across sessions with the `strict` profile, or without the same key |

---

## Security design

### Design principles

1. **Allowlist, never blocklist.** Only what is in a versioned public vocabulary (languages, web standards, frameworks such as Bootstrap) passes; everything else is censored. Each vocabulary has an id made of lowercase letters and digits, separated by dots, and an integer version (1 to 65535). A document lists the id and version of each vocabulary it uses, and egress rejects unknown ids, version mismatches and terms that are not in the declared vocabulary. Terms are 1 to 64 printable ASCII characters, without spaces.
2. **Closed output schema.** Pydantic v2 models with `extra="forbid"`. Only enums, bounded integers, booleans, tokens and public vocabulary. No free `str` from the source. The first bucket scheme is `log2`: the index is the bit length of a non-negative integer (0 to 64).
3. **Taint typing.** Source text lives in `Untrusted[str]` and can only become a `Token`, `Bucket` or `EnumValue` through sanitizer functions. Checked with strict mypy; **Python gives no compile-time guarantee**.
4. **Defense in depth.** A single egress gate (`egress.emit()`) revalidates every output against JSON Schema and rejects any string that is neither public vocabulary nor a token. It fails closed, with no partial output.
5. **Process isolation.** Parsing and sanitizing run in a worker process with size, CPU, memory and time limits and a Python audit hook that refuses network, process creation and writes. Only validated JSON crosses the boundary. This is defense in depth, not an OS sandbox; see [Isolation](#isolation-and-platforms).
6. **Local vault.** Originals and the token → original map stay on the machine.
7. **Content-free logs and exceptions.** Static codes, no local variables in tracebacks.
8. **Determinism.** Same input and same key give the same tokens (HMAC-SHA256, 256-bit key, per session by default). A token is `t_` plus the first 16 lowercase hex digits of the HMAC of `asfb.token/1`, a NUL byte and the UTF-8 bytes of the normalized value. Normalization removes format characters (bidi marks, zero-width characters, BOM, soft hyphen) and control characters except TAB, LF and CR, turns CR and CRLF into LF, applies NFC and strips outer ASCII whitespace. There is no per-context salt: under one key, a value gets the same token everywhere. The session key is 32 bytes from `secrets.token_bytes`, kept only in process memory.

### Guarantees

The intended guarantee: **no user content from the source appears in the output, per the schema, and this is verified by tests** (no-leak invariant, canary corpus, property-based tests). It is not a formal proof.

### Limits

- **Not zero information.** Public content, structure, approximate length, frequency and order still reveal information. Quantization and caps reduce this; they do not remove it.
- **Classification errors.** User content wrongly treated as public leaks. The public vocabulary is versioned and allowlist-only to keep this small.
- **No semantics.** No summaries, topic classification, or natural-language or intent detection.
- **Parser bugs.** A bug in a parser or decoder can break a guarantee. That is why there is a single egress gate, and why parsing runs in an isolated worker. `Session`, the CLI and the MCP server run the parser in the worker by default; the Python API does so with `isolate=True`.
- **Parser versions.** HTML is parsed with Python's `html.parser`. Its handling of malformed input differs between Python versions: the same file can give a different tree on 3.12 and 3.14, and an unclosed construct such as an attribute quote can drop the rest of the file.
- **No content certification.** Passing censorship does not mean the source is safe or trustworthy.

### Isolation and platforms

> **Isolation.** Parsing runs in a short-lived worker process (`python -I`, empty temporary
> directory, minimal environment, 60 s wall clock or 15 s for a Python source, one job per process). The worker never receives
> the session key: it tokenizes with a one-time key and the main process re-keys and checks every
> token. On every OS a Python audit hook refuses sockets, process creation, file writes, removals and
> renames, and native library loading. This is a Python-level control, not an OS sandbox: native
> code can bypass it, and the worker can read any file the user can read. During a Python job the hook also refuses `compile` and `exec`, and lifts the refusal when the job ends.
>
> | OS | Kernel-enforced limits |
> |---|---|
> | Linux | CPU 30 s, address space 1 GiB, no file growth (RLIMIT_FSIZE 0), no core files, no new processes for non-root users, `PR_SET_NO_NEW_PRIVS`. No seccomp, no network or mount namespace. |
> | macOS | As Linux without `PR_SET_NO_NEW_PRIVS`; the address-space limit is not enforced. Untested. |
> | Windows | Job Object: 512 MiB per process, 30 s CPU, at most 2 processes, killed when the job closes. No AppContainer: network and writes are blocked only by the audit hook. |
>
> The POSIX limits are tested with fakes only.
>
> **Native code (SQLite).** The database job runs SQLite's C library, which the audit hook cannot see. A memory fault inside it is contained only by the process limits above and by the process boundary; there is no seccomp filter and no namespace. The hook refuses connections to any file and extension loads during that job, which is a Python-level control like the rest.

---

## Evaluation and testing

### Metrics

Three metrics, tracked in CI:

- **Leak rate.** Canary values planted in a test corpus must never appear in any output. Target: zero. Each canary is `cnry_` plus 16 random hex digits, fresh for each test, planted in the templates of `tests/corpus/canary/carriers.json` and `tests/corpus/canary/html_carriers.json`.
- **Task success.** Share of agent tasks solved with the censored view, compared with the original source.
- **Token cost.** Tokens the model spends per task with the censored view, compared with the original source.

Current numbers are in [Results](README.md#results).

### Test layout

```text
tests/
├── unit/
├── property/
├── canary/
├── corpus/
└── parity/                task success and token cost, with and without ASFB
```

### Parity suite

The `tests/parity/` suite fills the first table in the README Results section. Each model solves the same coding tasks once with the original source and once with the ASFB censored view. The suite compares task success and tokens spent.

A model passes when:

- no canary leaks,
- its pass rate with ASFB is at most 5 percentage points below the rate without it,
- its token overhead is at most 20%.

The table is generated by `python -m tests.parity.report` from `tests/parity/results/`; it is not edited by hand. The run with ASFB needs the `asfb` package, which is now available (code support landed in M4). The table is still empty: no model run has been done.

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
├── ARCHITECTURE.md
├── pyproject.toml
├── tools/                 generators run by hand (gen_py_vocab.py, pinned CPython 3.12)
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
    ├── corpus/
    └── parity/                task success and token cost, with and without ASFB
```

---

## Roadmap

- **M0.** Scaffolding, types, egress gate and canary harness. Done.
- **M1.** HTML sanitizer and censored view. Done. Script and style contents are one token each; JavaScript is not censored by name.
- **M2.** Vault, token translation, `edit`, `diff` and `run` (pytest), agent access settings, CLI and MCP server: first end-to-end version, with HTML. Done. `run` reads only the pytest junit report. The parity suite has not been run.
- **M3.** Text and markdown. Done. The markdown scanner is a CommonMark/GFM-like subset.
- **M4.** Python (`asfb.code/1`), with edit and diff by lexeme. Done. Other languages are not started. Known deviation: 2,000 lines take about 1.7 s in process, above the 1 s target. Accepted runtimes are 3.12, 3.13 and 3.14; 3.13 is not tested here.
- **M5.** Logs (`asfb.log/1`). Done. Logs cannot be edited. Other formats are not started.
- **M6.** Databases (`asfb.db/1`). Done, without sample rows. The file limit is 16 MiB, not the 256 MiB first planned.

---

## Requirements

- Python >= 3.12.
- Windows, Linux or macOS.
- Stack: pydantic v2, jsonschema (egress check against the committed JSON Schema), pytest, hypothesis, ruff, mypy `--strict`.

---

## Not specified in the README

- How new text without tokens is introduced into a file: not supported in M2 (see *How the agent reads and writes*).
- Quantization and cap values that reduce information leaks.
- Bucket schemes other than `log2`, such as timestamp bucket width.
- How JavaScript and CSS inside HTML are censored beyond one token per script, style or attribute value.
- Supported model providers for ASFB itself. The parity suite names only the OpenAI, Anthropic and Gemini models.
- Database sample rows, which are not implemented. WAL files are never read; only their presence is recorded.
- Contents of the parity task corpus.
- How the parity suite's offline checks are run apart from the `pytest` command.
