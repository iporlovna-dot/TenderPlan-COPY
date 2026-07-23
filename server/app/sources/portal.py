"""Адаптер «Портал поставщиков» (zakupki.mos.ru).

Все эндпоинты публичные (без токена), проверены вживую 2026-07-23:
  поиск     GET old.zakupki.mos.ru/api/Cssp/Purchase/Query?queryDto=<json>
  карточка  GET zakupki.mos.ru/newapi/api/Auction/Get?auctionId=<id>
  файл      GET zakupki.mos.ru/newapi/api/FileStorage/Download?id=<fileId>
"""

from __future__ import annotations

import json
from datetime import datetime

import httpx

from app.schema import Document, Lot, Purchase
from app.sources.base import Downloaded, Source

OLD_API = "https://old.zakupki.mos.ru/api"
NEW_API = "https://zakupki.mos.ru/newapi/api"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# state 19000002 = «Активная» (идёт подача). tradeType/typeIn 1 = котировочная сессия.
ACTIVE_STATE = 19000002
KS_TYPE = 1
DAY = 86400.0


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.strptime(s[:19], "%d.%m.%Y %H:%M:%S")
    except ValueError:
        return None


def _clean_region(name: str | None) -> str:
    name = (name or "").strip()
    if name.startswith("г ") and not name.startswith("г. "):
        name = "г. " + name[2:]
    return name


class PortalPostavshikov(Source):
    name = "Портал поставщиков (mos.ru)"
    code = "pp"

    def __init__(self, client: httpx.AsyncClient):
        self._c = client

    # ---------- поиск (реестр) ----------

    async def search(self, *, take: int = 100, skip: int = 0) -> list[Purchase]:
        query_dto = json.dumps({
            "filter": {
                "typeIn": {"values": [KS_TYPE]},
                "auctionSpecificFilter": {"stateIdIn": [ACTIVE_STATE]},
            },
            "order": [{"field": "endDate", "desc": False}],
            "withCount": True,
            "take": take,
            "skip": skip,
        }, ensure_ascii=False)

        r = await self._c.get(
            f"{OLD_API}/Cssp/Purchase/Query",
            params={"queryDto": query_dto},
            headers={"User-Agent": UA, "Accept": "application/json"},
        )
        r.raise_for_status()
        data = r.json()
        return [self._map_list_item(it) for it in data.get("items", [])]

    def _map_list_item(self, it: dict) -> Purchase:
        now = datetime.now()
        end = _parse_dt(it.get("endDate"))
        begin = _parse_dt(it.get("beginDate"))
        deadline = max(0, int((end - now).total_seconds() // DAY) + 1) if end else 0
        published = max(0, int((now - begin).total_seconds() // DAY)) if begin else 0
        cust = (it.get("customers") or [{}])[0]
        price = float(it.get("startPrice") or 0)
        aid = it.get("auctionId")
        return Purchase(
            id=f"{self.code}_{aid}",
            number=str(it.get("number") or aid),
            title=it.get("name") or "Котировочная сессия",
            customer=cust.get("name") or "—",
            customerInn=cust.get("inn") or "",
            law=it.get("federalLawName") or "44-ФЗ",
            source=self.name,
            region=_clean_region(it.get("regionName")),
            price=price,
            stage="active" if it.get("stateId") == ACTIVE_STATE else "committee",
            deadlineDays=deadline,
            publishedDaysAgo=published,
            href=f"https://zakupki.mos.ru/auction/{aid}",
            lots=[Lot(name=it.get("name") or "Позиция", price=price)],
        )

    # ---------- карточка ----------

    async def card(self, purchase_id: str) -> Purchase:
        aid = purchase_id.split("_", 1)[-1]
        r = await self._c.get(
            f"{NEW_API}/Auction/Get",
            params={"auctionId": aid},
            headers={"User-Agent": UA, "Accept": "application/json"},
        )
        r.raise_for_status()
        d = r.json()

        now = datetime.now()
        end = _parse_dt_iso(d.get("endDate"))
        begin = _parse_dt_iso(d.get("startDate"))
        deadline = max(0, int((end - now).total_seconds() // DAY) + 1) if end else 0
        published = max(0, int((now - begin).total_seconds() // DAY)) if begin else 0
        cust = d.get("customer") or {}

        lots = [
            Lot(
                name=i.get("name") or "Позиция",
                qty=str(i.get("currentValue") or "—"),
                price=float(i.get("costPerUnit") or 0),
                okpd=(i.get("okpdName") or ""),
            )
            for i in (d.get("items") or [])
        ]
        docs = [
            Document(
                id=str(f.get("id")),
                name=f.get("name") or f"файл {f.get('id')}",
                url=f"/api/documents/{f.get('id')}",
            )
            for f in (d.get("files") or [])
        ]
        okpd = lots[0].okpd if lots else ""
        price = float(d.get("startCost") or (lots[0].price if lots else 0) or 0)
        return Purchase(
            id=f"{self.code}_{d.get('id')}",
            number=str(d.get("id")),
            title=d.get("name") or "Котировочная сессия",
            customer=cust.get("name") or "—",
            customerInn=cust.get("inn") or "",
            law=d.get("federalLawName") or "44-ФЗ",
            source=self.name,
            region=_clean_region((d.get("auctionRegion") or {}).get("name")),
            okpd=okpd,
            price=price,
            stage="active" if (end and end > now) else "committee",
            deadlineDays=deadline,
            publishedDaysAgo=published,
            guaranteeContract=float(d.get("contractGuaranteeAmount") or 0),
            href=f"https://zakupki.mos.ru/auction/{d.get('id')}",
            lots=lots,
            documents=docs,
        )

    # ---------- скачивание файла ----------

    async def download(self, file_id: str) -> Downloaded:
        r = await self._c.get(
            f"{NEW_API}/FileStorage/Download",
            params={"id": file_id},
            headers={"User-Agent": UA},
        )
        r.raise_for_status()
        ctype = r.headers.get("content-type", "application/octet-stream")
        # имя файла из Content-Disposition, если есть
        cd = r.headers.get("content-disposition", "")
        filename = f"document_{file_id}"
        if "filename" in cd:
            part = cd.split("filename")[-1].strip("=*\"' ;")
            if part:
                filename = part
        return Downloaded(filename=filename, content_type=ctype, content=r.content)


def _parse_dt_iso(s: str | None) -> datetime | None:
    """Карточка Auction/Get отдаёт даты в ISO (2026-07-24T13:08:08...)."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "").split(".")[0])
    except ValueError:
        return _parse_dt(s)
