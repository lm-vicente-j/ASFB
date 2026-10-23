"""Hard limits for ingest. Enforced in-process; see html.py for what is not isolated."""

MAX_BYTES = 1_048_576  # UTF-8 bytes of the source
MAX_DEPTH = 64  # open elements, counting the element being created
MAX_NODES = 20_000  # every node in the tree, elements and text alike
MAX_ATTRS = 64  # distinct attributes per element after first-wins dedupe
MAX_LINES = 20_000  # lines of a plain text source (ingest/text.py)
MAX_MD_BLOCKS = 20_000  # blocks of a markdown source (ingest/markdown.py)
MAX_QUOTE = 16  # nested block quotes
MAX_LIST_DEPTH = 16  # nested list items
MAX_CODE_BYTES = 524_288  # UTF-8 bytes of a Python source: 512 KiB (ingest/code.py)
MAX_CODE_LINES = 20_000  # lines of a Python source (ingest/code.py)
MAX_CODE_LINE = 10_000  # characters of one line of a Python source
MAX_CODE_LEXEMES = 50_000  # lexemes of a Python source, f-strings counted once each
MAX_CODE_TOKEN_BYTES = 65_536  # UTF-8 bytes of the surface of one lexeme
MAX_CODE_INDENT = 100  # nested indentation levels of a Python source
MAX_LOG_BYTES = 16_777_216  # UTF-8 bytes of a log file: 16 MiB (ingest/log.py)
MAX_LOG_RECORDS = 200_000  # entries of a log: JSON objects, plain lines and raw lines alike
MAX_LOG_LINE = 65_536  # UTF-8 bytes of one physical line of a log: 64 KiB
MAX_LOG_FIELDS = 256  # fields of one record, nested fields included
MAX_LOG_NAME = 128  # characters of one field name, nested names included
MAX_LOG_JSON_DEPTH = 16  # nesting levels kept as fields; a deeper object is one token
# SQLite files are sent to the worker as base64 inside one 32 MiB frame, and the worker holds
# the file, its base64 text and a copy in SQLite memory under a 512 MiB job limit on Windows.
MAX_DB_BYTES = 16_777_216  # bytes of a SQLite file: 16 MiB (ingest/db.py)
MAX_DB_TABLES = 1_000  # tables of one database, virtual tables included
MAX_DB_COLUMNS = 2_000  # columns of one table
MAX_DB_INDEXES = 5_000  # indexes of one database, automatic ones included
