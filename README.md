# Agent Secure Filter Bridge (ASFB)

> Show the agent what is public. Censor what is yours.


---

## What it is

ASFB is a library, CLI tool and MCP server, written in Python (>= 3.12), that sits between private content and a coding agent.

It reads HTML, Python source files, .txt files, databases, markdown files, logs and test run reports, and converts them into a secure representation for the agent, in a versioned, closed schema. In this representation, all free-form content (text, attribute values, URLs, class names, multimedia content) is replaced by:

- Keyed pseudonyms (HMAC tokens, hexadecimal strings)

The ASFB workflow:

Agent -> ASFB -> ToolCalling -> Output -> ASFB -> Agent

### Problems it addresses

- **Exposure to sensitive or offensive content.** The model never receives the source text.
- **Prompt injection.** No natural-language text from the source reaches the model, so instructions embedded in the source have no direct vehicle.
- **Reuse.** The layer is independent of any specific project and can be used by other agents or workflows that consume untrusted projects.

---

## Censor, not obfuscate

ASFB does not hide the source. Public content stays visible: HTML tags, language keywords, standard and framework APIs, and public classes such as Bootstrap's `btn btn-primary`. Only user content is censored: text, strings, identifiers you defined, file names, values, URLs and personal data. In JavaScript (not supported yet, planned), the agent would see `fetch` but read `t_4a7d…` instead of `sendInvoice`. Python is supported (M4).

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

**M1 note.** The HTML sanitizer turns the whole content of `<script>` and `<style>` into one token, so today the script line reads `<script>t_…</script>`, and `email` is not linked between the form and the script. Censoring JavaScript names and strings, with `fetch` visible, is not implemented: code support covers Python only (M4).

---

## Installation

Requirements: Python 3.12 or later, and git.

```bash
git clone https://github.com/lm-vicente-j/ASFB.git
cd ASFB
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
asfb --help
```

On Windows PowerShell, activate with `.venv\Scripts\Activate.ps1` and use `python` instead of `python3`.

- `pip install -e .` installs the `asfb` command and its two runtime dependencies, pydantic and jsonschema. `pyproject.toml` defines no optional extras.
- The development tools (pytest, hypothesis, ruff, mypy) are in the `dev` dependency group: `pip install --group dev` (pip 25.1 or later).
- To verify the install, `asfb --help` must list the commands `sanitize`, `keygen`, `serve` and `agents`.

**Platforms.** Linux is the best-verified platform. Windows and macOS have not been tested: the install is expected to work, but the process isolation limits described in *Guarantees and limits* have not been checked there.

---

## Quick start

Save this as `site/index.html` to follow the steps below:

```html
<!DOCTYPE html>
<html>
<head><title>Invoices</title></head>
<body>
<h1>Welcome, Ana García</h1>
<form class="send-invoice" action="/invoices/send">
  <input type="email" name="email" value="ana@example.com">
  <button type="submit" class="btn btn-primary">Send invoice</button>
</form>
</body>
</html>
```

### 1. Create a key

```bash
mkdir -p ~/.asfb
asfb keygen ~/.asfb/key
```

`keygen` prints nothing and never overwrites an existing file. Keep the key outside every folder ASFB protects: a key file inside a root is refused (`E_KEY_FILE`). Without `--key-file`, each run uses a new random key, so tokens change from one run to the next.

### 2. Sanitize a file and read the censored view

```bash
asfb sanitize site/index.html --key-file ~/.asfb/key
```

The output keeps the public parts (tags, `type="email"`, `btn btn-primary`) and replaces the user content (the heading, the form action, the field name, the value and the button text) with tokens such as `t_…`. The same value gets the same token under the same key. Add `--json` to print the emitted JSON instead. Given a directory, `asfb sanitize site --key-file ~/.asfb/key` prints a listing of its documents.

### 3. Use the Python API

```python
from pathlib import Path

import asfb

key = asfb.SessionKey.from_file(Path.home() / ".asfb" / "key")
session = asfb.Session(["site"], profile="strict", key=key)  # isolate=True by default
ref = session.sanitize("index.html")
print(ref.ref)  # d_…@1
print(ref.view)  # the censored view
```

`asfb.sanitize(path)` runs in the current process unless you pass `isolate=True`. For files you do not trust, use `Session` or `isolate=True`.

### 4. Start the MCP server and register it in your harness

To start the server by hand (it waits for JSON-RPC messages on stdin):

```bash
asfb serve --root "$PWD/site" --key-file ~/.asfb/key
```

To register it, print the MCP configuration and save it as the project's `.mcp.json`. Use absolute paths, because relative paths are written as given:

```bash
asfb agents mcp-config --root "$PWD/site" --key-file ~/.asfb/key > .mcp.json
```

Claude Code reads `.mcp.json` from the project root. For other harnesses, copy the `mcpServers` entry into their MCP settings. The server exposes four tools: `sanitize`, `edit`, `run` and `diff`.

### 5. Block direct reads of the originals (required)

The censored view protects nothing if the agent can still open the original files with its own tools.

```bash
asfb agents claude --root "$PWD/site" --key-file ~/.asfb/key
```

This prints Claude Code settings with `permissions.deny` rules and a `PreToolUse` hook, and writes nothing. Merge them into `.claude/settings.json` of the project. The hook blocks reads, edits and shell commands that touch the protected paths. It is a best-effort check, not an OS sandbox; see *Guarantees and limits*.

### Prompt for an agent

To have any agent install and use ASFB, paste this:

```text
Install and use ASFB (https://github.com/lm-vicente-j/ASFB) to work with protected documents.
1. Clone the repository and install it in a virtualenv with `pip install -e .`.
2. Read the "Quick start" section of its README.md and follow it.
3. Read, edit, run and diff protected files only through the ASFB tools (sanitize, edit, run, diff).
4. Never open the original protected files directly, with any tool, search or shell command.
5. Work with the tokens as they are; do not try to recover the original text.
6. If ASFB is missing or fails, stop and report it instead of using the originals.
```

**Not verified.** Windows and macOS have not been tested. The MCP server was checked with the official Python MCP SDK client over stdio, not with a real model. The hook was checked with simulated tool calls, not inside Claude Code. The steps have not been run by an agent.

---

## How the agent reads and writes

- **Read.** User content becomes tokens. The agent reads `t_8ec1d2a47b9f3e60` instead of `send-invoice`.
- **Edit.** `edit(ref, old, new)` replaces exactly one occurrence of `old` by `new` in the latest revision of a stored file, and returns the new censored revision (`d_…@N+1`). Only files already in the session can be edited. Rules, checked before the disk is touched:
  1. `old` must not be empty and must differ from `new`; each at most 65,536 UTF-8 bytes.
  2. Tokens (`t_` plus 16 hex digits) are translated back to their surface. Unknown tokens are rejected (`E_EDIT_UNKNOWN_TOKEN`). A token with several possible surfaces is resolved by trying the combinations; if more than one matches, `E_TOKEN_AMBIGUOUS`.
  3. Literal text may use only printable ASCII, tab and newline, and its words must be public vocabulary terms (or punctuation between them). `old="Ana García"` is refused (`E_EDIT_INVALID_TEXT`), so `edit` cannot be used to probe whether a secret occurs in a file. **Introducing new free text is not supported in this version**: the agent can only rearrange tokens and public words.
  4. In HTML, a surface that contains `& < > " '` cannot be inserted (`E_EDIT_INVALID_TEXT`). Markdown and text have no such restriction.
  5. `old` must occur exactly once (`E_EDIT_NO_MATCH` or `E_EDIT_AMBIGUOUS` otherwise). The file must still match the digest read at the last sanitize (`E_EDIT_STALE`).
  6. The new content is censored before writing, and the write is atomic. On any failure the file is unchanged.
  7. Python sources (`.py`, `.pyi`) use lexeme rules instead of text matching: see *The Python sanitizer (M4)*. Their `old` must still match exactly once. Bytes outside the edited range, including CRLF and dropped control characters, are written as they were read; a range that touches or splits a control character is refused.
  Known residual leak: the agent can learn whether a sequence of public words and punctuation occurs zero, one or several times in a file, and whether a token's surface contains markup characters. In Python, an `old` that names an identifier or a literal shows whether that name or literal occurs in the file.
- **Run.** `run(tool, targets)` executes an allowed test tool (`pytest` only in this version) on paths under one root. Targets are paths, not test ids. Console output is discarded; only the junit report is read, parsed and censored. Test names, classes, parameters, exception types and messages become tokens; `AssertionError`-style exception names stay visible; tracebacks, captured output and properties are never read. A report that is missing, too large, not UTF-8 or malformed is dropped: the result says `report=dropped` and carries no cases. A run that exceeds `run_timeout` (default 300 s, 1 to 3600) is killed with its whole process tree and reported as `exit=timeout`. The tool runs with the user's permissions and its writes are not restricted. On Linux the tool runs in an empty network namespace (`unshare`), when that is available; otherwise `run` refuses with `E_RUN_NETWORK` unless the session was created with `run_network="host"`, which lets the tool use the network.
- **Diff.** `diff(a, b)` compares two stored revisions, which may be different documents, line by line in their censored views, and returns a unified diff without context. It works with any profile inside one session. When both revisions are Python sources, the comparison is by lexeme: the hunks index lexemes and the diff says `units: lexeme`.
- **Single access path.** The agent must not read protected files directly. ASFB ships agent settings (for Claude Code: permission deny rules and a `PreToolUse` hook) that block direct reads, edits and shell commands on protected paths, so the MCP server is the only way in.

Originals and the token → original map never leave the machine.

---

## Sources

| Source | Visible (public) | Censored (user content) |
|---|---|---|
| HTML | Tags, attribute names, standard attribute values, public framework classes (e.g. Bootstrap), URL schemes and URL shape | Text, custom element, attribute, class and id names, URL hosts, paths, queries and fragments, form values, comments, script and style contents |
| Python (`.py`, `.pyi`) | Keywords, soft keywords, builtins, standard library names, operators, the numbers 0 and 1, indentation depth and line numbers, string prefix classes | User-defined names (third-party names too, in this version), other numbers, strings and docstrings, f-strings whole, comments, file names |
| Markdown (`.md`, `.markdown`) | Heading levels, list kinds and depth, task marks, table shape and alignment, code fences and public language names, block quote depth, rules, link and image shape (scheme, host and segment flags) | Text, code, URL hosts, segments, queries and fragments, link and image titles, alt text, labels, embedded HTML, front matter, non-public language names |
| Text (`.txt`) | Paragraphs, line endings and indentation width | Every line |
| Logs (`.log`, `.jsonl`, `.ndjson`) | Levels (canonical), standard field names, `true`, `false` and `null`, number and time buckets (log2), record order, the count of raw lines | Messages, loggers, values, custom field names, traces, exact times (never shown, not even as digits), exception names that are not standard |
| Databases (`.db`, `.sqlite`, `.sqlite3`) | Table, column, index, view and trigger structure, declared type names from a closed list, affinities, foreign keys, row count and page size buckets, whether a WAL file exists | Names (except SQLite's own tables), the SQL text of views, triggers, checks and defaults, values, comments |
| Run results (`asfb.run/1`, from `Session.run`, pytest only) | The tool name, the exit status, the totals (tests, failures, errors, skipped), and each case's outcome | Suite, class and test names and parameters (tokens); tracebacks and console output are never kept |

The HTML sanitizer (M1) runs ingest, normalize, sanitize, schema and egress, in that order:

- **Input.** One `.html` or `.htm` file, or a string. UTF-8 only. At most 1,048,576 bytes, 64 levels of element nesting, 20,000 nodes and 64 attributes per element; for a repeated attribute the first one wins. Anything larger is refused with a static error code.
- **Public terms.** Known element and attribute names, listed values of enumerated attributes (such as `type`, `method`, `rel`, `dir` and `role`), URL schemes and Bootstrap classes stay visible. They come from the vocabularies `html.elements`, `html.attributes`, `html.values`, `url.schemes` and `css.bootstrap` (a curated subset of Bootstrap 5.3 classes), all version 1, in `src/asfb/vocab/data/`. Enumerated values match exactly or ASCII case-insensitively.
- **Tokens.** Other element and attribute names (custom elements, `data-*`) become tokens. Each text node, comment, CDATA section, declaration and processing instruction becomes one token, and so does the whole text of `<script>`, `<style>`, `<title>` and `<textarea>`. Whitespace-only text is dropped.
- **Attribute values.** `class`, `id`, `name`, `for`, `headers` and similar attributes get one token per space-separated part, so `for="email"` links to `id="email"`; Bootstrap classes stay visible. Other values, including `style`, `srcset` and `on*` handlers, become one token. An attribute with no value or an empty value prints bare.
- **URLs.** In `href`, `src`, `action`, `formaction`, `cite`, `poster`, `data` and `background`, an `http`, `https`, `ws`, `wss`, `ftp`, `file` or scheme-less URL keeps its shape: the host, each path segment, the query and the fragment become one token each, as in `/t_…/t_…?t_…#t_…`. For `mailto`, `tel`, `data`, `javascript` and `blob`, everything after the scheme is one token. Any other URL is one token.
- **Other.** The doctype only shows whether it is `<!DOCTYPE html>`. Character references are decoded before tokenizing.

### The text and markdown sanitizers (M3)

Plain text (`.txt`) and markdown (`.md`, `.markdown`) follow the same pipeline as HTML: ingest, normalize, sanitize, schema and egress. Both are read by the isolated worker by default, like HTML. Neither adds a dependency: the markdown scanner is ASFB's own, a CommonMark/GFM-like subset.

- **Input.** UTF-8 only, at most 1,048,576 bytes. Text: at most 20,000 lines. Markdown: at most 20,000 blocks, 20,000 lines, 1,024 inlines per line, 64 table columns and 10,000 table rows, with block quotes and list items nested at most 16 levels deep. Anything larger is refused with a static code (`E_TEXT_LINES`, `E_MD_BLOCKS`, `E_MD_DEPTH`, `E_SOURCE_SIZE`). A line with more than 1,024 inlines is kept as one text token.
- **Text.** Each non-blank line is one token. Blank lines separate paragraphs. Visible: the paragraph structure, the line ending kind (`lf`, `crlf`, `cr`, `mixed` or `none`) and each line's indentation width (spaces, and four per tab, capped at 64). A line that holds only invisible characters is blank.
- **Markdown.** Visible: heading levels, list kinds, depth and task marks, table alignment, code fences, block quote depth, rules, and the shape of links and images. A code fence's language is visible only when it is a public term of `md.languages`; any other word is a token. Censored: text, code, URL hosts, path segments, queries and fragments (as in HTML), link and image titles, alt text, link labels, embedded HTML and front matter.
- **Entities and escapes.** Markdown entities and escapes are not decoded: `&#99;` and `\*` stay as written in the surface. Embedded HTML is one token.
- **Public terms.** `md.languages` (version 1: common programming and markup language names, such as `python`, `js`, `json`, `yaml`, `text`) and `url.schemes` (version 1, shared with HTML). They are in `src/asfb/vocab/data/`.
- **Vocabulary versions.** Any change to the terms of a vocabulary raises its `version` by one. The registry loads one version per id, and egress refuses a document that declares another version.
- **API and suffixes.** `asfb.sanitize_text(text)` and `asfb.sanitize_markdown(text)` take strings; `asfb.sanitize(path)` accepts `.html`, `.htm`, `.txt`, `.md`, `.markdown`, `.py` and `.pyi`. `Session` takes the same suffixes.

### The Python sanitizer (M4)

Python (`.py`, `.pyi`) runs ingest, normalize, sanitize, schema and egress like the other sources. The lexer is the stdlib `tokenize` module, run in the isolated worker. The package never calls `ast`, `compile` or `exec` on source text.

- **Input.** UTF-8 only: a source whose encoding cookie or BOM names another encoding is refused with `E_CODE_ENCODING_UNSUPPORTED`, and so is a file that is not UTF-8. Limits: 524,288 bytes (512 KiB), 20,000 lines, 10,000 characters per line, 50,000 lexemes (an f-string counts once), 64 KiB per lexeme and 100 levels of indentation (the stdlib tokenizer may stop earlier). Anything larger is refused with a static code (`E_CODE_TOO_LARGE`, `E_CODE_LINE_TOO_LONG`, `E_CODE_TOO_MANY_LEXEMES`, `E_CODE_INDENT_TOO_DEEP`, `E_CODE_TOKENIZE_FAILED`).
- **Normalization.** Tab, LF, CR and form feed stay. Other control characters and all format characters (bidi marks, zero-width characters, BOM) are removed before lexing, and the document records how many were removed. CRLF and CR become LF.
- **Schema.** The output is `asfb.code/1`, a closed model committed as `asfb.code-1.schema.json`. It holds the profile, the declared vocabularies (always the four `py.*` ones), the file token and extension, the count of removed characters and the lexemes.
- **Lexemes.** The output is a flat list. Each lexeme has its line, a closed kind and a value: `keyword`, `builtin`, `stdlib`, `exception`, `op`, `name`, `number`, `string`, `comment`, `newline`, `indent` and `dedent`. Indent and dedent carry the depth. A string carries its prefix class (`str`, `bytes`, `fstring` or `raw`). The original spacing is not kept: the view prints the lexemes with one space between them and four spaces per depth.
- **Public terms.** `py.keywords` (keywords and soft keywords), `py.builtins`, `py.stdlib` (module names and public attributes of about 45 common modules) and `py.exceptions`, `py.keywords` and `py.builtins` at version 1, `py.stdlib` and `py.exceptions` at version 2, in `src/asfb/vocab/data/`. A name is public only when it equals a term exactly (no Unicode folding, so a Cyrillic look-alike of `def` is a token). The operators are a closed list of the 48 tokenizer operators, and the numbers 0 and 1 are public.
- **Tokens.** Every other name, number, string (docstrings included), comment body and file name becomes one token. The comment's `#` stays visible. An f-string is one token from its first to its last character: its literal parts and its interpolated expressions alike, so no name inside `{…}` appears.
- **File names.** A file name's stem is a token; its extension (`py` or `pyi`) is public.
- **Runtimes.** The tokenizers of Python 3.12, 3.13 and 3.14 are accepted; any other runtime refuses the source with `E_CODE_TOKENIZE_FAILED`. The f-string tokens differ between the tokenizers, but spans come from the tokenizer's positions, so the output does not depend on them.
- **Worker.** During a code job the audit hook also refuses `compile` and `exec` events, and it lifts the refusal when the job ends.
- **Speed.** Egress checks every emitted lexeme against the JSON Schema with a checker compiled once per process, and a sanitize checks the document twice (emit and render). Measured in development (CPython 3.14, a shared machine): about 1.7 seconds in process for 2,000 lines (14,895 lexemes), about 5 seconds for 50,000 lexemes, and about 2.9 and 8.8 seconds for the same two inputs in the isolated worker. The one-second target for 2,000 lines is not met; this is a known deviation, see *Python source policies* in ARCHITECTURE.md.
- **Edit and diff.** See the rules in *How the agent reads and writes*. A new identifier must be ASCII and at most 32 characters, and it is recorded as introduced by the model.
- **Vocabulary provenance.** The four `py.*` vocabularies are generated by `tools/gen_py_vocab.py` from CPython 3.12.15, the only interpreter it accepts. Each file records that version, its vocabulary version and the sha256 of its terms. Dunder names and names with a leading underscore are never public, and names that only later releases add are excluded. The byte-for-byte test reproduces the committed files when a 3.12.15 interpreter is found (`ASFB_PY312`, or the uv default).

### The log sanitizer (M5)

Logs run ingest, normalize, sanitize, schema and egress like the other sources, and the isolated worker reads them by default. Plain text is `.log`; JSON Lines is `.jsonl` or `.ndjson`. The JSON is parsed with the stdlib `json` module; no dependency is added.

- **Input.** UTF-8 only (`E_LOG_ENCODING_UNSUPPORTED` otherwise). Each limit refuses the whole file with a static code: 16 MiB (`E_LOG_TOO_LARGE`), 200,000 records (`E_LOG_TOO_MANY_RECORDS`), 64 KiB per physical line (`E_LOG_LINE_TOO_LONG`), 256 fields per record counting nested ones (`E_LOG_TOO_MANY_FIELDS`) and 128 characters per field name (`E_LOG_FIELD_NAME_TOO_LONG`). A JSON object nests as fields up to 16 levels; a deeper object is one token.
- **Records.** In JSON Lines each non-blank line is a record. A line that is not one JSON object (broken JSON, an array, `NaN` or a duplicate key) is a raw record: one token of the whole line, with no error. In plain text a line starting with an ISO 8601 timestamp, a level word, or both, is a header; a line that starts with a space or a tab continues the record before it, so a traceback is one token; any other line is a raw record. A stamp that is not a valid date and time makes its line raw.
- **Levels.** A level is shown in the upper case of `log.levels`, so `warn` is `WARN`. Only ASCII letters are folded. In JSON, `level` or `severity` takes a level word or a syslog digit from 0 to 7. Any other value is a token.
- **Time.** No instant is ever shown. A record with a time carries the log2 bucket of its delta in milliseconds from the previous timed record, and a `backwards` flag when the order steps back. A delta of one second is index 10. The first timed record has no time. In JSON, `timestamp`, `time` and `ts` are read as the time, and a second time field is dropped; an unreadable time value is a token.
- **Numbers.** A number shows its sign and the log2 bucket of its absolute value, saturating at 64. Booleans and `null` are shown as they are.
- **Tokens.** Each message, logger, string value, array (one token of its JSON text) and deep object is one token. A message is never split into words, and a name that is not a standard field name is a token. An `exc_type` value is public only when it is a `py.exceptions` term.
- **Vocabularies.** `log.levels` (version 1: the syslog and named levels, and the digits 0 to 7) and `log.fields` (version 1: the standard field names, such as `timestamp`, `level`, `msg`, `message`, `logger`, `exc_type` and `trace_id`). They are in `src/asfb/vocab/data/`.
- **Edit.** Not supported: a log is evidence. `edit` refuses with `E_EDIT_UNSUPPORTED_KIND`.
- **Diff.** Two logs are compared record by record. A record's unit is its whole censored line, so a change in a field or a time bucket shows as a change, not only a change in level or message.
- **Speed.** Measured once in development (CPython 3.14) on a 10,000-line log of text and JSON records: about 3.4 seconds in process and 8.7 seconds isolated, with the same output. Most of the time goes to the egress gate and to normalizing each token.
- **API and suffixes.** `asfb.sanitize(path)` accepts `.log`, `.jsonl` and `.ndjson`; `Session` takes the same suffixes.

### The database sanitizer (M6)

SQLite files (`.db`, `.sqlite`, `.sqlite3`) run ingest, sanitize, schema and egress, but the file is always read in the isolated worker, whatever `isolate` says, because it is hostile input for SQLite's native code. The client checks the header `SQLite format 3` and the size before it reads the bytes.

- **Reading.** The worker loads the bytes into an in-memory connection with `deserialize`. SQLite never receives a path, so no journal, WAL, shared memory or temporary file is written. Before the first read the connection is hardened: query-only, trusted schema off, defensive mode where the runtime has it, temporary storage in memory, no `ATTACH` and no extensions. An authorizer allows only reads of the schema, the table, index and foreign key pragmas, `count()`, and `quick_check` when no virtual table exists. A progress budget bounds every query. SQLite's messages are never kept: a failure is a static code.
- **Virtual tables** are listed by name, with no columns and an unknown row count. Connecting one runs module code, so none is ever connected.
- **WAL.** A `-wal` file next to the database is never read; the model records only that it exists (`wal_present`). A database whose header declares WAL mode, without its WAL file, cannot be read and gives `E_DB_CORRUPT`.
- **Names.** Table, column, index, view and trigger names are tokens. The only public names are SQLite's internal tables (`sqlite_sequence`, `sqlite_stat1` to `sqlite_stat4`, `sqlite_schema`), exactly. A declared type is public when its base name (before any parenthesis) is in `db.sqltypes`. Affinities are computed with SQLite's five rules and are public.
- **Counts.** A row count is a log2 bucket, or `unknown` for a virtual table or a count that ran out of budget. The page size is a bucket.
- **Not shown.** Values, the SQL text of views, triggers, checks and defaults (only their presence), comments and generated-column expressions.
- **Limits.** 16 MiB per file (`E_DB_TOO_LARGE`), 1000 tables, 2000 columns per table and 5000 indexes (`E_DB_LIMIT_EXCEEDED`). Other refusals: `E_DB_NOT_SQLITE`, `E_DB_CORRUPT`, `E_DB_UNSUPPORTED_RUNTIME` (the runtime's `sqlite3` lacks the calls the reader needs; the package requires 3.12, so this does not occur on a supported interpreter), `E_DB_DENIED` and `E_DB_BUDGET_EXCEEDED`.
- **Edit.** Not supported: a database is evidence. `edit` refuses with `E_EDIT_UNSUPPORTED_KIND`.
- **Diff.** Two databases of one session are compared schema line by schema line. Adding a column to a copy shows one inserted line.
- **Not yet.** Sample rows are not implemented. The file limit is 16 MiB, not the 256 MiB first planned: the worker frame carries the file as base64 under a 32 MiB cap, and the worker's memory limit is 512 MiB on Windows.

---

## What an agent can and cannot do with the output

| Can | Cannot |
|---|---|
| Read all public content: tags, keywords, standard APIs, structure | Read user content: text, strings, user-defined names, values, personal data |
| Follow references: under one key, the same value always gives the same token | Recover an original from its token |
| Edit files and call tools using tokens; ASFB translates them back | Receive natural-language instructions from the source |
| Compare two revisions of this session with `diff`; with `diffable` and a key file, tokens stay equal across sessions | Get tokens comparable across sessions with the `strict` profile, or without the same key |

---

## Guarantees and limits

The intended guarantee: **no user content from the source appears in the output, per the schema, and this is verified by tests** (no-leak invariant, canary corpus, property-based tests). It is not a formal proof.

Limits:

- **Not zero information.** Public content, structure, approximate length, frequency and order still reveal information. Quantization and caps reduce this; they do not remove it.
- **Classification errors.** User content wrongly treated as public leaks. The public vocabulary is versioned and allowlist-only to keep this small.
- **No semantics.** No summaries, topic classification, or natural-language or intent detection.
- **Parser bugs.** A bug in a parser or decoder can break a guarantee. That is why there is a single egress gate, and why parsing runs in an isolated worker. `Session`, the CLI and the MCP server run the parser in the worker by default; the Python API does so with `isolate=True`.
- **Python lexer.** The stdlib tokenizer decides what a lexeme is. It is accepted on 3.12, 3.13 and 3.14 only, and its behaviour on other versions is not tested. A name that is not in a public vocabulary is a token, so a public word that is also a user's name (for example `match`, `type` or `path`) shows as public. Its shape (the number of lines, indentation, lexemes and string prefixes) stays visible.
- **Parser versions.** HTML is parsed with Python's `html.parser`. Its handling of malformed input differs between Python versions: the same file can give a different tree on 3.12 and 3.14, and an unclosed construct such as an attribute quote can drop the rest of the file.
- **No content certification.** Passing censorship does not mean the source is safe or trustworthy.

> **Isolation.** Parsing runs in a short-lived worker process (`python -I`, empty temporary
> directory, minimal environment, 60 s wall clock or 15 s for a Python source, one job per process). The worker never receives
> the session key: it tokenizes with a one-time key and the main process re-keys and checks every
> token. On every OS a Python audit hook refuses sockets, process creation, file writes, removals and
> renames, and native library loading. This is a Python-level control, not an OS sandbox: native
> code can bypass it, and the worker can read any file the user can read.
>
> | OS | Kernel-enforced limits |
> |---|---|
> | Linux | CPU 30 s, address space 1 GiB, no file growth (RLIMIT_FSIZE 0), no core files, no new processes for non-root users, `PR_SET_NO_NEW_PRIVS`. No seccomp, no network or mount namespace. |
> | macOS | As Linux without `PR_SET_NO_NEW_PRIVS`; the address-space limit is not enforced. Untested. |
> | Windows | Job Object: 512 MiB per process, 30 s CPU, at most 2 processes, killed when the job closes. No AppContainer: network and writes are blocked only by the audit hook. |
>
> The POSIX limits are tested with fakes only.

---

## Evaluation

Three metrics, tracked in CI:

- **Leak rate.** Canary values planted in a test corpus must never appear in any output. Target: zero. Each canary is `cnry_` plus 16 random hex digits, fresh for each test, planted in the templates of `tests/corpus/canary/carriers.json` and `tests/corpus/canary/html_carriers.json`.
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

`python -m tests.parity.report` generates this table from `tests/parity/results/`; do not edit it by hand. A model passes when no canary leaks, its pass rate with ASFB is at most 5 percentage points below the rate without it, and its token overhead is at most 20%. The run with ASFB needs the `asfb` package, which is now available (code support landed in M4). The table is still empty: no model run has been done.

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


## Usage

Three entry points share one `Session`: the Python API, the `asfb` command and the MCP server. `Session`, the `asfb` command and the MCP server isolate parsing in a worker process by default. The module-level functions (`asfb.sanitize` and the others) run in process unless you pass `isolate=True`, except databases, which always go to the worker.

### Python API

```python
import asfb

doc = asfb.sanitize("page.html", profile="strict")  # isolate=False by default here
print(doc.views.censored())

session = asfb.Session(["./site"], profile="strict", isolate=True)
ref = session.sanitize("index.html")  # d_…@1 and its view
after = session.edit(str(ref.ref), "t_…", "t_…")  # one occurrence, token by token
print(session.diff(str(ref.ref), str(after.ref)).view)
print(session.run("pytest", ["tests"]).view)
```

- `asfb.sanitize(path)`, `asfb.sanitize_html(text)`, `asfb.sanitize_text(text)`, `asfb.sanitize_markdown(text)` and `asfb.sanitize_python(text)` (library functions, in process unless `isolate=True`) and `asfb.Session(roots, *, profile="strict", key=None, python=None, run_timeout=300, isolate=True, run_network="isolate")` (defaults isolate). Use `Session` or `isolate=True` for untrusted content: the library default is kept for callers that already handle the content.
- `Session.sanitize(target)` takes a document id (`d_…` or `d_…@N`), `.` for the first root, or a root-relative path whose segments may be tokens. A file yields its id and view; a directory yields a listing (one line per document). The vault is memory only, per session.
- `Session.edit(ref, old, new)`, `Session.run(tool, targets)` and `Session.diff(a, b)` follow the rules in *How the agent reads and writes*.
- Without `key`, each session creates a 32-byte key kept only in process memory. `profile="diffable"` needs a key: pass `asfb.SessionKey.from_file(path)`, a file made with `asfb keygen PATH` (64 hex digits, mode 0600 on POSIX). Keys are never printed, exported to the worker, or written anywhere else.
- Errors raise `asfb.AsfbError` with a static code (`E_…`). Nothing from the source appears in messages.

### Command line

| Command | Effect |
|---|---|
| `asfb sanitize TARGET [--root R…] [--profile strict\|diffable] [--key-file F] [--json] [--no-isolate]` | Print the censored view of a file, or the listing of a directory. Without `--root`, the root is `TARGET` itself (a directory) or its directory (a file). `--json` prints the emitted JSON. `--no-isolate` is for diagnosis only. |
| `asfb keygen PATH` | Create a new key file (mode 0600, never overwrites). Prints nothing. |
| `asfb serve --root R… [--profile] [--key-file F] [--python P] [--run-network isolate\|host] [--run-timeout N]` | MCP server over stdio. |
| `asfb agents claude --root R… [--key-file F] [--python P]` | Print the Claude Code settings JSON (deny rules and the hook). Writes nothing. |
| `asfb agents mcp-config --root R… [--profile] [--key-file F] [--python P] [--run-network …]` | Print the `.mcp.json` that starts `asfb serve`. |

There is no configuration file: everything is a flag. Errors print only the static code on stderr and exit 1; wrong usage prints `E_USAGE` and exits 2. A key file inside a root is refused (`E_KEY_FILE`). In `sanitize` with `--json` on a directory, the failed documents go to stderr so stdout stays one JSON document.

### MCP server

Transport: stdio, JSON-RPC 2.0, one message per line (UTF-8, at most 4 MiB), requests answered in order. Methods: `initialize` (protocol versions 2025-06-18, 2025-03-26 and 2024-11-05), `ping`, `tools/list` and `tools/call`. Notifications get no reply. Unknown methods return `-32601`, invalid JSON `-32700` (with `id: null`), invalid requests and batches `-32600`, bad `tools/call` parameters `-32602`. Error messages are fixed strings. The server never writes to stderr.

| Tool | Arguments | Returned text |
|---|---|---|
| `sanitize` | `target` (string, at most 4096 bytes) | A file: `d_…@N` on the first line, then the view. A directory: the listing view, then `d_… E_CODE` lines for failed files. |
| `edit` | `doc` (`d_…@N`), `old`, `new` (strings, at most 65,536 bytes) | `d_…@M` and the new view |
| `run` | `tool` (`"pytest"`), `targets` (1 to 32 paths, default `["."]`) | The run summary view (`asfb.run/1`) |
| `diff` | `a`, `b` (`d_…@N`) | The unified diff view |

Each `tools/call` returns `{"content": [{"type": "text", "text": T}], "isError": false}`. A failure returns the static code as the text with `"isError": true`. Arguments with the wrong type, length, pattern or extra fields give `E_MCP_ARGS`. An unexpected exception gives `E_INTERNAL`. No argument is repeated in an answer.

## Design principles

1. **Allowlist, never blocklist.** Only what is in a versioned public vocabulary (languages, web standards, frameworks such as Bootstrap) passes; everything else is censored. Each vocabulary has an id made of lowercase letters and digits, separated by dots, and an integer version (1 to 65535). A document lists the id and version of each vocabulary it uses, and egress rejects unknown ids, version mismatches and terms that are not in the declared vocabulary. Terms are 1 to 64 printable ASCII characters, without spaces.
2. **Closed output schema.** Pydantic v2 models with `extra="forbid"`. Only enums, bounded integers, booleans, tokens and public vocabulary. No free `str` from the source. The first bucket scheme is `log2`: the index is the bit length of a non-negative integer (0 to 64).
3. **Taint typing.** Source text lives in `Untrusted[str]` and can only become a `Token`, `Bucket` or `EnumValue` through sanitizer functions. Checked with strict mypy; **Python gives no compile-time guarantee**.
4. **Defense in depth.** A single egress gate (`egress.emit()`) revalidates every output against JSON Schema and rejects any string that is neither public vocabulary nor a token. It fails closed, with no partial output.
5. **Process isolation.** Parsing and sanitizing run in a worker process with size, CPU, memory and time limits and a Python audit hook that refuses network, process creation and writes. Only validated JSON crosses the boundary. This is defense in depth, not an OS sandbox; see [Guarantees and limits](#guarantees-and-limits).
6. **Local vault.** Originals and the token → original map stay on the machine.
7. **Content-free logs and exceptions.** Static codes, no local variables in tracebacks.
8. **Determinism.** Same input and same key give the same tokens (HMAC-SHA256, 256-bit key, per session by default). A token is `t_` plus the first 16 lowercase hex digits of the HMAC of `asfb.token/1`, a NUL byte and the UTF-8 bytes of the normalized value. Normalization removes format characters (bidi marks, zero-width characters, BOM, soft hyphen) and control characters except TAB, LF and CR, turns CR and CRLF into LF, applies NFC and strips outer ASCII whitespace. There is no per-context salt: under one key, a value gets the same token everywhere. The session key is 32 bytes from `secrets.token_bytes`, kept only in process memory.

---

## Roadmap

- **M0.** Scaffolding, types, egress gate and canary harness. Done.
- **M1.** HTML sanitizer and censored view. Done. Script and style contents are one token each; JavaScript is not censored by name.
- **M2.** Vault, token translation, `edit`, `diff` and `run` (pytest), agent access settings, CLI and MCP server: first end-to-end version, with HTML. Done. `run` reads only the pytest junit report. The parity suite has not been run, so the Results table is empty.
- **M3.** Text and markdown. Done. The markdown scanner is a CommonMark/GFM-like subset, not a full parser.
- **M4.** Python (`asfb.code/1`), with edit and diff by lexeme. Done. Other languages are not started. Known deviation: 2,000 lines take about 1.7 s in process, above the 1 s target. Accepted runtimes are 3.12, 3.13 and 3.14; 3.13 is not tested here.
- **M5.** Logs (`asfb.log/1`). Done. Logs cannot be edited. Other formats are not started.
- **M6.** Databases (`asfb.db/1`, SQLite). Done, without sample rows. The file limit is 16 MiB, not the 256 MiB first planned.

---

## Requirements

- Python >= 3.12.
- Windows, Linux or macOS.
- Stack: pydantic v2, jsonschema (egress check against the committed JSON Schema), pytest, hypothesis, ruff, mypy `--strict`.

---

## Repository layout

```text
ASFB/
├── README.md
├── ARCHITECTURE.md
├── pyproject.toml
├── tools/                     generators run by hand (gen_py_vocab.py)
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