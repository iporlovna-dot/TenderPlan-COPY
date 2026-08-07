"""Интерфейс источника закупок (шаги 1 и 4, plan.md §3, §10).

Источник абстрагирован НАРОЧНО: сегодня данные берём из Тендерплана (быстрый старт,
plan.md §10 «абстракция источника»), завтра — из официальной интеграции ЕИС. Ядро
(parser → extractor → matcher) от источника не зависит: ему нужны Purchase + файлы ТЗ,
откуда они пришли — неважно. Это защищает от вендор-локина на конкурента (Тендерплан
назван конкурентом в §1) — меняется только адаптер, не движок.
"""
from __future__ import annotations

from typing import Iterator, List, Optional, Protocol

from schema import Purchase


class TenderSource(Protocol):
    """Контракт любого источника закупок. Реализации: TenderplanSource, (позже) EisSource."""

    def iter_new(self, profile: dict, since: Optional[str] = None) -> Iterator[Purchase]:
        """Поток релевантных профилю закупок (discovery + грубый фильтр, шаги 1-2).

        profile — профиль категории (data/profiles/*.json): коды ОКПД2/КТРУ,
        ключевые слова. since — необязательная нижняя граница по дате публикации
        (инкрементальный сбор). Возвращает Purchase без скачанных файлов —
        вложения тянутся отдельно, только для финалистов воронки.
        """
        ...

    def download_attachments(self, purchase: Purchase, dest_dir: str) -> List[str]:
        """Скачать вложения ТЗ закупки в dest_dir (шаг 4). Возвращает пути к файлам.

        Только текстовые документы (docx/pdf/xls) — их дальше ест parser.py.
        Вызывается точечно, на финалистов, а не на весь поток.
        """
        ...
