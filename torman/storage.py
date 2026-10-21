import base64
import ctypes
import json
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path

from .catalog import UserError

DEFAULT_MODEL = "gemini-3.8-flash"


def now():
    return datetime.now(timezone.utc).isoformat()


def protect_key(value, decrypt=False):
    """Use Windows DPAPI, bound to the current Windows account."""
    if os.name != "nt":
        raise UserError("На этой системе ключ можно использовать только до закрытия приложения.")

    class Blob(ctypes.Structure):
        _fields_ = [("length", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    raw = base64.b64decode(value) if decrypt else value.encode("utf-8")
    buffer = ctypes.create_string_buffer(raw)
    source = Blob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    operation = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    operation.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    operation.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    if not operation(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise UserError("Windows не смог сохранить/прочитать ключ. Введите ключ заново или отключите запоминание.")
    try:
        result = ctypes.string_at(target.data, target.length)
        return result.decode("utf-8") if decrypt else base64.b64encode(result).decode("ascii")
    finally:
        kernel32.LocalFree(target.data)


class Storage:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "torman.sqlite3"
        self.session_key = ""
        self.lock = threading.RLock()
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS catalog (id INTEGER PRIMARY KEY CHECK(id=1), content TEXT NOT NULL, original BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK(id=1), model TEXT NOT NULL, encrypted_key TEXT NOT NULL DEFAULT '');
                CREATE TABLE IF NOT EXISTS history (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, catalog_id TEXT NOT NULL, content TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS prepared_chunks (catalog_id TEXT NOT NULL, model TEXT NOT NULL, version INTEGER NOT NULL, chunk_key TEXT NOT NULL, content TEXT NOT NULL, PRIMARY KEY(catalog_id, model, version, chunk_key));
                CREATE TABLE IF NOT EXISTS search_cache (cache_key TEXT PRIMARY KEY, catalog_id TEXT NOT NULL, created_at TEXT NOT NULL, content TEXT NOT NULL);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        try:
            with db:
                yield db
        finally:
            db.close()

    def catalog(self):
        with self.connect() as db:
            record = db.execute("SELECT content FROM catalog WHERE id=1").fetchone()
        return json.loads(record[0]) if record else None

    def catalog_with_original(self):
        # Read one snapshot so a simultaneous replacement cannot mix two files.
        with self.connect() as db:
            record = db.execute("SELECT content, original FROM catalog WHERE id=1").fetchone()
        return (json.loads(record[0]), record[1]) if record else (None, None)

    def save_catalog(self, catalog, original, filename):
        catalog = {**catalog, "id": uuid.uuid4().hex, "filename": filename, "saved_at": now()}
        # Commit the original PDF and its page metadata atomically.
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO catalog VALUES (1, ?, ?)", (json.dumps(catalog, ensure_ascii=False), original))
            db.execute("DELETE FROM prepared_chunks")
            db.execute("DELETE FROM search_cache")
        return catalog

    def prepared_chunks(self, catalog_id, model, version):
        with self.connect() as db:
            rows = db.execute("SELECT chunk_key, content FROM prepared_chunks WHERE catalog_id=? AND model=? AND version=?", (catalog_id, model, version)).fetchall()
        return {key: json.loads(content) for key, content in rows}

    def save_chunk(self, catalog_id, model, version, chunk_key, content):
        with self.connect() as db:
            # A search can finish after the user has replaced the catalogue.
            db.execute("INSERT OR REPLACE INTO prepared_chunks SELECT ?, ?, ?, ?, ? FROM catalog WHERE id=1 AND json_extract(content, '$.id')=?", (catalog_id, model, version, chunk_key, json.dumps(content, ensure_ascii=False), catalog_id))

    def cached_search(self, cache_key):
        with self.connect() as db:
            row = db.execute("SELECT content FROM search_cache WHERE cache_key=?", (cache_key,)).fetchone()
        return json.loads(row[0]) if row else None

    def cache_search(self, cache_key, result):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO search_cache SELECT ?, ?, ?, ? FROM catalog WHERE id=1 AND json_extract(content, '$.id')=?", (cache_key, result["catalog_id"], now(), json.dumps(result, ensure_ascii=False), result["catalog_id"]))
            db.execute("DELETE FROM search_cache WHERE cache_key NOT IN (SELECT cache_key FROM search_cache ORDER BY created_at DESC LIMIT 100)")

    def original(self):
        with self.connect() as db:
            row = db.execute("SELECT original FROM catalog WHERE id=1").fetchone()
        return row[0] if row else None

    def credentials(self):
        with self.lock, self.connect() as db:
            row = db.execute("SELECT model, encrypted_key FROM settings WHERE id=1").fetchone()
            model, encrypted = row if row else (DEFAULT_MODEL, "")
            key = self.session_key or os.environ.get("GEMINI_API_KEY", "")
            if not key and encrypted:
                try:
                    key = protect_key(encrypted, decrypt=True)
                except UserError:
                    key = ""
            return model, key, bool(encrypted)

    def settings(self):
        model, key, remembered = self.credentials()
        return {"model": model, "has_key": bool(key), "remembered": remembered, "can_remember": os.name == "nt"}

    def save_settings(self, model, key=None, remember=True, clear=False):
        with self.lock:
            _, old_key, _ = self.credentials()
            key = "" if clear else (key or old_key)
            encrypted = protect_key(key) if key and remember else ""
            with self.connect() as db:
                db.execute("INSERT OR REPLACE INTO settings VALUES (1, ?, ?)", (model, encrypted))
            self.session_key = key if not remember else ""
        return self.settings()

    def save_search(self, result):
        with self.connect() as db:
            db.execute("INSERT INTO history VALUES (?, ?, ?, ?)", (result["id"], result["created_at"], result["catalog_id"], json.dumps(result, ensure_ascii=False)))
            db.execute("DELETE FROM history WHERE id NOT IN (SELECT id FROM history ORDER BY created_at DESC LIMIT 20)")

    def history(self):
        with self.connect() as db:
            rows = db.execute("SELECT content FROM history ORDER BY created_at DESC LIMIT 20").fetchall()
        return [json.loads(row[0]) for row in rows]
