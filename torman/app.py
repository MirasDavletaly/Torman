import io
import json
import re
import threading
import time
import uuid
from pathlib import Path

from flask import Flask, jsonify, request, send_file, send_from_directory
from werkzeug.exceptions import HTTPException

from .catalog import MAX_FILE_BYTES, UserError, read_pdf
from .gemini import call_gemini
from .search import cache_key, fast_search, index_status, reuse_result
from .storage import DEFAULT_MODEL, Storage


def catalog_summary(catalog):
    if not catalog:
        return None
    if catalog.get("source_type") == "pdf":
        return {key: value for key, value in catalog.items() if key != "pages"}
    # Older installations keep their catalogue and history until explicitly replaced.
    return {**{key: value for key, value in catalog.items() if key != "products"}, "source_type": "legacy", "count": len(catalog.get("products", []))}


def create_app(data_dir, static_dir=None, gemini_caller=call_gemini):
    static_dir = Path(static_dir or Path(__file__).parent.parent / "static")
    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = MAX_FILE_BYTES + 64 * 1024
    storage = Storage(data_dir)
    app.extensions["storage"] = storage
    uploads, jobs = {}, {}
    state_lock = threading.RLock()

    @app.before_request
    def local_only():
        if request.host.split(":")[0] not in ("127.0.0.1", "localhost"):
            return jsonify(error="Приложение доступно только на этом компьютере."), 403
        if request.method in ("POST", "PUT", "DELETE", "PATCH"):
            if request.headers.get("X-Torman-Request") != "1":
                return jsonify(error="Запрос заблокирован. Обновите страницу приложения."), 403
            origin = request.headers.get("Origin")
            if origin and origin != request.host_url.rstrip("/"):
                return jsonify(error="Запрос с другого сайта заблокирован."), 403

    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(UserError)
    def user_error(error):
        return jsonify(error=str(error)), 400

    @app.errorhandler(HTTPException)
    def http_error(error):
        messages = {413: "Файл слишком большой. Максимальный размер — 20 МБ.", 404: "Данные не найдены.", 400: "Некорректный запрос."}
        return jsonify(error=messages.get(error.code, "Запрос не поддерживается.")), error.code

    @app.errorhandler(Exception)
    def unexpected_error(error):
        app.logger.exception("Local application error")
        return jsonify(error="Не удалось выполнить действие. Перезапустите программу и попробуйте снова."), 500

    def body():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise UserError("Некорректный формат запроса.")
        return data

    @app.get("/")
    def index():
        return send_from_directory(static_dir, "index.html")

    @app.get("/assets/<path:filename>")
    def asset(filename):
        return send_from_directory(static_dir, filename)

    @app.get("/api/state")
    def state():
        with state_lock:
            active = next((job["id"] for job in jobs.values() if job["status"] == "running"), None)
        catalog, settings = storage.catalog(), storage.settings()
        return jsonify(catalog=catalog_summary(catalog), settings=settings, active_job=active,
                       index=index_status(storage, catalog, settings["model"]))

    @app.get("/api/catalog")
    def catalog_page():
        catalog = storage.catalog()
        if not catalog:
            return jsonify(pages=[], total=0, page=1, page_size=12)
        if catalog.get("source_type") != "pdf":
            return jsonify(source_type="legacy", pages=[], total=0, page=1, page_size=12)
        query = request.args.get("q", "").strip().casefold()
        pages = catalog["pages"]
        if query:
            pages = [p for p in pages if query in p["text"].casefold()]
        try:
            page = max(1, int(request.args.get("page", 1)))
        except ValueError:
            raise UserError("Неверный номер страницы.")
        return jsonify(source_type="pdf", pages=pages[(page - 1) * 12:page * 12], total=len(pages), page=page, page_size=12, warnings=catalog["warnings"])

    @app.get("/api/catalog/original")
    def original():
        catalog, original = storage.catalog_with_original()
        if not catalog:
            raise UserError("Сначала загрузите каталог.")
        return send_file(io.BytesIO(original), as_attachment=True, download_name=catalog["filename"])

    @app.get("/api/catalog/document")
    def document():
        catalog, original = storage.catalog_with_original()
        if not catalog or catalog.get("source_type") != "pdf":
            raise UserError("Загрузите PDF-каталог.")
        if request.args.get("catalog_id") != catalog["id"]:
            raise UserError("Этот подбор относится к другому PDF. Каталог был заменён.")
        return send_file(io.BytesIO(original), mimetype="application/pdf", as_attachment=False, download_name=catalog["filename"])

    @app.post("/api/upload")
    def upload():
        file = request.files.get("file")
        if not file or not file.filename:
            raise UserError("Выберите файл PDF.")
        filename = file.filename.replace("\\", "/").split("/")[-1][:200]
        content = file.read()
        if not content:
            raise UserError("Файл пустой.")
        catalog = read_pdf(content, filename)
        upload_id = uuid.uuid4().hex
        with state_lock:
            for key in list(uploads):
                if time.monotonic() - uploads[key]["created"] > 1800:
                    del uploads[key]
            if len(uploads) >= 3:
                del uploads[next(iter(uploads))]
            uploads[upload_id] = {"catalog": catalog, "original": content, "filename": filename, "created": time.monotonic()}
        return jsonify(id=upload_id, filename=filename, **catalog_summary(catalog), pages=catalog["pages"][:3])

    def parsed_upload(data):
        with state_lock:
            upload = uploads.get(data.get("upload_id"))
        if not upload or time.monotonic() - upload["created"] > 1800:
            raise UserError("Время предварительного просмотра истекло. Выберите файл заново.")
        return upload, upload["catalog"]

    @app.post("/api/upload/preview")
    def preview():
        _, catalog = parsed_upload(body())
        return jsonify(**catalog_summary(catalog), pages=catalog["pages"][:3])

    @app.get("/api/upload/<upload_id>/document")
    def uploaded_document(upload_id):
        upload, _ = parsed_upload({"upload_id": upload_id})
        return send_file(io.BytesIO(upload["original"]), mimetype="application/pdf", as_attachment=False, download_name=upload["filename"])

    @app.post("/api/upload/commit")
    def commit():
        data = body()
        upload, catalog = parsed_upload(data)
        catalog = storage.save_catalog(catalog, upload["original"], upload["filename"])
        with state_lock:
            uploads.pop(data["upload_id"], None)
        return jsonify(catalog=catalog_summary(catalog))

    @app.post("/api/settings")
    def settings():
        data = body()
        model = data.get("model", DEFAULT_MODEL)
        key = data.get("key")
        if not isinstance(model, str) or not re.fullmatch(r"gemini-[a-zA-Z0-9._-]{2,100}", model):
            raise UserError("Укажите название модели, например gemini-3.8-flash.")
        if key is not None and (not isinstance(key, str) or len(key) > 512 or any(c.isspace() for c in key)):
            raise UserError("API-ключ не должен содержать пробелы или переносы строк.")
        return jsonify(storage.save_settings(model, key, bool(data.get("remember", True))))

    @app.post("/api/settings/test")
    def test_settings():
        model, key, _ = storage.credentials()
        if not key:
            raise UserError("Сначала сохраните API-ключ Gemini.")
        answer = gemini_caller(key, model, "Reply with OK.", schema=None,
                               system="This is a connection test. Reply with OK.", max_tokens=1024, fast=True)
        if not isinstance(answer, dict) or not isinstance(answer.get("text"), str) or not answer["text"].strip():
            raise UserError("Gemini ответил без текста. Повторите проверку подключения.")
        return jsonify(ok=True)

    @app.post("/api/search")
    def search():
        data = body()
        query = data.get("query", "")
        if not isinstance(query, str) or not 3 <= len(query.strip()) <= 3000:
            raise UserError("Опишите нужный товар: от 3 до 3000 символов.")
        direct = data.get("direct", False)
        if type(direct) is not bool:
            raise UserError("Некорректный режим поиска.")
        catalog = storage.catalog()
        if not catalog:
            raise UserError("Сначала загрузите PDF-каталог с товарами.")
        if catalog.get("source_type") != "pdf":
            raise UserError("В этой версии нужен PDF-каталог. Загрузите его вместо прежнего файла.")
        model, key, _ = storage.credentials()
        if not direct:
            cached = storage.cached_search(cache_key(catalog["id"], model, query))
            if cached:
                result = reuse_result(cached, query.strip())
                storage.save_search(result)
                return jsonify(result=result)
        if not key:
            raise UserError("Добавьте API-ключ Gemini в настройках.")
        catalog, original = storage.catalog_with_original()
        if not catalog or catalog.get("source_type") != "pdf":
            raise UserError("Каталог изменился. Обновите страницу и повторите подбор.")
        with state_lock:
            if any(job["status"] == "running" for job in jobs.values()):
                raise UserError("Предыдущий подбор ещё выполняется.")
            for job_id in list(jobs)[:-10]:
                del jobs[job_id]
            job_id = uuid.uuid4().hex
            cancel = threading.Event()
            job = {"id": job_id, "status": "running", "completed": 0, "total": 1, "message": "Начинаем подбор", "cancel": cancel}
            jobs[job_id] = job

        def progress(completed, total, message):
            with state_lock:
                job.update(completed=completed, total=total, message=message)

        def run():
            try:
                result = fast_search(storage, catalog, original, query.strip(), key, model, progress, cancel,
                                     caller=gemini_caller, direct=direct)
                if cancel.is_set():
                    raise UserError("Подбор остановлен.")
                storage.save_search(result)
                with state_lock:
                    job.update(status="done", result=result)
            except UserError as exc:
                with state_lock:
                    job.update(status="error", error=str(exc))
            except Exception:
                app.logger.exception("Search failed")
                with state_lock:
                    job.update(status="error", error="Не удалось завершить подбор. Повторите запрос.")

        threading.Thread(target=run, daemon=True).start()
        return jsonify(id=job_id), 202

    @app.get("/api/jobs/<job_id>")
    def get_job(job_id):
        with state_lock:
            job = jobs.get(job_id)
            if not job:
                raise UserError("Подбор не найден. Возможно, программа была перезапущена.")
            return jsonify({key: value for key, value in job.items() if key != "cancel"})

    @app.post("/api/jobs/<job_id>/cancel")
    def cancel_job(job_id):
        with state_lock:
            if job_id in jobs:
                jobs[job_id]["cancel"].set()
                jobs[job_id]["message"] = "Останавливаем после текущего обращения к Gemini"
        return jsonify(ok=True)

    @app.get("/api/history")
    def history():
        return jsonify(items=storage.history())

    return app
