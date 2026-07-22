"""Адаптер источника закупок «Тендерплан» (реализация source.TenderSource).

Используем Тендерплан как ВРЕМЕННЫЙ, заменяемый источник для Этапа 0 (см. source.py):
собрать реальные ТЗ быстро, не строя интеграцию ЕИС. Наш дифференциатор
(извлечение+матчинг) от него не зависит.

Проверено probe'ом на реальном PAT со scope resources:external:
  • /api/tenders/v2/fullinfo?id=<_id> -> {tender, tenderInfo, lawyers, tasks};
    tender содержит okpd2/ktru/okpd, orderName, number, customers[], maxPrice, region.
    (/api/tenders/get даёт 403 — используем fullinfo.)
  • /api/tenders/attachments?id=<_id> -> [{displayName, href, realName, size, ...}];
    href — прямая ссылка на файл (внешний ЭТП), realName — имя файла. Часть — .rar/.zip.
  • Авторизация: заголовок Authorization: Bearer <PAT>.
  • Лимиты: 100/10с, 500/60с; getList ≤60/мин; tenders/get ≤300/мин; 429 при превышении.

DISCOVERY (шаги 1-2) — реализован в iter_new. Подтверждено probe'ом на PAT со scope
resources+keys+relations+marks: /api/tenders/v2/getlist отдаёт ФИД пользователя (тендеры
по всем его «ключам»), постранично (param `page`, по 50). Серверный фильтр по одному
ключу Тендерплан не поддерживает (/search/v2/list и keys= в getlist игнорируют фильтр),
поэтому листаем фид и отсеиваем нерелевантное нашим filter.score_purchase по ключевым
словам профиля. На тестовом фиде из 1250 тендеров так находятся реальные перчаточные ТЗ.

Токен: переменная окружения TENDERPLAN_TOKEN (в git не коммитить)."""
from __future__ import annotations

import json
import os
import time
from typing import Iterator, List, Optional

import httpx

from schema import Position, Purchase

BASE_URL = "https://tenderplan.ru"
# Собранный бандл certifi + Russian Trusted CA (см. scripts/setup_ca_bundle.py).
_CA_BUNDLE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "certs", "ru_trusted_bundle.pem")


def _resolve_verify():
    """Как проверять сертификаты при скачивании ТЗ. Проверка ВКЛючена по умолчанию.

    Приоритет: TENDERPLAN_CA_BUNDLE (явный путь) → готовый certs/ru_trusted_bundle.pem
    → True (системный certifi). Отключить проверку можно только явным
    TENDERPLAN_INSECURE_FILES=1 (для публичных файлов; на свой риск).
    """
    if os.environ.get("TENDERPLAN_INSECURE_FILES") == "1":
        return False
    env = os.environ.get("TENDERPLAN_CA_BUNDLE")
    if env:
        return env
    if os.path.exists(_CA_BUNDLE):
        return _CA_BUNDLE
    return True


class _RateLimiter:
    """Простой троттл: не чаще max_per_window запросов за window секунд (< лимита 100/10с)."""

    def __init__(self, max_per_window: int = 90, window: float = 10.0):
        self.max = max_per_window
        self.window = window
        self._calls: List[float] = []

    def wait(self) -> None:
        now = time.monotonic()
        self._calls = [t for t in self._calls if now - t < self.window]
        if len(self._calls) >= self.max:
            time.sleep(self.window - (now - self._calls[0]) + 0.01)
        self._calls.append(time.monotonic())


class TenderplanSource:
    """Источник закупок поверх API Тендерплана. Реализует source.TenderSource."""

    def __init__(self, token: Optional[str] = None, timeout: float = 30.0,
                 verify_files: Optional[bool] = None):
        token = token or os.environ.get("TENDERPLAN_TOKEN")
        if not token:
            raise RuntimeError(
                "Нет токена Тендерплана: задай TENDERPLAN_TOKEN (PAT из личного кабинета)")
        self._client = httpx.Client(
            base_url=BASE_URL,
            headers={"Authorization": "Bearer " + token},
            timeout=timeout,
        )
        # Клиент для скачивания файлов ТЗ с внешних ЭТП по href — БЕЗ нашего
        # Authorization (чужому хосту токен не шлём). Проверка сертификата ВКЛЮЧЕНА
        # по умолчанию. Часть рос. ЭТП использует сертификаты национального УЦ,
        # которых нет в доверенных у Python — на них скачивание упадёт с SSL-ошибкой.
        # Как решить, БЕЗ отключения проверки: добавить корневой сертификат Минцифры
        # в CA-бандл и указать путь в TENDERPLAN_CA_BUNDLE. Отключить проверку можно
        # только явным согласием: verify_files=False или TENDERPLAN_INSECURE_FILES=1.
        if verify_files is None:
            verify_files = _resolve_verify()
        # zakupki.gov.ru отклоняет запросы без браузерного User-Agent (отдаёт 404),
        # поэтому клиент скачивания представляется браузером.
        self._files = httpx.Client(
            timeout=timeout, follow_redirects=True, verify=verify_files,
            headers={"User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                                    "Chrome/122.0 Safari/537.36")},
        )
        self._rl = _RateLimiter()

    # --- транспорт --------------------------------------------------------

    def _request(self, method: str, path: str, *, retries: int = 2, **kw) -> httpx.Response:
        """HTTP с троттлом и ретраями на 429/5xx."""
        resp = None
        for attempt in range(retries + 1):
            self._rl.wait()
            resp = self._client.request(method, path, **kw)
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt < retries:
                    time.sleep(float(resp.headers.get("Retry-After", 2 ** attempt)))
                    continue
            resp.raise_for_status()
            return resp
        resp.raise_for_status()
        return resp

    # --- discovery (шаги 1-2) — требует scope keys + relations:read -------

    def iter_new(self, profile: dict, since: Optional[str] = None,
                 max_pages: int = 40) -> Iterator[Purchase]:
        """Поток закупок, релевантных профилю (discovery + грубый фильтр).

        Подтверждено probe'ом: /api/tenders/v2/getlist отдаёт ФИД пользователя —
        тендеры, сматченные под все его «ключи» Тендерплана, — постранично (param
        `page`, по 50). Серверный фильтр по одному ключу не поддержан, поэтому листаем
        фид и отсеиваем нерелевантное НАШИМ грубым фильтром (filter.score_purchase) по
        ключевым словам профиля. max_pages ограничивает объём за прогон (40*50=2000).
        """
        from filter import score_purchase  # локальный импорт: filter.py — часть ядра

        seen = set()
        for page in range(max_pages):
            data = self._request("GET", "/api/tenders/v2/getlist",
                                params={"limit": 50, "page": page}).json()
            tenders = data.get("tenders", [])
            new = [t for t in tenders if t.get("_id") not in seen]
            if not new:
                break
            for t in new:
                seen.add(t.get("_id"))
                purchase = _to_purchase(t)
                if score_purchase(purchase, profile) >= 1.0:
                    yield purchase

    # --- забор по id (шаги 4-5) — работает со scope resources:external ----

    def get_tender(self, tender_id: str) -> Purchase:
        """Полная карточка тендера по внутреннему id (_id Тендерплана) через fullinfo."""
        data = self._request("GET", "/api/tenders/v2/fullinfo",
                             params={"id": tender_id}).json()
        return _to_purchase(data.get("tender", {}))

    def position_ktru(self, tender_id: str) -> List[str]:
        """Коды КТРУ позиций закупки из fullinfo (ObjectInfo→Objects→таблица→Code).

        Отдельный дешёвый запрос (без LLM) для сверки товар↔позиция в воронке (`ktru.py`).
        Пусто, если структуры нет — вызывающий трактует как «не отсекать» (fail-open).
        """
        data = self._request("GET", "/api/tenders/v2/fullinfo",
                             params={"id": tender_id}).json()
        return _extract_position_codes(data)

    def positions(self, tender_id: str) -> List[Position]:
        """Позиции (лоты) закупки из fullinfo: Name/Code/Quantity/Price на позицию.

        Один дешёвый запрос (без LLM) закрывает и КТРУ-сверку в воронке (коды позиций),
        и пометку «позиция N из M» для многолотов (§11.4). Пусто, если структуры нет.
        """
        data = self._request("GET", "/api/tenders/v2/fullinfo",
                             params={"id": tender_id}).json()
        return _extract_positions(data)

    def download_attachments(self, purchase: Purchase, dest_dir: str) -> List[str]:
        """Скачать вложения тендера в dest_dir по прямым href. Возвращает пути к файлам."""
        os.makedirs(dest_dir, exist_ok=True)
        atts = self._request("GET", "/api/tenders/attachments",
                            params={"id": purchase.id}).json()
        saved: List[str] = []
        for att in atts if isinstance(atts, list) else []:
            href = att.get("href")
            if not href:
                continue
            name = att.get("realName") or att.get("displayName") or "attachment"
            self._rl.wait()
            r = self._files.get(href)
            if r.status_code != 200:
                continue
            path = os.path.join(dest_dir, "%s__%s" % (purchase.id, _safe(name)))
            with open(path, "wb") as f:
                f.write(r.content)
            saved.append(path)
        return saved

    def fetch_tz(self, tender_id: str, dest_dir: str) -> tuple:
        """Точка входа Этапа 0: карточка + скачанные файлы ТЗ по id. -> (Purchase, [пути])."""
        purchase = self.get_tender(tender_id)
        files = self.download_attachments(purchase, dest_dir)
        return purchase, files

    def close(self) -> None:
        self._client.close()
        self._files.close()


# --- маппинг ответов Тендерплана в наши модели (поля подтверждены probe) ---

def _to_purchase(t: dict) -> Purchase:
    """Модель tender из fullinfo -> наш Purchase."""
    customers = t.get("customers") or []
    customer = customers[0].get("name", "") if customers and isinstance(customers[0], dict) else ""
    codes_okpd = _as_str_list(t.get("okpd2")) + _as_str_list(t.get("okpd"))
    region = t.get("region")
    return Purchase(
        id=str(t.get("_id") or t.get("id") or ""),
        subject=str(t.get("orderName") or ""),
        okpd2=codes_okpd,
        ktru=_as_str_list(t.get("ktru")),
        customer=str(customer),
        price=_as_float(t.get("maxPrice")),
        law="",  # в модели нет явного поля ФЗ; определяется по placingWay/платформе позже
        attachments=[],
        region=str(region) if region not in (None, "") else None,
        submission_close=t.get("submissionCloseDateTime"),  # epoch ms
    )


def _find_object_table(fullinfo: dict) -> dict:
    """Таблица позиций из fullinfo. ObjectInfo лежит вложенной JSON-строкой — ищем её
    рекурсивно и достаём таблицу Objects (`tb`: строка = позиция, ячейки {fn, fv})."""
    found = None

    def walk(o):
        nonlocal found
        if found is not None:
            return
        if isinstance(o, str) and "ObjectInfo" in o and o.strip().startswith("{"):
            try:
                found = json.loads(o)
            except ValueError:
                pass
        elif isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(fullinfo)
    if not found:
        return {}
    try:
        tb = found["0"]["fv"]["0"]["fv"].get("tb", {})
    except (KeyError, TypeError, AttributeError):
        return {}
    return tb if isinstance(tb, dict) else {}


def _extract_positions(fullinfo: dict) -> List[Position]:
    """Позиции закупки из таблицы Objects: Name/Code/Quantity/Price на строку."""
    positions: List[Position] = []
    for row in _find_object_table(fullinfo).values():
        cells = {c.get("fn"): c.get("fv") for c in row.values()
                 if isinstance(c, dict)} if isinstance(row, dict) else {}
        name = cells.get("Name")
        code = cells.get("Code")
        if not (name or code):
            continue
        positions.append(Position(
            name=str(name or ""),
            code=str(code) if code not in (None, "") else "",
            quantity=str(cells.get("Quantity") or ""),
            price=_as_float(cells.get("Price")),
        ))
    return positions


def _extract_position_codes(fullinfo: dict) -> List[str]:
    """Коды КТРУ позиций (колонка Code) — для сверки товар↔позиция в воронке."""
    return [p.code for p in _extract_positions(fullinfo) if p.code]


def _as_str_list(v) -> List[str]:
    if isinstance(v, list):
        return [str(x) for x in v]
    return [] if v in (None, "") else [str(v)]


def _as_float(v) -> Optional[float]:
    try:
        return float(str(v).replace(" ", "").replace(",", ".")) if v not in (None, "") else None
    except (ValueError, TypeError):
        return None


def _safe(name: str) -> str:
    """Убрать разделители пути из имени файла."""
    return name.replace("/", "_").replace("\\", "_").strip() or "attachment"
