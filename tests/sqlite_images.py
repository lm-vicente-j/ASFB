"""Build SQLite database images in memory for the tests: the bytes of a database, never a path."""

import sqlite3

SAMPLE_SCHEMA = """
CREATE TABLE clientes (
  id INTEGER PRIMARY KEY,
  nombre TEXT NOT NULL DEFAULT 'anon',
  email VARCHAR(255) UNIQUE,
  ref INT REFERENCES clientes(id)
);
CREATE INDEX idx_email ON clientes(email);
CREATE VIEW vista AS SELECT id FROM clientes;
CREATE TRIGGER tr AFTER INSERT ON clientes BEGIN SELECT 1; END;
CREATE TABLE sin_fila (a TEXT PRIMARY KEY, b) WITHOUT ROWID;
CREATE VIRTUAL TABLE busqueda USING fts5(texto);
INSERT INTO clientes(email) VALUES ('a@x'), ('b@x');
"""


def image(script: str) -> bytes:
    """The serialized database that the SQL script creates in an in-memory connection."""
    connection = sqlite3.connect(":memory:", isolation_level=None)
    try:
        connection.executescript(script)
        return bytes(connection.serialize())
    finally:
        connection.close()
