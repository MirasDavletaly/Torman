"""Local PDF validation and page previews. Gemini receives the original PDF."""
import io
from pathlib import Path

from pypdf import PdfReader

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_PAGES = 300
MAX_PAGE_TEXT = 8000
MAX_PREVIEW_TEXT = 1_000_000


class UserError(Exception):
    pass


def read_pdf(content, filename):
    if Path(filename).suffix.lower() != ".pdf":
        raise UserError("Выберите каталог в формате .pdf.")
    if not content:
        raise UserError("Файл пустой.")
    if len(content) > MAX_FILE_BYTES:
        raise UserError("Максимальный размер PDF — 20 МБ.")
    if b"%PDF-" not in content[:1024]:
        raise UserError("Файл не является PDF. Одного переименования расширения недостаточно.")
    try:
        reader = PdfReader(io.BytesIO(content), strict=False)
        if reader.is_encrypted:
            raise UserError("PDF защищён паролем. Загрузите копию без парольной защиты.")
        page_count = len(reader.pages)
        if not page_count:
            raise UserError("В PDF нет страниц.")
        if page_count > MAX_PAGES:
            raise UserError(f"В PDF больше {MAX_PAGES} страниц. Разделите каталог на несколько файлов.")
        pages, warnings, unavailable = [], [], []
        remaining = MAX_PREVIEW_TEXT
        for number, page in enumerate(reader.pages, 1):
            text = ""
            truncated = False
            if remaining > 0:
                try:
                    raw_text = page.extract_text() or ""
                    text = raw_text[:min(MAX_PAGE_TEXT, remaining)].strip()
                    truncated = len(raw_text) > min(MAX_PAGE_TEXT, remaining)
                    remaining -= len(text)
                except Exception:
                    unavailable.append(number)
            else:
                truncated = True
            pages.append({"number": number, "text": text, "has_text": bool(text), "truncated": truncated})
        no_text = sum(not p["has_text"] for p in pages)
        if no_text:
            warnings.append(f"На {no_text} стр. нет доступного текстового предпросмотра. Это может быть скан, изображение или пустая страница. Gemini получит сам PDF и прочитает видимое содержимое.")
        if any(p["truncated"] for p in pages):
            warnings.append("Текст предпросмотра сокращён. Для подбора в Gemini передаётся полный PDF.")
        if unavailable:
            warnings.append("Не удалось извлечь текст некоторых страниц для предпросмотра. Исходный PDF сохранён полностью.")
        return {
            "source_type": "pdf", "page_count": page_count, "size_bytes": len(content),
            "text_page_count": page_count - no_text, "pages": pages, "warnings": warnings
        }
    except UserError:
        raise
    except Exception as exc:
        raise UserError("Не удалось открыть PDF. Проверьте, что файл не повреждён и открывается в программе для просмотра PDF.") from exc
