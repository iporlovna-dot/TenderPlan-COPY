"""Probe API Тендерплана: снять РЕАЛЬНЫЕ схемы ответов, чтобы зафиксировать маппинг.

Зачем: спека Тендерплана за авторизацией, точные имена полей неизвестны. Адаптер
src/tenderplan.py помечает такие места VERIFY. Этот скрипт дёргает эндпоинты твоим
токеном и сохраняет сырые JSON в scratchpad — по ним правим _to_purchase /
_build_search_body / _search_items и т.д. под реальность, а не под догадки.

Запуск:
    export TENDERPLAN_TOKEN=<PAT из личного кабинета Тендерплана>
    .venv/bin/python scripts/tp_probe.py data/profiles/gloves.json

Сохраняет ответы в scratchpad/tp_probe/*.json и печатает верхнеуровневые ключи.
Ничего не изменяет на стороне Тендерплана — только GET/поиск (read-only).
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import httpx  # noqa: E402

BASE_URL = "https://tenderplan.ru"
# Сырые ответы кладём в gitignored-папку внутри проекта (не в git — могут быть ПДн).
OUT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scratchpad", "tp_probe"))


def _dump(name, obj):
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.abspath(os.path.join(OUT_DIR, name + ".json"))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    top = list(obj.keys()) if isinstance(obj, dict) else "[список из %d]" % len(obj)
    print("  сохранено %s  | верхние ключи: %s" % (path, top))


def main():
    token = os.environ.get("TENDERPLAN_TOKEN")
    if not token:
        sys.exit("Задай TENDERPLAN_TOKEN (PAT из личного кабинета Тендерплана)")

    profile = json.load(open(sys.argv[1], encoding="utf-8")) if len(sys.argv) > 1 else {}
    client = httpx.Client(base_url=BASE_URL,
                          headers={"Authorization": "Bearer " + token}, timeout=30.0)

    # Авторизацию проверяем сразу боевым методом на scope resources:external —
    # чтобы одной галки resources:external при генерации PAT было достаточно.
    # 401 = плохой токен; 403 = токену не хватает scope resources:external.

    # Тело поиска пробуем в нескольких вероятных формах — какая вернёт 200, ту и фиксируем.
    kw = " ".join(profile.get("keywords", []) or ["перчатки нитриловые"])
    codes = profile.get("okpd2_ktru", []) or []
    candidates = [
        {"query": kw, "limit": 5},
        {"text": kw, "limit": 5},
        {"keywords": kw, "limit": 5},
        {"query": kw, "okpd2": codes, "limit": 5},
    ]
    print("→ поиск/авторизация: POST /api/search/v2/list (перебор форм тела фильтра)")
    ok = False
    for i, body in enumerate(candidates):
        r = client.post("/api/search/v2/list", json=body)
        print("  форма #%d %s -> %d" % (i, list(body.keys()), r.status_code))
        if r.status_code == 401:
            sys.exit("401 — токен неверный/просрочен, проверь PAT")
        if r.status_code == 403:
            sys.exit("403 — токену не хватает scope resources:external, перегенерируй PAT")
        if r.status_code == 200:
            _dump("10_search_%d" % i, r.json())
            ok = True
            break
    if not ok:
        print("  ни одна форма не вернула 200 — пришли вывод, разберём тело фильтра")

    # Если удалось получить хоть один tender id — пробуем карточку и вложения.
    tender_id = None
    try:
        data = json.load(open(os.path.abspath(
            os.path.join(OUT_DIR, "10_search_0.json")), encoding="utf-8"))
    except Exception:
        data = None
    if isinstance(data, dict):
        for v in data.values():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                tender_id = v[0].get("id") or v[0].get("_id") or v[0].get("regNumber")
                break

    if tender_id:
        print("→ карточка: GET /api/tenders/get id=%s" % tender_id)
        r = client.get("/api/tenders/get", params={"id": tender_id})
        print("  статус:", r.status_code)
        if r.status_code == 200:
            _dump("20_tender_get", r.json())

        print("→ вложения: GET /api/tenders/attachments id=%s" % tender_id)
        r = client.get("/api/tenders/attachments", params={"id": tender_id})
        print("  статус:", r.status_code)
        if r.status_code == 200:
            _dump("30_attachments", r.json())
    else:
        print("→ tender id не извлечён из поиска — пришли 10_search_*.json, разберём вручную")

    client.close()
    print("\nГотово. Пришли содержимое scratchpad/tp_probe/*.json — зафиксирую VERIFY-поля.")


if __name__ == "__main__":
    main()
