"""Measure local overhead and concurrency using a fixed simulated API latency.

This is NOT a benchmark of Google's model or its OCR accuracy. No network calls.
"""
import json
import sys
import threading
import time
import uuid
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pdf_fixture import fake_gemini, pdf_bytes
from torman.app import create_app
from torman.catalog import read_pdf
from torman.search import fast_search
from torman.storage import Storage


def benchmark(workers):
    directory = ROOT / ".test-output" / ("benchmark-" + uuid.uuid4().hex)
    storage = Storage(directory)
    original = pdf_bytes(36)
    catalog = storage.save_catalog(read_pdf(original, "sample.pdf"), original, "sample.pdf")
    calls = []
    lock = threading.Lock()

    def provider(*args, **kwargs):
        with lock:
            calls.append({"task": json.loads(args[2]).get("task"), "pdf_bytes": len(kwargs.get("pdf_bytes", b""))})
        time.sleep(.10)
        return fake_gemini(*args, **kwargs)

    def search(query):
        started = time.perf_counter()
        result = fast_search(storage, catalog, original, query, "fake-key", "gemini-3.8-flash", lambda *a: None, threading.Event(), caller=provider)
        return round((time.perf_counter() - started) * 1000, 2), result

    with patch("torman.search.MAX_WORKERS", workers):
        first_ms, _ = search("first query")
        first_calls = len(calls)
        warm_ms, _ = search("new query")
        warm_calls = calls[first_calls:]
        assert all(c["pdf_bytes"] == 0 for c in warm_calls)
        count_before = len(calls)
        cached_ms, result = search("new query")
        assert result["cached"] and len(calls) == count_before
    app = create_app(directory, gemini_caller=lambda *a, **k: (_ for _ in ()).throw(AssertionError("network called")))
    client = app.test_client()
    timings = []
    for _ in range(10):
        started = time.perf_counter()
        response = client.post("/api/search", json={"query": "new query"}, headers={"X-Torman-Request": "1"})
        timings.append((time.perf_counter() - started) * 1000)
        assert response.status_code == 200 and response.get_json()["result"]["cached"]
    return {"workers": workers, "pages": 36, "simulated_api_ms": 100, "first_ms": first_ms,
            "first_api_calls": first_calls, "new_query_ms": warm_ms, "new_query_api_calls": len(warm_calls),
            "new_query_pdf_bytes_sent": sum(c["pdf_bytes"] for c in warm_calls),
            "cached_query_ms": cached_ms, "cached_query_api_calls": 0,
            "cached_http_median_ms": round(sorted(timings)[len(timings) // 2], 2)}


if __name__ == "__main__":
    results = [benchmark(1), benchmark(3)]
    print(json.dumps({"provider": "simulated; not real Gemini", "results": results}, indent=2))
