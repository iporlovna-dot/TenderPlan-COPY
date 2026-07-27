"""Лид-лист: ранжированная лента подходящих закупок под товар (продуктовый вывод, Этап 1).

Читает вердикты, которые пишет `run_auto.py` (--out/*.json), сводит по закупке ЛУЧШИЙ товар,
отсекает дисквалифицированные и слабые (< --min), сортирует по % убыв. и печатает ленту с
практичной обвязкой (заказчик, НМЦК, дней до окончания подачи, ссылка). Это то, что видит
поставщик: «вот закупки под твой товар, по убыванию соответствия».

Детерминированно, без LLM/сети. Запуск:
    .venv/bin/python scripts/leads.py --results data/results --min 60
"""
import argparse
import glob
import json
import os
import sys
import time

VERDICT_RU = {"eligible": "ПРОХОДИТ", "eligible_with_gaps": "ПРОХОДИТ·пробелы",
              "disqualified": "НЕ ПРОХОДИТ"}


def _days_left(submission_close):
    if not submission_close:
        return None
    return (submission_close / 1000.0 - time.time()) / 86400.0


def main():
    ap = argparse.ArgumentParser(description="Ранжированный лид-лист по вердиктам run_auto")
    ap.add_argument("--results", default="data/results", help="папка вердиктов run_auto")
    ap.add_argument("--min", type=float, default=60, help="порог %% для попадания в ленту")
    ap.add_argument("--product", help="фильтр по product_id (если несколько товаров)")
    args = ap.parse_args()

    best = {}  # purchase_id -> лучший вердикт
    for fp in glob.glob(os.path.join(args.results, "*.json")):
        v = json.load(open(fp, encoding="utf-8"))
        if args.product and v.get("product_id") != args.product:
            continue
        pid = v["purchase_id"]
        if pid not in best or v["score"] > best[pid]["score"]:
            best[pid] = v

    leads = [v for v in best.values()
             if v["verdict"] != "disqualified" and v["score"] >= args.min]
    leads.sort(key=lambda v: v["score"], reverse=True)

    if not leads:
        print("Лента пуста (нет вердиктов ≥ %g%% в %s/). Сначала: run_auto.py …" % (args.min, args.results))
        return

    print("ЛЕНТА ПОДХОДЯЩИХ ЗАКУПОК — %d (порог ≥%g%%)\n" % (len(leads), args.min))
    for v in leads:
        dl = _days_left(v.get("submission_close"))
        dl_s = ("%.0f дн до подачи" % dl) if dl is not None and dl >= 0 else \
               ("подача истекла" if dl is not None else "срок —")
        price = ("%s ₽" % format(int(v["price"]), ",d").replace(",", " ")) if v.get("price") else "НМЦК —"
        print("%3d%%  %-16s  %s" % (v["score"], VERDICT_RU.get(v["verdict"], v["verdict"]),
                                    (v.get("subject") or "")[:66]))
        print("      %s · %s · %s" % ((v.get("customer") or "заказчик —")[:34], price, dl_s))
        print("      %s  [товар: %s]" % (v.get("url", ""), v.get("product_id", "")))
        print()


if __name__ == "__main__":
    main()
