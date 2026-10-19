"""Hard limits for ingest. Enforced in-process; see html.py for what is not isolated."""

MAX_BYTES = 1_048_576  # UTF-8 bytes of the source
MAX_DEPTH = 64  # open elements, counting the element being created
MAX_NODES = 20_000  # every node in the tree, elements and text alike
MAX_ATTRS = 64  # distinct attributes per element after first-wins dedupe
