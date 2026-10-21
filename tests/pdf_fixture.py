"""Small real PDFs for import and packaging checks; no Excel dependency."""
import io

from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject


def pdf_bytes(page_count=2, text=True, password=None):
    writer = PdfWriter()
    for number in range(1, page_count + 1):
        page = writer.add_blank_page(width=595, height=842)
        if text:
            font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
            page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
            stream = DecodedStreamObject()
            stream.set_data(f"BT /F1 14 Tf 50 760 Td (Pipe A{number} - Stainless steel - 50 mm - 25000 KZT) Tj ET".encode("ascii"))
            page[NameObject("/Contents")] = writer._add_object(stream)
    if password:
        writer.encrypt(password)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def product(page=1):
    return {"name": f"Pipe A{page}", "sku": f"A{page}", "pages": [page],
            "attributes": [{"name": "Diameter, mm", "value": "50"}, {"name": "Material", "value": "Stainless steel"}],
            "source_excerpt": f"Pipe A{page} - Stainless steel - 50 mm", "notes": ""}


def fake_gemini(key, model, prompt, **kwargs):
    if "schema" in kwargs and kwargs["schema"] is None:
        return {"text": "OK"}
    data = __import__("json").loads(prompt)
    if data.get("task") == "index":
        return {"products": [product(p) for p in data["target_pages"]], "covered_pages": data["target_pages"], "unreadable_pages": [], "complete": True}
    if data.get("task") in ("match", "rank"):
        return {"matches": [{"id": p["id"], "status": "exact", "reason": "Диаметр 50 мм, нержавеющая сталь", "evidence": ["Diameter, mm", "Material"], "missing": []} for p in data["products"][:10]]}
    return {"matches": [{**product(), "status": "exact", "reason": "Диаметр 50 мм", "missing": []}], "unreadable_pages": []}
