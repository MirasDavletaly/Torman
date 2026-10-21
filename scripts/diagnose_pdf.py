"""Reproduce PDF validation against one chunk without logging keys or PDF text.

Explicit manual diagnostic. Calls Gemini once with the already imported PDF.
Reads the app database in read-only mode; never modifies the user's catalogue.
"""
import io
import json
import os
import sqlite3
import sys
import threading
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pypdf import PdfReader, PdfWriter
from torman.gemini import call_gemini
from torman.catalog import read_pdf
from torman.search import CHUNK_PAGES, INDEX_SCHEMA, INDEX_SYSTEM, INDEX_VERSION, fast_search, validate_index
from torman.storage import Storage, protect_key


def shape(value):
    if isinstance(value, dict):
        return {name: shape(item) for name, item in value.items()}
    if isinstance(value, list):
        return {"type": "list", "length": len(value), "items": [shape(v) for v in value[:3]]}
    if isinstance(value, str):
        return {"type": "str", "length": len(value), "empty": not value.strip()}
    return {"type": type(value).__name__}


def main():
    database = Path(os.environ["LOCALAPPDATA"]) / "Torman" / "torman.sqlite3"
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
        model, encrypted = db.execute("SELECT model, encrypted_key FROM settings WHERE id=1").fetchone()
        metadata, original = db.execute("SELECT content, original FROM catalog WHERE id=1").fetchone()
    key = os.environ.get("GEMINI_API_KEY") or (protect_key(encrypted, decrypt=True) if encrypted else "")
    if not key:
        raise RuntimeError("No saved key; enter it in the app, not in diagnostic output.")
    catalog = json.loads(metadata)
    sample = "--sample" in sys.argv or "--match-sample" in sys.argv
    if sample:
        sys.path.insert(0, str(ROOT / "tests"))
        from pdf_fixture import pdf_bytes
        original = pdf_bytes()
        catalog = {"page_count": 2}
    if "--match-sample" in sys.argv:
        response = json.loads((ROOT / ".test-output" / "gemini-sample-index.json").read_text(encoding="utf-8"))
        chunk = validate_index(response, 1, 2, 2)
        storage = Storage(ROOT / ".test-output" / ("live-sample-" + uuid.uuid4().hex))
        catalog = storage.save_catalog(read_pdf(original, "sample.pdf"), original, "sample.pdf")
        storage.save_chunk(catalog["id"], model, INDEX_VERSION, "1-2", chunk)
        result = fast_search(storage, catalog, original, "Труба из нержавеющей стали, диаметр 50 мм, не дороже 30000 KZT", key, model, lambda *a: None, threading.Event())
        assert len(result["matches"]) == 2, "Expected both matching sample products"
        assert all(m["status"] == "exact" for m in result["matches"]), "Expected confirmed sample characteristics"
        print(json.dumps({"live_matching": "passed", "matches": len(result["matches"]), "duration_ms": result["duration_ms"]}), flush=True)
        return
    last = min(CHUNK_PAGES, catalog["page_count"])
    physical = list(range(1, min(catalog["page_count"], last + 1) + 1))
    reader, writer = PdfReader(io.BytesIO(original)), PdfWriter()
    for number in physical:
        writer.add_page(reader.pages[number - 1])
    stream = io.BytesIO()
    writer.write(stream)
    prompt = json.dumps({"task": "index", "physical_pages": physical, "target_pages": list(range(1, last + 1))})
    print(json.dumps({"model": model, "pages": len(physical), "pdf_bytes": len(stream.getvalue())}), flush=True)
    response = call_gemini(key, model, prompt, schema=INDEX_SCHEMA, pdf_bytes=stream.getvalue(), system=INDEX_SYSTEM, max_tokens=32768)
    if sample:
        (ROOT / ".test-output" / "gemini-sample-index.json").write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"response_keys": list(response), "products_count": len(response.get("products", []))}), flush=True)
    try:
        result = validate_index(response, 1, last, catalog["page_count"])
        print(json.dumps({"validation": "passed", "products_count": len(result["products"])}))
    except Exception as exc:
        tb = exc.__traceback__
        frames = []
        while tb:
            frame = tb.tb_frame
            if frame.f_code.co_name in ("validate_index", "validate_pdf_matches", "valid_pages"):
                info = {"function": frame.f_code.co_name, "line": tb.tb_lineno}
                for name in ("field", "name"):
                    if name in frame.f_locals:
                        info[name] = frame.f_locals[name]
                for name in ("item", "attrs", "value", "pages"):
                    if name in frame.f_locals:
                        info[name + "_shape"] = shape(frame.f_locals[name])
                frames.append(info)
            tb = tb.tb_next
        print(json.dumps({"validation": "failed", "error_type": type(exc).__name__, "frames": frames}, ensure_ascii=True))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Exceptions are already sanitized by the provider wrapper. Do not dump
        # frames / locals at this level, which contain the credential.
        print(type(exc).__name__ + ": " + str(exc))
        sys.exit(1)
