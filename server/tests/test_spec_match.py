"""Тесты моста «позиции закупки ↔ карточки товара»: python server/tests/test_spec_match.py

Мост опасен тем, что ошибается ТИХО. Неверно переведённый оператор даст
правдоподобный процент, посчитанный не по тем требованиям; выбранный не тот
товар — вердикт по чужой карточке; потерянная жёсткость — «проходим» там, где
дисквалификация. Ни одно из этого не падает.

Движок и FastAPI не мокаем: ядро зависимостей не требует, а веб-слой здесь и не
участвует. Файл tz.json подменяем временным.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def _fresh_module(spec_path):
    """Модуль читает tz.json по пути из окружения и кэширует по mtime — для
    каждого теста поднимаем его заново на своём файле.

    ⚠️ Одного sys.modules.pop мало: `from app import spec_match` сначала смотрит
    АТРИБУТ пакета `app`, а он после первого импорта остаётся и указывает на
    старый модуль со старым SPEC_FILE. Поэтому перезагружаем явно.
    """
    import importlib
    os.environ["LK_SPEC_FILE"] = spec_path
    import app
    if hasattr(app, "spec_match"):
        return importlib.reload(app.spec_match)
    return importlib.import_module("app.spec_match")


def _write_spec(items, purchase_id="eis_1"):
    """Позиции живут в data/spec.json — ОТДЕЛЬНОМ довеске, а не в tz.json: там
    4.93 МБ терминов «Умной сверки», а позиции весят 0.23 МБ и нужны каждой
    раскрытой карточке. Закупка без спецификации в файле просто отсутствует."""
    fd, path = tempfile.mkstemp(suffix=".json")
    spec = {purchase_id: items} if items else {}
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"v": 2, "spec": spec}, fh)
    return path


GLOVES = {
    "id": "prod_gloves", "name": "Перчатки смотровые нитриловые",
    "ktru": ["32.50.13.190-00007686"],
    "attributes": [
        {"key": "Материал", "value": "нитрил"},
        {"key": "Длина", "value": 250},
        {"key": "Толщина", "value": 0.12},
    ],
}
BLADE = {
    "id": "prod_blade", "name": "Клинок ларингоскопа Миллер",
    "ktru": ["32.50.50.190-00001458"],
    "attributes": [{"key": "Материал", "value": "сталь"}],
}


def item(**over):
    base = {
        "name": "Перчатки нитриловые", "ktru": "32.50.13.190-00007686",
        "okpd": "32.50.13.190", "qty": 100, "unit": "пара",
        "chars": [
            {"key": "Материал", "operator": "eq", "value": "нитрил", "unit": "",
             "hardness": "hard", "raw": "нитрил"},
            {"key": "Длина", "operator": "gte", "value": 240, "unit": "мм",
             "hardness": "soft", "raw": "не менее 240"},
        ],
    }
    base.update(over)
    return base


class TestRequirements(unittest.TestCase):
    def setUp(self):
        self.path = _write_spec([item()])
        self.sm = _fresh_module(self.path)

    def tearDown(self):
        os.unlink(self.path)

    def test_операторы_переводятся_в_словарь_движка(self):
        reqs = self.sm.to_requirements(item(chars=[
            {"key": "a", "operator": "gte", "value": 4},
            {"key": "b", "operator": "lte", "value": 9},
            {"key": "c", "operator": "range", "value": [1, 5]},
            {"key": "d", "operator": "present", "value": True},
            {"key": "e", "operator": "eq", "value": "нитрил"},
        ]))
        self.assertEqual([r.operator.value for r in reqs],
                         ["gte", "lte", "range", "present", "eq"])

    def test_неизвестный_оператор_пропускается_а_не_подменяется(self):
        reqs = self.sm.to_requirements(item(chars=[
            {"key": "a", "operator": "какой-то-новый", "value": 4},
            {"key": "b", "operator": "gte", "value": 4},
        ]))
        self.assertEqual([r.key for r in reqs], ["b"],
                         "выдуманное требование даст ложное нарушение — хуже отсутствующего")

    def test_жёсткость_не_теряется(self):
        reqs = self.sm.to_requirements(item())
        self.assertEqual(reqs[0].hardness.value, "hard")
        self.assertEqual(reqs[1].hardness.value, "soft")

    def test_жёсткость_по_умолчанию_мягкая(self):
        reqs = self.sm.to_requirements(item(chars=[{"key": "a", "operator": "gte", "value": 4}]))
        self.assertEqual(reqs[0].hardness.value, "soft", "не дисквалифицируем без причины")


class TestPickProduct(unittest.TestCase):
    def setUp(self):
        self.path = _write_spec([item()])
        self.sm = _fresh_module(self.path)

    def tearDown(self):
        os.unlink(self.path)

    def test_точный_код_ктру_решает_сразу(self):
        chosen, reason = self.sm.pick_product(item(), [BLADE, GLOVES])
        self.assertEqual(chosen["id"], "prod_gloves")
        self.assertEqual(reason, "exact")

    def test_код_важнее_похожего_названия(self):
        # у клинка название ближе к позиции, но код чужой
        pos = item(name="Клинок ларингоскопа Миллер прямой")
        chosen, reason = self.sm.pick_product(pos, [BLADE, GLOVES])
        self.assertEqual(chosen["id"], "prod_gloves",
                         "код КТРУ федеральный и однозначный, название заказчик пишет как хочет")
        self.assertEqual(reason, "exact")

    def test_без_кодов_падаем_на_название(self):
        pos = item(name="Клинок ларингоскопа Миллер", ktru="", okpd="")
        chosen, reason = self.sm.pick_product(pos, [BLADE, GLOVES])
        self.assertEqual(chosen["id"], "prod_blade")
        self.assertEqual(reason, "name")

    def test_пустой_каталог_не_роняет(self):
        chosen, reason = self.sm.pick_product(item(), [])
        self.assertIsNone(chosen)
        self.assertEqual(reason, "none")


class TestMatchLot(unittest.TestCase):
    def tearDown(self):
        if getattr(self, "path", None):
            os.unlink(self.path)

    def test_лот_закрыт_когда_закрыты_все_позиции(self):
        self.path = _write_spec([item()])
        sm = _fresh_module(self.path)
        res = sm.match_lot("eis_1", [GLOVES])
        self.assertEqual(res["status"], "ok")
        self.assertEqual((res["covered"], res["total"]), (1, 1))
        self.assertEqual(res["verdict"], "eligible")

    def test_лот_берут_целиком_частичное_покрытие_это_отказ(self):
        self.path = _write_spec([item(), item(name="Бинт", ktru="21.20.23.110-00004254", okpd="21.20.23.110")])
        sm = _fresh_module(self.path)
        res = sm.match_lot("eis_1", [GLOVES])
        self.assertEqual(res["covered"], 1)
        self.assertEqual(res["total"], 2)
        self.assertEqual(res["verdict"], "disqualified",
                         "закрыть 1 позицию из 2 — отказ, а не 50%")

    def test_нарушение_жёсткого_требования_дисквалифицирует(self):
        bad = item(chars=[{"key": "Материал", "operator": "eq", "value": "латекс",
                           "hardness": "hard", "raw": "латекс"}])
        self.path = _write_spec([bad])
        sm = _fresh_module(self.path)
        res = sm.match_lot("eis_1", [GLOVES])
        self.assertEqual(res["positions"][0]["verdict"], "disqualified")
        self.assertEqual(res["covered"], 0)

    def test_статус_проверки_переводится_в_словарь_фронта(self):
        bad = item(chars=[{"key": "Материал", "operator": "eq", "value": "латекс",
                           "hardness": "hard", "raw": "латекс"}])
        self.path = _write_spec([bad])
        sm = _fresh_module(self.path)
        checks = sm.match_lot("eis_1", [GLOVES])["positions"][0]["checks"]
        self.assertEqual(checks[0]["status"], "fail",
                         "у фронта fail, у движка violation — переводим одним местом")
        self.assertTrue(checks[0]["hard"])

    def test_нет_подходящего_товара_говорим_прямо(self):
        self.path = _write_spec([item()])
        sm = _fresh_module(self.path)
        res = sm.match_lot("eis_1", [])
        pos = res["positions"][0]
        self.assertIsNone(pos["product_id"])
        self.assertEqual(pos["match_by"], "none")
        self.assertEqual(res["covered"], 0)

    def test_закупка_без_спецификации_отвечает_статусом(self):
        self.path = _write_spec([])
        sm = _fresh_module(self.path)
        res = sm.match_lot("eis_1", [GLOVES])
        self.assertEqual(res["status"], "no-spec",
                         "«спецификации нет» и «не совпало» — разные ответы")
        self.assertEqual(res["total"], 0)

    def test_неизвестная_закупка_не_падает(self):
        self.path = _write_spec([item()])
        sm = _fresh_module(self.path)
        self.assertEqual(sm.match_lot("eis_нет-такой", [GLOVES])["status"], "no-spec")

    def test_позиция_несёт_количество_и_единицу(self):
        self.path = _write_spec([item()])
        sm = _fresh_module(self.path)
        pos = sm.match_lot("eis_1", [GLOVES])["positions"][0]
        self.assertEqual((pos["qty"], pos["unit"]), (100, "пара"))

    def test_позиция_без_характеристик_не_проходит_молча_и_не_топит_лот(self):
        # ⚠️ Раньше пустой chars → matcher.match(product, [], ...) → `all([])`
        # вакуумно истинно → тихий ELIGIBLE, хотя ничего не проверялось (замер
        # 2026-08-28: goods-фолбэк ktrutable.js — 87% позиций 44-ФЗ, 98% 223-ФЗ).
        # Название короткое («Бахилы», < LK_SPEC_LLM_NAME_MIN) — LLM-фолбэк
        # (spec_llm.py) сам себя не пробует, тест остаётся offline-детерминированным.
        bahily = {"id": "prod_bahily", "name": "Бахилы", "ktru": [], "attributes": []}
        self.path = _write_spec([item(), item(name="Бахилы", ktru="", okpd="", chars=[])])
        sm = _fresh_module(self.path)
        res = sm.match_lot("eis_1", [GLOVES, bahily])
        unverified = res["positions"][1]
        self.assertEqual(unverified["verdict"], "unverified")
        self.assertEqual(unverified["product_id"], "prod_bahily",
                         "товар для позиции нашёлся — не хватает именно характеристик, а не товара")
        self.assertTrue(unverified["note"], "должно быть понятно ПОЧЕМУ нет вердикта")
        self.assertEqual(res["covered"], 1, "unverified не считается закрытой позицией")
        self.assertEqual(res["verdict"], "eligible_with_gaps",
                         "нет данных для проверки — пробел, а не отказ и не тихий проход")

    def test_нет_товара_приоритетнее_llm_фолбэка(self):
        # Товара под позицию нет вообще — LLM звать незачем (см. spec_match.py:
        # pick_product решается ДО обращения к spec_llm, а не после).
        self.path = _write_spec([item(name="Нечто без товара в каталоге", ktru="", okpd="", chars=[])])
        sm = _fresh_module(self.path)
        res = sm.match_lot("eis_1", [])
        pos = res["positions"][0]
        self.assertEqual(pos["verdict"], "disqualified")
        self.assertEqual(pos["note"], "в каталоге нет подходящего товара")

    def test_нарушение_остаётся_отказом_даже_рядом_с_unverified(self):
        bad = item(chars=[{"key": "Материал", "operator": "eq", "value": "латекс",
                           "hardness": "hard", "raw": "латекс"}])
        empty = item(name="Бахилы", ktru="", okpd="", chars=[])
        self.path = _write_spec([bad, empty])
        sm = _fresh_module(self.path)
        res = sm.match_lot("eis_1", [GLOVES])
        self.assertEqual(res["verdict"], "disqualified",
                         "настоящее нарушение важнее соседнего unverified — лот всё-или-ничего")


class TestSpecFile(unittest.TestCase):
    def test_отсутствующий_файл_не_роняет_сверку(self):
        sm = _fresh_module(os.path.join(tempfile.gettempdir(), "нет-такого-файла.json"))
        self.assertEqual(sm.match_lot("eis_1", [GLOVES])["status"], "no-spec")

    def test_битый_файл_не_роняет_сверку(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("{это не json")
        try:
            sm = _fresh_module(path)
            self.assertEqual(sm.match_lot("eis_1", [GLOVES])["status"], "no-spec")
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
