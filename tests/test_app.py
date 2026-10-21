import base64
import io
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from pdf_fixture import fake_gemini, pdf_bytes, product
from torman.app import create_app
from torman.catalog import UserError, read_pdf
from torman.gemini import (IncompleteResponse, ResponseValidationError, call_gemini,
                           request_validated, validate_pdf_matches)
from torman.search import (INDEX_VERSION, batches_for, cache_key, fast_search,
                           index_status, prepare_index, validate_index, validate_selection)
from torman.storage import Storage

ROOT = Path(__file__).resolve().parent.parent
TEST_ROOT = ROOT / ".test-output"
TEST_ROOT.mkdir(exist_ok=True)


class ParsingTests(unittest.TestCase):
    def test_pdf_text_and_physical_pages(self):
        result = read_pdf(pdf_bytes(3), "Каталог.PDF")
        self.assertEqual(result["source_type"], "pdf")
        self.assertEqual(result["page_count"], 3)
        self.assertEqual(result["text_page_count"], 3)
        self.assertIn("Pipe A2", result["pages"][1]["text"])

    def test_pdf_without_text_is_accepted_for_vision(self):
        result = read_pdf(pdf_bytes(text=False), "scan.pdf")
        self.assertEqual(result["text_page_count"], 0)
        self.assertTrue(result["warnings"])

    def test_corrupt_encrypted_empty_and_wrong_extension(self):
        for raw, name in [(b"", "a.pdf"), (b"not pdf", "a.pdf"), (b"%PDF-broken", "a.pdf"),
                          (pdf_bytes(), "a.xlsx"), (pdf_bytes(password="secret"), "a.pdf"), (pdf_bytes(0), "a.pdf")]:
            with self.subTest(name=name, size=len(raw)):
                with self.assertRaises(UserError):
                    read_pdf(raw, name)

    def test_file_and_page_limits(self):
        with patch("torman.catalog.MAX_FILE_BYTES", 10):
            with self.assertRaises(UserError):
                read_pdf(pdf_bytes(), "a.pdf")
        with patch("torman.catalog.MAX_PAGES", 1):
            with self.assertRaises(UserError):
                read_pdf(pdf_bytes(), "a.pdf")

    def test_preview_truncation_does_not_change_original(self):
        with patch("torman.catalog.MAX_PREVIEW_TEXT", 5):
            result = read_pdf(pdf_bytes(), "a.pdf")
        self.assertTrue(result["pages"][0]["truncated"])
        self.assertTrue(result["pages"][1]["truncated"])
        self.assertTrue(result["warnings"])


class StoredTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=TEST_ROOT)
        self.storage = Storage(self.temp.name)
        self.original = pdf_bytes(13)
        self.catalog = self.storage.save_catalog(read_pdf(self.original, "catalog.pdf"), self.original, "catalog.pdf")
        self.cancel = threading.Event()
        self.progress = lambda *args: None

    def tearDown(self):
        self.temp.cleanup()

    def search(self, query="stainless steel 50 mm", caller=fake_gemini, **kwargs):
        return fast_search(self.storage, self.catalog, self.original, query, "fake-key",
                           "gemini-3.8-flash", self.progress, self.cancel, caller=caller, **kwargs)

    def test_prepare_once_persist_and_exact_query_cache(self):
        calls = []
        def caller(*args, **kwargs):
            calls.append((json.loads(args[2])["task"], "pdf_bytes" in kwargs))
            return fake_gemini(*args, **kwargs)
        first = self.search(caller=caller)
        self.assertEqual(sum(task == "index" for task, _ in calls), 3)
        self.assertEqual(first["indexed_records"], 13)
        self.assertTrue(index_status(self.storage, self.catalog, "gemini-3.8-flash")["ready"])
        self.storage = Storage(self.temp.name)
        calls.clear()
        second = self.search("another characteristic query", caller)
        self.assertEqual(calls, [("match", False)])
        self.assertFalse(second["cached"])
        calls.clear()
        third = self.search("another characteristic query", caller)
        self.assertTrue(third["cached"])
        self.assertEqual(calls, [])
        self.assertNotEqual(third["id"], second["id"])
        # Matching data are the saved source values, never invented model fields.
        self.assertEqual(third["matches"][0]["product"]["values"]["Diameter, mm"], "50")

    def test_cache_does_not_conflate_case_or_numbers_or_models(self):
        keys = [cache_key(self.catalog["id"], model, query) for model, query in
                [("gemini-a", "A50"), ("gemini-a", "a50"), ("gemini-a", "A51"), ("gemini-b", "A50")]]
        self.assertEqual(len(set(keys)), 4)

    def test_replacement_invalidates_and_old_worker_cannot_repopulate(self):
        self.search()
        self.storage.save_catalog(read_pdf(pdf_bytes(), "new.pdf"), pdf_bytes(), "new.pdf")
        self.assertEqual(self.storage.prepared_chunks(self.catalog["id"], "gemini-3.8-flash", INDEX_VERSION), {})
        self.storage.save_chunk(self.catalog["id"], "gemini-3.8-flash", INDEX_VERSION, "1-6", {"products": []})
        self.assertEqual(self.storage.prepared_chunks(self.catalog["id"], "gemini-3.8-flash", INDEX_VERSION), {})
        self.assertIsNone(self.storage.cached_search(cache_key(self.catalog["id"], "gemini-3.8-flash", "stainless steel 50 mm")))

    def test_limited_parallelism(self):
        barrier = threading.Barrier(3)
        lock = threading.Lock()
        active = peak = 0
        def caller(*args, **kwargs):
            nonlocal active, peak
            if json.loads(args[2]).get("task") == "index":
                with lock:
                    active += 1
                    peak = max(peak, active)
                barrier.wait(timeout=10)
                response = fake_gemini(*args, **kwargs)
                with lock:
                    active -= 1
                return response
            return fake_gemini(*args, **kwargs)
        self.search(caller=caller)
        self.assertEqual(peak, 3)

    def test_interrupted_preparation_resumes_without_rereading_saved_pages(self):
        def failing(*args, **kwargs):
            if json.loads(args[2]).get("target_pages") == [13]:
                time.sleep(.1)
                raise UserError("simulated provider failure")
            return fake_gemini(*args, **kwargs)
        with self.assertRaises(UserError):
            self.search(caller=failing)
        saved = self.storage.prepared_chunks(self.catalog["id"], "gemini-3.8-flash", INDEX_VERSION)
        self.assertIn("1-6", saved)
        self.assertIn("7-12", saved)
        called = []
        def resume(*args, **kwargs):
            if "pdf_bytes" in kwargs:
                called.append(json.loads(args[2])["target_pages"])
            return fake_gemini(*args, **kwargs)
        self.search(caller=resume)
        self.assertEqual(called, [[13]])

    def test_incomplete_output_is_split_and_no_partial_results_accepted(self):
        def small_only(*args, **kwargs):
            data = json.loads(args[2])
            if len(data.get("target_pages", [])) > 2:
                raise IncompleteResponse("too large")
            return fake_gemini(*args, **kwargs)
        result = self.search(caller=small_only)
        self.assertEqual(result["indexed_records"], 13)

    def test_unreadable_pages_warn_and_do_not_cache(self):
        def unreadable(*args, **kwargs):
            result = fake_gemini(*args, **kwargs)
            data = json.loads(args[2])
            if data.get("target_pages") == [13]:
                result.update(products=[], unreadable_pages=[13])
            return result
        result = self.search(caller=unreadable)
        self.assertEqual(result["unreadable_pages"], [13])
        self.assertIsNone(self.storage.cached_search(cache_key(self.catalog["id"], "gemini-3.8-flash", result["query"])))

    def test_document_without_products_is_not_a_validation_error(self):
        def no_products(*args, **kwargs):
            data = json.loads(args[2])
            self.assertEqual(data["task"], "index", "Empty catalogue must not be sent for matching")
            return {"products": [], "covered_pages": data["target_pages"], "unreadable_pages": [], "complete": True}
        result = self.search(caller=no_products)
        self.assertTrue(result["empty_catalog"])
        self.assertEqual(result["matches"], [])
        self.assertEqual(result["indexed_records"], 0)

    def test_index_format_error_is_corrected_once_and_checkpointed(self):
        prompts = []
        lock = threading.Lock()
        def caller(*args, **kwargs):
            prompt = args[2]
            data = json.loads(prompt.split("\n", 1)[0])
            result = fake_gemini(args[0], args[1], json.dumps(data), **kwargs)
            if data.get("task") == "index" and data["target_pages"][0] == 1:
                with lock:
                    prompts.append(prompt)
                if "\n" not in prompt:
                    result["products"][0]["sku"] = None
            return result
        result = self.search(caller=caller)
        self.assertEqual(len(prompts), 2)
        self.assertIn("products[0].sku", prompts[1])
        self.assertEqual(result["indexed_records"], 13)
        self.assertTrue(index_status(self.storage, self.catalog, "gemini-3.8-flash")["ready"])

    def test_invalid_corrected_answer_is_not_saved(self):
        self.original = pdf_bytes(2)
        self.catalog = self.storage.save_catalog(read_pdf(self.original, "a.pdf"), self.original, "a.pdf")
        calls = []
        def invalid(*args, **kwargs):
            calls.append(args[2])
            result = fake_gemini(args[0], args[1], args[2].split("\n", 1)[0], **kwargs)
            result["products"][0]["pages"] = [0]
            return result
        with self.assertRaises(ResponseValidationError) as caught:
            self.search(caller=invalid)
        self.assertEqual(len(calls), 2)
        self.assertIn("products[0].pages", str(caught.exception))
        self.assertEqual(self.storage.prepared_chunks(self.catalog["id"], "gemini-3.8-flash", INDEX_VERSION), {})
        self.assertIsNone(self.storage.cached_search(cache_key(self.catalog["id"], "gemini-3.8-flash", "stainless steel 50 mm")))

    def test_cancellation_does_not_save_results(self):
        self.cancel.set()
        with self.assertRaises(UserError):
            self.search(caller=lambda *args, **kwargs: self.fail("must not call Gemini"))
        self.assertEqual(self.storage.history(), [])

    def test_direct_pdf_mode_always_calls_provider(self):
        self.search()
        seen = []
        def direct(*args, **kwargs):
            seen.append(kwargs["pdf_bytes"])
            return fake_gemini(*args, **kwargs)
        result = self.search(caller=direct, direct=True)
        self.assertEqual(seen, [self.original])
        self.assertEqual(result["mode"], "direct")
        self.assertFalse(result["cached"])

    def test_all_batches_are_checked_and_ranked(self):
        seen, ranked = [], []
        def caller(*args, **kwargs):
            data = json.loads(args[2])
            if data.get("task") == "match":
                seen.extend(p["id"] for p in data["products"])
                # Force enough candidates for final ranking.
                return {"matches": [{"id": p["id"], "status": "exact", "reason": "50 mm", "evidence": ["Diameter, mm"], "missing": []} for p in data["products"]]}
            if data.get("task") == "rank":
                ranked.append(len(data["products"]))
            return fake_gemini(*args, **kwargs)
        # 25 records, five matching batches: all must enter matching, not just a
        # lexical top-k. Force batching while retaining normal ranking sizes.
        original = pdf_bytes(25)
        self.catalog = self.storage.save_catalog(read_pdf(original, "a.pdf"), original, "a.pdf")
        self.original = original
        original_batches = batches_for
        def five_at_a_time(products, limit=None):
            if limit is None:
                return (products[i:i + 5] for i in range(0, len(products), 5))
            return original_batches(products, limit)
        with patch("torman.search.batches_for", side_effect=five_at_a_time):
            result = self.search(caller=caller)
        self.assertEqual(len(set(seen)), 25)
        self.assertTrue(ranked)
        self.assertLessEqual(len(result["matches"]), 15)


class ValidationTests(unittest.TestCase):
    def test_missing_field_reports_path_without_source_values(self):
        response = fake_gemini("", "", "{}")
        response["matches"][0]["sku"] = None
        response["matches"][0]["name"] = "private catalogue title"
        with self.assertRaises(ResponseValidationError) as caught:
            validate_pdf_matches(response, 2)
        self.assertIn("matches[0].sku", str(caught.exception))
        self.assertNotIn("private catalogue title", str(caught.exception))

    def test_conflicting_characteristics_still_rejected(self):
        response = fake_gemini("", "", "{}")
        response["matches"][0]["attributes"].append({"name": "Артикул", "value": "Different-model"})
        with self.assertRaises(ResponseValidationError) as caught:
            validate_pdf_matches(response, 2)
        self.assertIn("attributes[2]", str(caught.exception))
        self.assertNotIn("Different-model", str(caught.exception))

    def test_correction_cancellation_stops_before_second_call(self):
        cancel = threading.Event()
        calls = []
        def caller(*args, **kwargs):
            calls.append(1)
            return {}
        with self.assertRaises(UserError):
            request_validated(caller, "key", "model", "test", lambda r: validate_pdf_matches(r, 2),
                              cancel=cancel, on_retry=cancel.set)
        self.assertEqual(len(calls), 1)

    def test_physical_pages_and_missing_force_uncertain(self):
        response = fake_gemini("", "", "{}")
        response["matches"][0]["pages"] = [0]
        with self.assertRaises(UserError):
            validate_pdf_matches(response, 2)
        response["matches"][0]["pages"] = [True]
        with self.assertRaises(UserError):
            validate_pdf_matches(response, 2)
        response["matches"][0]["pages"] = [1]
        response["matches"][0]["missing"] = ["Нет цены"]
        matches, _ = validate_pdf_matches(response, 2)
        self.assertEqual(matches[0]["status"], "uncertain")

    def test_index_requires_all_pages_and_only_attached_page_references(self):
        response = fake_gemini("", "", json.dumps({"task": "index", "target_pages": [7, 8]}))
        response["covered_pages"] = [7]
        with self.assertRaises(UserError):
            validate_index(response, 7, 8, 20)
        response["covered_pages"] = [7, 8]
        response["products"][0]["pages"] = [1, 7]
        with self.assertRaises(UserError):
            validate_index(response, 7, 8, 20)

    def test_invented_product_or_evidence_rejected_and_uncertainty_preserved(self):
        p = {"id": "known", "values": {"Size": "50 mm"}, "pages": [1], "source_excerpt": "50 mm", "source_missing": []}
        row = {"id": "invented", "status": "exact", "reason": "ok", "evidence": ["Size"], "missing": []}
        with self.assertRaises(UserError):
            validate_selection({"matches": [row]}, [p])
        row["id"] = "known"
        row["evidence"] = ["invented field"]
        with self.assertRaises(UserError):
            validate_selection({"matches": [row]}, [p])
        row["evidence"] = ["Size"]
        matches = validate_selection({"matches": [row]}, [p], {"known": {"status": "uncertain", "missing": ["Цена"]}})
        self.assertEqual(matches[0]["status"], "uncertain")
        self.assertEqual(matches[0]["missing"], ["Цена"])

    def test_batching_preserves_every_record(self):
        products = [{"id": str(i), "text": "x" * 100} for i in range(101)]
        batches = list(batches_for(products, 600))
        self.assertGreater(len(batches), 1)
        self.assertEqual([p for b in batches for p in b], products)


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=TEST_ROOT)
        self.calls = []
        def caller(*args, **kwargs):
            self.calls.append(kwargs)
            return fake_gemini(*args, **kwargs)
        self.app = create_app(self.temp.name, gemini_caller=caller)
        self.app.testing = True
        self.client = self.app.test_client()
        self.headers = {"X-Torman-Request": "1"}

    def tearDown(self):
        self.temp.cleanup()

    def post(self, path, data):
        return self.client.post(path, json=data, headers=self.headers)

    def upload(self):
        return self.client.post("/api/upload", data={"file": (io.BytesIO(pdf_bytes()), "Каталог.pdf")}, headers=self.headers).get_json()

    def commit(self):
        result = self.post("/api/upload/commit", {"upload_id": self.upload()["id"]})
        self.assertEqual(result.status_code, 200)
        return result.get_json()["catalog"]

    def wait_job(self, job_id):
        for _ in range(300):
            job = self.client.get("/api/jobs/" + job_id).get_json()
            if job["status"] != "running":
                self.assertEqual(job["status"], "done", job)
                return job["result"]
            time.sleep(.01)
        self.fail("job did not complete")

    def test_commit_original_persistence_and_bad_upload_preserves_data(self):
        catalog = self.commit()
        restarted = create_app(self.temp.name).test_client()
        self.assertEqual(restarted.get("/api/state").get_json()["catalog"]["id"], catalog["id"])
        self.assertEqual(restarted.get("/api/catalog").get_json()["total"], 2)
        self.assertEqual(restarted.get("/api/catalog/original").data, pdf_bytes())
        bad = self.client.post("/api/upload", data={"file": (io.BytesIO(b"broken"), "bad.pdf")}, headers=self.headers)
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(restarted.get("/api/state").get_json()["catalog"]["id"], catalog["id"])

    def test_preview_not_committed_and_page_links_require_same_catalog(self):
        old = self.commit()
        upload = self.upload()
        self.assertEqual(self.post("/api/upload/preview", {"upload_id": upload["id"]}).get_json()["page_count"], 2)
        self.assertEqual(self.client.get("/api/state").get_json()["catalog"]["id"], old["id"])
        self.assertEqual(self.client.get(f"/api/catalog/document?catalog_id={old['id']}").data, pdf_bytes())
        self.assertEqual(self.client.get("/api/catalog/document?catalog_id=wrong").status_code, 400)
        self.assertEqual(self.client.get(f"/api/upload/{upload['id']}/document").status_code, 200)
        self.assertEqual(self.client.get("/api/catalog?q=Pipe%20A2").get_json()["total"], 1)

    def test_security_and_all_assets(self):
        self.assertEqual(self.client.post("/api/settings", json={}).status_code, 403)
        self.assertEqual(self.client.post("/api/settings", json={}, headers={**self.headers, "Origin": "https://evil.example"}).status_code, 403)
        self.assertEqual(self.client.get("/api/state", headers={"Host": "evil.example"}).status_code, 403)
        for path in ("/", "/assets/app.js", "/assets/styles.css", "/assets/pdf.css", "/assets/brand/logo-light.svg", "/assets/brand/torman.ico"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)
            self.assertIn("Content-Security-Policy", response.headers)
            response.close()
        self.assertEqual(self.post("/api/settings", {"model": "../../bad"}).status_code, 400)

    def test_job_history_cache_and_offline_cache_after_restart(self):
        self.commit()
        self.post("/api/settings", {"model": "gemini-3.8-flash", "key": "not-a-real-key", "remember": False})
        query = "Нержавейка 50 мм"
        started = self.post("/api/search", {"query": query})
        self.assertEqual(started.status_code, 202)
        result = self.wait_job(started.get_json()["id"])
        self.assertEqual(len(result["matches"]), 2)
        self.assertEqual(len(self.client.get("/api/history").get_json()["items"]), 1)
        restarted = create_app(self.temp.name, gemini_caller=lambda *a, **kw: self.fail("cache called network")).test_client()
        with patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
            response = restarted.post("/api/search", json={"query": query}, headers=self.headers)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.get_json()["result"]["cached"])
            self.assertEqual(restarted.post("/api/search", json={"query": "другой товар"}, headers=self.headers).status_code, 400)
        self.assertNotIn("not-a-real-key", json.dumps(self.client.get("/api/state").get_json()))

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI")
    def test_key_encryption(self):
        self.post("/api/settings", {"model": "gemini-3.8-flash", "key": "private-test-key", "remember": True})
        self.assertNotIn(b"private-test-key", (Path(self.temp.name) / "torman.sqlite3").read_bytes())
        self.assertTrue(Storage(self.temp.name).settings()["has_key"])

    def test_connection_is_a_small_text_probe_without_pdf_or_catalog_schema(self):
        self.post("/api/settings", {"model": "gemini-2.5-flash", "key": "test-key", "remember": False})
        response = self.post("/api/settings/test", {})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"ok": True})
        self.assertEqual(len(self.calls), 1)
        self.assertIsNone(self.calls[0]["schema"])
        self.assertNotIn("pdf_bytes", self.calls[0])
        self.assertLessEqual(self.calls[0]["max_tokens"], 1024)


class TransportTests(unittest.TestCase):
    @staticmethod
    def error(code=400, message="Request rejected", reason="", status="INVALID_ARGUMENT"):
        body = {"error": {"code": code, "message": message, "status": status, "details": [{"reason": reason}]}}
        return urllib.error.HTTPError("https://generativelanguage.googleapis.com/test", code, "test", {}, io.BytesIO(json.dumps(body).encode()))

    @staticmethod
    def success(text='{"matches":[],"unreadable_pages":[]}'):
        return io.BytesIO(json.dumps({"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": text}]}}]}).encode())

    def test_connection_probe_has_no_schema_and_uses_short_legacy_flash_response(self):
        captured = []
        def send(request, **kwargs):
            captured.append(json.loads(request.data))
            return self.success("OK")
        with patch("urllib.request.urlopen", side_effect=send):
            answer = call_gemini("key", "gemini-2.5-flash", "Reply with OK.", schema=None, max_tokens=1024, fast=True)
        self.assertEqual(answer, {"text": "OK"})
        config = captured[0]["generationConfig"]
        self.assertNotIn("responseJsonSchema", config)
        self.assertNotIn("responseMimeType", config)
        self.assertEqual(config["thinkingConfig"], {"thinkingBudget": 0})

    def test_invalid_key_http400_is_explained_redacted_and_never_retried(self):
        key = "AIza" + "a" * 35
        error = self.error(message="API key not valid: " + key, reason="API_KEY_INVALID")
        with patch("urllib.request.urlopen", side_effect=error) as send:
            with self.assertRaises(UserError) as caught:
                call_gemini(key, "gemini-2.5-flash", "probe", schema=None)
        message = str(caught.exception)
        self.assertIn("Google не принял API-ключ", message)
        self.assertIn("HTTP 400", message)
        self.assertIn("API_KEY_INVALID", message)
        self.assertNotIn(key, message)
        self.assertEqual(send.call_count, 1)

    def test_account_region_and_model_errors_preserve_provider_reason(self):
        for message, expected in [("User location is not supported", "региона"),
                                  ("Free tier is not available. Enable billing.", "тарифа"),
                                  ("Model gemini-2.5-flash is not found", "модель недоступна")]:
            with self.subTest(message=message), patch("urllib.request.urlopen", side_effect=self.error(message=message)) as send:
                with self.assertRaises(UserError) as caught:
                    call_gemini("key", "gemini-2.5-flash", "probe", schema=None)
                self.assertIn(expected, str(caught.exception))
                self.assertIn("Google: " + message, str(caught.exception))
                self.assertEqual(send.call_count, 1)

    def test_schema_compatibility_retries_only_explicit_rejection(self):
        captured = []
        def send(request, **kwargs):
            captured.append(json.loads(request.data))
            if len(captured) == 1:
                raise self.error(message='Unknown name "responseJsonSchema" at generation_config')
            return self.success()
        with patch("urllib.request.urlopen", side_effect=send):
            result = call_gemini("key", "gemini-2.5-flash", "test")
        self.assertEqual(result["matches"], [])
        self.assertEqual(len(captured), 2)
        self.assertNotIn("responseJsonSchema", captured[1]["generationConfig"])
        self.assertEqual(captured[1]["generationConfig"]["responseSchema"]["type"], "OBJECT")

    def test_complex_schema_retry_is_bounded_and_generic_400_does_not_retry(self):
        captured = []
        def send(request, **kwargs):
            captured.append(json.loads(request.data))
            raise self.error(message="The schema has too many states for serving")
        with patch("urllib.request.urlopen", side_effect=send):
            with self.assertRaises(UserError):
                call_gemini("key", "gemini-2.5-flash", "test")
        self.assertEqual(len(captured), 2)
        self.assertNotIn("maxItems", json.dumps(captured[1]["generationConfig"]["responseJsonSchema"]))
        with patch("urllib.request.urlopen", side_effect=self.error()) as send:
            with self.assertRaises(UserError) as caught:
                call_gemini("key", "gemini-2.5-flash", "test")
        self.assertEqual(send.call_count, 1)
        self.assertIn("Request rejected", str(caught.exception))

    def test_non_json_provider_error_is_still_actionable(self):
        error = urllib.error.HTTPError("https://generativelanguage.googleapis.com/test", 400, "test", {}, io.BytesIO(b"<html>no JSON</html>"))
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(UserError) as caught:
                call_gemini("key", "gemini-2.5-flash", "test")
        self.assertIn("HTTP 400", str(caught.exception))
        self.assertIn("не передал подробности", str(caught.exception))

    def test_native_pdf_schema_and_low_latency_configuration(self):
        response = {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": '{"matches":[],"unreadable_pages":[]}'}]}}]}
        captured = []
        def open_request(request, **kwargs):
            captured.append(json.loads(request.data))
            return io.BytesIO(json.dumps(response).encode())
        with patch("urllib.request.urlopen", side_effect=open_request):
            call_gemini("secret", "gemini-3.8-flash", "hello", pdf_bytes=b"%PDF-sample", fast=True)
        payload = captured[0]
        self.assertEqual(base64.b64decode(payload["contents"][0]["parts"][0]["inlineData"]["data"]), b"%PDF-sample")
        self.assertEqual(payload["generationConfig"]["thinkingConfig"]["thinkingLevel"], "low")
        self.assertIn("responseJsonSchema", payload["generationConfig"])
        self.assertNotIn("secret", json.dumps(payload))

    def test_token_cutoff_never_becomes_empty_success(self):
        response = {"candidates": [{"finishReason": "MAX_TOKENS", "content": {"parts": [{"text": '{"matches":[]}'}]}}]}
        with patch("urllib.request.urlopen", return_value=io.BytesIO(json.dumps(response).encode())):
            with self.assertRaises(IncompleteResponse):
                call_gemini("key", "gemini-3.8-flash", "hello")


if __name__ == "__main__":
    unittest.main()
