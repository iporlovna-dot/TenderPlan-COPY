"""Интерфейс источника закупок. Каждая площадка реализует его отдельно,
ядро/API работают только через этот контракт (заменяемость источников)."""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.schema import Purchase


class Downloaded:
    def __init__(self, filename: str, content_type: str, content: bytes):
        self.filename = filename
        self.content_type = content_type
        self.content = content


class Source(ABC):
    #: человекочитаемое имя площадки (попадает в Purchase.source)
    name: str = "источник"
    #: короткий код источника, префикс id (напр. "pp")
    code: str = "src"

    @abstractmethod
    async def search(self, *, take: int = 100, skip: int = 0) -> list[Purchase]:
        """Свежий пул активных закупок, нормализованных в Purchase.
        Текстовый поиск/фильтры применяются выше (в API), а не здесь —
        так одинаково работает для площадок без нормального серверного поиска."""

    @abstractmethod
    async def card(self, purchase_id: str) -> Purchase:
        """Полная карточка: позиции, сроки, обеспечение, документы."""

    @abstractmethod
    async def download(self, file_id: str) -> Downloaded:
        """Скачать вложение закупки по id файла."""
