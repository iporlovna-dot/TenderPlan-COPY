"""CLI майнера синонимов имён полей (src/synonym_miner.py, plan §Этап2 п.3).

Собирает имена полей из карточек товара + замороженных ТЗ (golden), эмбеддингами предлагает
кандидатов в `key_synonyms` профиля. Только ПРЕДЛАГАЕТ — ты подтверждаешь и вставляешь в профиль
(антонимы вроде миллер↔макинтош отсеиваешь глазами). Нужна bge-m3 (sentence-transformers), без API.

Запуск:
    .venv/bin/python scripts/mine_synonyms.py data/profiles/laryngoscope.json data/products/ \
        --golden tests/golden
"""
import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from synonym_miner import mine_candidates, suggest_groups  # noqa: E402


def collect_fields(products_dir, golden_dir, category_hint=None):
    """{имя_поля: образец_значения} из карточек товара и требований golden-ТЗ."""
    fields = {}
    for path in glob.glob(os.path.join(products_dir, "*.json")):
        d = json.load(open(path, encoding="utf-8"))
        for a in d.get("attributes", []):
            fields.setdefault(a["key"], a.get("value"))
    if golden_dir:
        for path in glob.glob(os.path.join(golden_dir, "*.json")):
            fx = json.load(open(path, encoding="utf-8"))
            for r in fx.get("requirements", []):
                fields.setdefault(r["key"], r.get("value"))
            for a in fx.get("product", {}).get("attributes", []):
                fields.setdefault(a["key"], a.get("value"))
    return fields


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("profile")
    ap.add_argument("products", help="каталог карточек товара")
    ap.add_argument("--golden", help="каталог golden-фикстур (доп. имена из ТЗ)")
    ap.add_argument("--threshold", type=float, default=0.85)
    args = ap.parse_args()

    profile = json.load(open(args.profile, encoding="utf-8"))
    existing = profile.get("key_synonyms", [])
    fields = collect_fields(args.products, args.golden, profile.get("category"))
    print("Собрано уникальных имён полей: %d" % len(fields))

    cands = mine_candidates(fields, existing, threshold=args.threshold)
    if not cands:
        print("Кандидатов нет (либо нет модели: pip install sentence-transformers, либо всё уже в словаре).")
        return

    print("\n=== КАНДИДАТЫ (подтверди/отсей вручную; антонимы эмбеддинг путает) ===")
    for s, a, b in cands:
        print("  %.3f  %-32s ↔ %s" % (s, a, b))

    groups = suggest_groups(cands)
    print("\n=== ГОТОВЫЕ ГРУППЫ для key_synonyms (вставить ПОСЛЕ проверки) ===")
    print(json.dumps(groups, ensure_ascii=False, indent=2))
    print("\n⚠ Не вставляй вслепую: убери пары РАЗНЫХ характеристик (миллер↔макинтош, длина↔ширина).")


if __name__ == "__main__":
    main()
