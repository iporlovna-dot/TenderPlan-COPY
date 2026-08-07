"""OCR сканов-PDF (шаг 5 конвейера, plan.md §5). БЕЗ генеративного LLM.

Часть ТЗ на zakupki.gov.ru — сканы без текстового слоя. Здесь распознаём их в текст,
чтобы не терять контракты. Рендер страниц PDF в картинки — через PyMuPDF (fitz),
без системного poppler.

Два бэкенда, выбираются автоматически:
  • tesseract — если в системе есть бинарь `tesseract` (+ pytesseract). Лёгкий, как в плане.
  • easyocr   — pip-only fallback с нативной поддержкой русского (когда tesseract не поставить).
Кривые/сложные сканы позже можно эскалировать на Claude vision (нужен ANTHROPIC_API_KEY).
"""
from __future__ import annotations

import io
import shutil
from typing import List, Optional

_LANGS_TESS = "rus+eng"       # языки для tesseract
_LANGS_EASY = ["ru", "en"]    # языки для easyocr
_easy_reader = None            # ленивый синглтон EasyOCR (модель грузится один раз)


def available_backend() -> Optional[str]:
    """Какой OCR-бэкенд доступен: 'tesseract' | 'easyocr' | None."""
    if shutil.which("tesseract"):
        try:
            import pytesseract  # noqa: F401
            import fitz  # noqa: F401
            return "tesseract"
        except ImportError:
            pass
    try:
        import easyocr  # noqa: F401
        import fitz  # noqa: F401
        return "easyocr"
    except ImportError:
        return None


def ocr_pdf(path: str, dpi: int = 300) -> str:
    """Скан-PDF → распознанный текст. Бэкенд выбирается автоматически."""
    backend = available_backend()
    if backend is None:
        raise RuntimeError(
            "OCR недоступен: нет ни системного tesseract, ни пакета easyocr. "
            "Поставь `pip install easyocr` или системный tesseract (+rus).")
    images = _pdf_to_pngs(path, dpi)
    if not images:
        return ""
    return _ocr_tesseract(images) if backend == "tesseract" else _ocr_easyocr(images)


def _pdf_to_pngs(path: str, dpi: int) -> List[bytes]:
    """Рендер каждой страницы PDF в PNG-байты (PyMuPDF, без poppler)."""
    import fitz

    doc = fitz.open(path)
    pngs = [page.get_pixmap(dpi=dpi).tobytes("png") for page in doc]
    doc.close()
    return pngs


def _ocr_tesseract(pngs: List[bytes]) -> str:
    import pytesseract
    from PIL import Image

    out = []
    for png in pngs:
        out.append(pytesseract.image_to_string(Image.open(io.BytesIO(png)), lang=_LANGS_TESS))
    return "\n\n".join(t.strip() for t in out if t.strip())


def _ocr_easyocr(pngs: List[bytes]) -> str:
    global _easy_reader
    import easyocr
    import numpy as np
    from PIL import Image

    if _easy_reader is None:
        _easy_reader = easyocr.Reader(_LANGS_EASY, gpu=False)  # первый вызов качает модель
    out = []
    for png in pngs:
        img = np.array(Image.open(io.BytesIO(png)).convert("RGB"))
        lines = _easy_reader.readtext(img, detail=0, paragraph=True)
        out.append("\n".join(lines))
    return "\n\n".join(t.strip() for t in out if t.strip())


if __name__ == "__main__":
    import sys

    print("бэкенд:", available_backend())
    if len(sys.argv) > 1:
        print(ocr_pdf(sys.argv[1]))
