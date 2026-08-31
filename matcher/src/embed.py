"""Локальные эмбеддинги для семантики воронки — БЕЗ облака, БЕЗ платы за запрос (plan.md §3.7, §13).

Зачем: `keymatch.align_keys` сводил имена полей ТЗ↔карточки через Haiku — это (1) недетерминированно
(корень разброса live-% АКОД 97↔81), (2) платно на объёме, (3) шлёт поля ТЗ в облако. Локальная
эмбеддинг-модель (bge-m3 / multilingual-e5) считает те же соответствия детерминированно, бесплатно и
приватно: один вход → один вектор → стабильный маппинг.

Модуль спроектирован так, чтобы импортироваться ДАЖЕ без `sentence-transformers` И БЕЗ `numpy`:
если пакет/модель недоступны, `default_embedder()` вернёт None, и вызывающий код (keymatch) мягко
деградирует на прежний LLM-путь (или, при `llm_fallback=False`, просто оставляет остаток
несведённым). Тяжёлую модель грузим лениво (первый `encode`), один раз на процесс.

⚠️ `numpy` — тоже опциональный: детерминированный слой `keymatch.align_keys` (морфология имени,
без эмбеддингов вообще) не должен требовать его лишь потому, что этот модуль лежит рядом на
пути импорта. sentence-transformers сам тянет numpy транзитивно, так что деградация здесь той же
формы, что и для sentence-transformers выше — просто на уровень раньше.

Env:
- SPECMATCH_EMBED_MODEL — id модели (по умолчанию bge-m3). e5-модели получают префикс «query:».
- SPECMATCH_EMBED=off  — принудительно отключить эмбеддинги (тогда keymatch идёт через LLM).
"""
from __future__ import annotations

import os
from typing import List, Optional

try:
    import numpy as np
except ImportError:  # numpy не гарантирован без sentence-transformers — см. предупреждение выше
    np = None  # type: ignore

_DEFAULT_MODEL = os.getenv("SPECMATCH_EMBED_MODEL", "BAAI/bge-m3")


class Embedder:
    """Обёртка над sentence-transformers: текст → L2-нормированный вектор.

    Единый интерфейс `encode(texts) -> np.ndarray` (строки нормированы, косинус = скалярное
    произведение). Инъектируется в keymatch для тестов (FakeEmbedder с тем же `encode`)."""

    def __init__(self, model_name: str = _DEFAULT_MODEL):
        from sentence_transformers import SentenceTransformer  # тяжёлый импорт — лениво, в __init__

        self.model_name = model_name
        self._model = SentenceTransformer(model_name)
        # e5-семейство обучено с префиксами «query:/passage:»; для симметричного сходства имён
        # полей префиксуем обе стороны «query:». bge-m3 и прочие префикса не требуют.
        self._prefix = "query: " if "e5" in model_name.lower() else ""

    def encode(self, texts: List[str]) -> np.ndarray:
        """Список строк → матрица (n, d) L2-нормированных векторов."""
        prepared = [self._prefix + t for t in texts]
        vecs = self._model.encode(prepared, normalize_embeddings=True, show_progress_bar=False)
        return np.asarray(vecs, dtype=np.float32)


_singleton: Optional[Embedder] = None
_tried = False


def default_embedder() -> Optional[Embedder]:
    """Singleton-эмбеддер по умолчанию или None, если недоступен/отключён.

    None — легитимный результат: вызывающий (keymatch) деградирует на LLM. Одна попытка загрузки
    на процесс: неуспех (нет пакета/модели/сети) кэшируется как None, повторно не пытаемся."""
    global _singleton, _tried
    if os.getenv("SPECMATCH_EMBED", "").lower() == "off":
        return None
    if _tried:
        return _singleton
    _tried = True
    if np is None:
        _singleton = None  # numpy недоступен — sentence-transformers тем более не заработает
        return _singleton
    try:
        _singleton = Embedder()
    except Exception:
        _singleton = None  # нет sentence-transformers / модель не скачалась / оффлайн — мягко в None
    return _singleton


def cosine_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Матрица косинусных сходств между строками a и b (обе уже L2-нормированы)."""
    return a @ b.T
