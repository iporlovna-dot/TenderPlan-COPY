"""Регресс на правило покрытия позиции: совпадение ТОЛЬКО по поставочным полям (РУ/срок/
новизна) не считается «наш товар». Ловушка на баг ранжирования малых позиций (plan §3):
позиция из одних бумажных полей ложно «покрывалась» любым оформленным товаром.

LLM-слой (align_keys/align_values) замокан на identity — тестируем детерминированную
логику покрытия, а не семантику имён (её проверяет test_keymatch)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from schema import Attribute, Product, ReqType  # noqa: E402
import lot_coverage as lc  # noqa: E402

# identity-мок семантического слоя: сверяем по прямым именам ключей, без API
lc.align_keys = lambda req_fields, product_fields, *a, **k: {}
lc.align_values = lambda reqs, product, *a, **k: reqs


def _card(pid, **attrs):
    d = {"id": pid, "name": pid, "ktru": [],
         "attributes": [{"key": k, "value": v, "status": "declared"} for k, v in attrs.items()]}
    return (d, Product(id=pid, name=pid,
                       attributes=[Attribute(key=k, value=v) for k, v in attrs.items()]))


def test_req_marks_supply_fields_documentary():
    assert lc._req("рег_удостоверение", "present", None)["type"] == ReqType.DOCUMENTARY.value
    assert lc._req("срок_годности_остаточный_мес", "gte", 6)["type"] == ReqType.DOCUMENTARY.value
    assert lc._req("напряжение_в", "eq", 3.5)["type"] == ReqType.TECHNICAL.value


def test_supply_only_match_is_not_coverage():
    # товар совпадает по РУ и сроку годности, но категорийное поле (напряжение) расходится/отсутствует
    catalog = [_card("charger", рег_удостоверение="РУ имеется", срок_годности_остаточный_мес=24,
                     входное_напряжение="110-240 В")]
    reqs = [lc._req("рег_удостоверение", "present", None),
            lc._req("срок_годности_остаточный_мес", "gte", 6),
            lc._req("рабочее_напряжение_в", "eq", 3.5)]  # нет в карточке → gap, не совпало
    pid, score, verdict, tech = lc.best_card(reqs, None, catalog, None, [])
    assert tech == 0, "категорийных совпадений быть не должно — только поставочные"


def test_category_match_counts_as_coverage():
    # реальное категорийное совпадение (напряжение) → техническое покрытие есть
    catalog = [_card("handle", рег_удостоверение="РУ имеется", напряжение_в=3.5)]
    reqs = [lc._req("рег_удостоверение", "present", None),
            lc._req("напряжение_в", "eq", 3.5)]
    pid, score, verdict, tech = lc.best_card(reqs, None, catalog, None, [])
    assert tech >= 1, "категорийное совпадение по напряжению должно считаться покрытием"


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in fns:
        try:
            fn()
            print("✅ %s" % fn.__name__)
            passed += 1
        except Exception:
            print("❌ %s" % fn.__name__)
            traceback.print_exc()
    print("\n%d/%d тестов прошли" % (passed, len(fns)))
    sys.exit(0 if passed == len(fns) else 1)
