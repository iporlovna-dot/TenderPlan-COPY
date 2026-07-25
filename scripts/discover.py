"""Автономный поиск процедур под наши товары (discovery). plan.md Этап 1.

Гоняет `TenderplanSource.iter_new` по профилям категорий (фид Тендерплана + фильтр
профиля по ключевым словам и стоп-словам) и печатает найденные тендеры СО ССЫЛКАМИ на
Тендерплан — чтобы фаундер открыл на детальное изучение, а подтверждённые прогнать через
lot_coverage/check_tender.

По умолчанию — ФОКУС-категории (клинки/рукояти = laryngoscope, амбу, халаты); перчатки
НЕ ищем (договорённость, memory focus-categories). Между профилями пауза — бережём rate limit.

Запуск:
    .venv/bin/python scripts/discover.py                 # фокус-профили
    .venv/bin/python scripts/discover.py --profiles laryngoscope ambu --max-pages 30
    .venv/bin/python scripts/discover.py --all           # все профили в data/profiles

Ключи в окружении: TENDERPLAN_TOKEN.
"""
import argparse
import glob
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tenderplan import TenderplanSource  # noqa: E402

FOCUS = ["laryngoscope", "ambu", "gown"]          # клинки/рукояти, амбу, халаты
SKIP = {"gloves"}                                  # перчатки не ищем (см. focus-categories)
TP_URL = "https://tenderplan.ru/app?tender=%s"


def _profiles(args):
    dirp = os.path.join(os.path.dirname(__file__), "..", "data", "profiles")
    if args.profiles:
        names = args.profiles
    elif args.all:
        names = [os.path.basename(p)[:-5] for p in sorted(glob.glob(os.path.join(dirp, "*.json")))]
        names = [n for n in names if n not in SKIP]
    else:
        names = FOCUS
    return [(n, os.path.join(dirp, n + ".json")) for n in names]


def main():
    ap = argparse.ArgumentParser(description="Автономный поиск процедур под наши товары")
    ap.add_argument("--profiles", nargs="*", help="имена профилей (по умолчанию фокус-категории)")
    ap.add_argument("--all", action="store_true", help="все профили data/profiles (кроме перчаток)")
    ap.add_argument("--max-pages", type=int, default=20, help="страниц фида на профиль")
    ap.add_argument("--limit", type=int, default=20, help="макс. тендеров на профиль в выводе")
    ap.add_argument("--pause", type=float, default=2.0, help="пауза (сек) между профилями — бережём rate limit")
    args = ap.parse_args()

    src = TenderplanSource(timeout=60)
    seen = set()
    total = 0
    try:
        for i, (name, path) in enumerate(_profiles(args)):
            if i:
                time.sleep(args.pause)
            profile = json.load(open(path, encoding="utf-8"))
            found = []
            try:
                for pur in src.iter_new(profile, max_pages=args.max_pages):
                    if pur.id in seen:
                        continue
                    seen.add(pur.id)
                    found.append(pur)
                    if len(found) >= args.limit:
                        break
            except Exception as e:                 # 429/сеть — не роняем весь поиск
                print("## %s — ⚠ поиск прерван: %s" % (name, type(e).__name__))
                continue
            print("\n## %s — найдено %d" % (name, len(found)))
            for pur in found:
                total += 1
                code = ", ".join(pur.ktru or pur.okpd2) or "—"
                print(TP_URL % pur.id)
                print("   %s | %s | %s" % ((pur.subject or "")[:70], code,
                                           (pur.customer or "")[:30]))
    finally:
        src.close()
    print("\n▶ ВСЕГО найдено: %d тендеров по %d профилям" % (total, len(_profiles(args))))


if __name__ == "__main__":
    main()
