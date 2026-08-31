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
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "matcher", "tests")))
from _llm_mock import ChecksLLM, MappingLLM  # noqa: E402


def _fresh_module(spec_path, db_path=None):
    """Модуль читает tz.json по пути из окружения и кэширует по mtime — для
    каждого теста поднимаем его заново на своём файле.

    ⚠️ Одного sys.modules.pop мало: `from app import spec_match` сначала смотрит
    АТРИБУТ пакета `app`, а он после первого импорта остаётся и указывает на
    старый модуль со старым SPEC_FILE. Поэтому перезагружаем явно.

    db_path — для тестов кэша align_keys_cache (LK_ALIGN_KEYS_LLM): `db.DB_PATH`
    читается из окружения ТОЛЬКО при импорте модуля, тем же приёмом, что
    test_spec_llm._fresh_spec_llm, поэтому `app.db` тоже перезагружаем явно.
    """
    import importlib
    os.environ["LK_SPEC_FILE"] = spec_path
    if db_path is not None:
        os.environ["LK_DB_PATH"] = db_path
    import app
    if hasattr(app, "db"):
        importlib.reload(app.db)
    else:
        importlib.import_module("app.db")
    if db_path is not None:
        app.db.init_db()
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


class TestAlignKeys(unittest.TestCase):
    """align_keys — детерминированный слой (llm_fallback=False, без сети и денег), сводит
    расхождение в написании ключа между ТЗ и карточкой (matcher/src/keymatch.py)."""

    def setUp(self):
        self._prev_env = os.environ.get("LK_ALIGN_KEYS")

    def tearDown(self):
        if self._prev_env is None:
            os.environ.pop("LK_ALIGN_KEYS", None)
        else:
            os.environ["LK_ALIGN_KEYS"] = self._prev_env
        if getattr(self, "path", None):
            os.unlink(self.path)

    @staticmethod
    def _pos():
        # ключ ТЗ «материал» (строчными) — то же требование, что «Материал» у карточки,
        # но буквальный лукап их не отождествит без align_keys.
        return item(chars=[{"key": "материал", "operator": "eq", "value": "нитрил",
                            "unit": "", "hardness": "hard", "raw": "нитрил"}])

    def test_сводит_расхождение_в_регистре_ключа(self):
        os.environ.pop("LK_ALIGN_KEYS", None)  # по умолчанию включено
        self.path = _write_spec([self._pos()])
        sm = _fresh_module(self.path)
        res = sm.match_lot("eis_1", [GLOVES])
        checks = res["positions"][0]["checks"]
        self.assertEqual(checks[0]["key"], "Материал",
                         "ключ требования сведён к ключу карточки — тот же материал, другое написание")
        self.assertEqual(checks[0]["status"], "pass")
        self.assertEqual(res["verdict"], "eligible")

    def test_LK_ALIGN_KEYS_0_отключает_сведение(self):
        os.environ["LK_ALIGN_KEYS"] = "0"
        self.path = _write_spec([self._pos()])
        sm = _fresh_module(self.path)
        res = sm.match_lot("eis_1", [GLOVES])
        checks = res["positions"][0]["checks"]
        self.assertEqual(checks[0]["key"], "материал", "align выключен — ключ остаётся как в ТЗ")
        self.assertEqual(checks[0]["status"], "gap",
                         "буквальный лукап не находит «Материал» под ключом «материал»")

    def test_несвязанные_ключи_не_сводятся_детерминированным_слоем(self):
        # «цвет» не пересекается по токенам ни с одним ключом карточки — детерминированный
        # слой (llm_fallback=False) обязан оставить это пробелом, а не выдумывать пару.
        os.environ.pop("LK_ALIGN_KEYS", None)
        pos = item(chars=[{"key": "цвет", "operator": "eq", "value": "белый",
                           "unit": "", "hardness": "soft", "raw": "белый"}])
        self.path = _write_spec([pos])
        sm = _fresh_module(self.path)
        res = sm.match_lot("eis_1", [GLOVES])
        checks = res["positions"][0]["checks"]
        self.assertEqual(checks[0]["key"], "цвет")
        self.assertEqual(checks[0]["status"], "gap")


class TestAlignKeysLlm(unittest.TestCase):
    """LK_ALIGN_KEYS_LLM — LLM-добор остатка align_keys с кэшем в align_keys_cache
    (Фаза 3 плана piped-forging-flame, matcher/src/keymatch.llm_map). Мок anthropic-
    клиента (MappingLLM, matcher/tests/_llm_mock.py) — сети не бьём. Тестируем
    `_align_keys` напрямую (как test_spec_llm тестирует extract_cached напрямую),
    не через match_lot — client туда не прокидывается сознательно, это приватный
    тестовый шов, а не публичный параметр моста."""

    CARD = [{"key": "Цвет", "value": "белый"}]

    def setUp(self):
        self._prev_llm = os.environ.get("LK_ALIGN_KEYS_LLM")
        os.environ["LK_ALIGN_KEYS_LLM"] = "1"
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(self.db_path)  # init_db создаст заново
        self.path = _write_spec([item()])
        self.sm = _fresh_module(self.path, db_path=self.db_path)

    def tearDown(self):
        if self._prev_llm is None:
            os.environ.pop("LK_ALIGN_KEYS_LLM", None)
        else:
            os.environ["LK_ALIGN_KEYS_LLM"] = self._prev_llm
        os.unlink(self.path)
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def _reqs(self):
        # «оттенок» не пересекается по токенам ни с одним ключом карточки —
        # детерминированный слой обязан оставить остаток, чтобы был повод звать LLM.
        return self.sm.to_requirements(item(chars=[
            {"key": "оттенок", "operator": "eq", "value": "белый",
             "unit": "", "hardness": "soft", "raw": "белый"},
        ]))

    def test_llm_сводит_остаток_после_детерминированного_слоя(self):
        llm = MappingLLM([{"tz_key": "оттенок", "card_key": "Цвет"}])
        out = self.sm._align_keys(self._reqs(), self.CARD, client=llm)
        self.assertEqual(out[0].key, "Цвет")
        self.assertTrue(out[0].remapped)
        self.assertEqual(llm.calls, 1)

    def test_повтор_идёт_из_кэша_не_в_сеть(self):
        llm = MappingLLM([{"tz_key": "оттенок", "card_key": "Цвет"}])
        self.sm._align_keys(self._reqs(), self.CARD, client=llm)
        self.sm._align_keys(self._reqs(), self.CARD, client=llm)
        self.assertEqual(llm.calls, 1, "второй вызов должен прийти из кэша, не из сети")

    def test_пустой_результат_llm_тоже_кэшируется(self):
        llm = MappingLLM([])  # LLM честно ничего не нашла
        first = self.sm._align_keys(self._reqs(), self.CARD, client=llm)
        second = self.sm._align_keys(self._reqs(), self.CARD, client=llm)
        self.assertEqual(first[0].key, "оттенок")
        self.assertEqual(second[0].key, "оттенок")
        self.assertEqual(llm.calls, 1, "пустой ответ — тоже успех, кэшируется, не повторяем зря")

    def test_сбой_llm_не_падает_и_не_кэшируется(self):
        class BoomLLM(MappingLLM):
            def respond(self, request):
                raise RuntimeError("сеть легла")
        out = self.sm._align_keys(self._reqs(), self.CARD, client=BoomLLM([]))
        self.assertEqual(out[0].key, "оттенок", "сбой не должен ронять сверку")

        llm2 = MappingLLM([{"tz_key": "оттенок", "card_key": "Цвет"}])
        out2 = self.sm._align_keys(self._reqs(), self.CARD, client=llm2)
        self.assertEqual(out2[0].key, "Цвет",
                         "сбой не кэшируется — повтор должен попробовать снова")

    def test_флаг_выключен_llm_не_зовётся(self):
        os.environ["LK_ALIGN_KEYS_LLM"] = "0"
        sm2 = _fresh_module(self.path, db_path=self.db_path)
        llm = MappingLLM([{"tz_key": "оттенок", "card_key": "Цвет"}])
        out = sm2._align_keys(sm2.to_requirements(item(chars=[
            {"key": "оттенок", "operator": "eq", "value": "белый",
             "unit": "", "hardness": "soft", "raw": "белый"},
        ])), self.CARD, client=llm)
        self.assertEqual(out[0].key, "оттенок")
        self.assertEqual(llm.calls, 0, "флаг выключен — деньги не тратим")


class TestAlignValues(unittest.TestCase):
    """LK_ALIGN_VALUES — семантическая сверка значений с кэшем в align_values_cache
    (Фаза 2 плана piped-forging-flame, matcher/src/keymatch.align_values). Мок
    anthropic-клиента (ChecksLLM, matcher/tests/_llm_mock.py) — сети не бьём. Тестируем
    `_align_values_cached` напрямую, тем же приёмом, что TestAlignKeysLlm."""

    def setUp(self):
        self._prev_flag = os.environ.get("LK_ALIGN_VALUES")
        os.environ["LK_ALIGN_VALUES"] = "1"
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(self.db_path)  # init_db создаст заново
        self.path = _write_spec([item()])
        self.sm = _fresh_module(self.path, db_path=self.db_path)
        self.card = self.sm.to_product({
            "id": "prod", "name": "x",
            "attributes": [{"key": "Материал", "value": "нержавеющая сталь"}],
        })

    def tearDown(self):
        if self._prev_flag is None:
            os.environ.pop("LK_ALIGN_VALUES", None)
        else:
            os.environ["LK_ALIGN_VALUES"] = self._prev_flag
        os.unlink(self.path)
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def _reqs(self):
        # ключ уже совпадает буквально («Материал»=«Материал») — расходится только
        # ЗНАЧЕНИЕ («металл» вместо «нержавеющая сталь»), это и оценивает align_values.
        return self.sm.to_requirements(item(chars=[
            {"key": "Материал", "operator": "eq", "value": "металл",
             "unit": "", "hardness": "hard", "raw": "металл"},
        ]))

    def test_llm_подтверждает_семантическое_совпадение(self):
        llm = ChecksLLM(["Материал"])
        out = self.sm._align_values_cached(self._reqs(), self.card, client=llm)
        self.assertEqual(out[0].value, "нержавеющая сталь",
                         "значение требования нормализовано к карточному — matcher засчитает pass")
        self.assertEqual(llm.calls, 1)

    def test_llm_отказывает_значение_остаётся_как_в_тз(self):
        llm = ChecksLLM([])  # ни один ключ не подтверждён
        out = self.sm._align_values_cached(self._reqs(), self.card, client=llm)
        self.assertEqual(out[0].value, "металл", "не подтверждено — значение ТЗ не трогаем")

    def test_повтор_идёт_из_кэша_не_в_сеть(self):
        llm = ChecksLLM(["Материал"])
        self.sm._align_values_cached(self._reqs(), self.card, client=llm)
        self.sm._align_values_cached(self._reqs(), self.card, client=llm)
        self.assertEqual(llm.calls, 1, "второй вызов должен прийти из кэша, не из сети")

    def test_отказ_тоже_кэшируется(self):
        llm = ChecksLLM([])
        self.sm._align_values_cached(self._reqs(), self.card, client=llm)
        self.sm._align_values_cached(self._reqs(), self.card, client=llm)
        self.assertEqual(llm.calls, 1, "честное «не совпадает» — тоже успех, кэшируется")

    def test_сбой_llm_не_падает_и_не_кэшируется(self):
        class BoomLLM(ChecksLLM):
            def respond(self, request):
                raise RuntimeError("сеть легла")
        out = self.sm._align_values_cached(self._reqs(), self.card, client=BoomLLM([]))
        self.assertEqual(out[0].value, "металл", "сбой не должен ронять сверку")

        llm2 = ChecksLLM(["Материал"])
        out2 = self.sm._align_values_cached(self._reqs(), self.card, client=llm2)
        self.assertEqual(out2[0].value, "нержавеющая сталь",
                         "сбой не кэшируется — повтор должен попробовать снова")

    def test_уже_совпадающие_строково_не_идут_в_llm(self):
        reqs = self.sm.to_requirements(item(chars=[
            {"key": "Материал", "operator": "eq", "value": "нержавеющая сталь",
             "unit": "", "hardness": "hard", "raw": "нержавеющая сталь"},
        ]))
        llm = ChecksLLM(["Материал"])
        out = self.sm._align_values_cached(reqs, self.card, client=llm)
        self.assertEqual(out[0].value, "нержавеющая сталь")
        self.assertEqual(llm.calls, 0, "уже совпадает строково — звать LLM незачем")

    def test_флаг_выключен_match_lot_не_меняет_значение(self):
        os.environ["LK_ALIGN_VALUES"] = "0"
        pos = item(chars=[{"key": "Материал", "operator": "eq", "value": "металл",
                           "unit": "", "hardness": "hard", "raw": "металл"}])
        path = _write_spec([pos])
        try:
            sm2 = _fresh_module(path, db_path=self.db_path)
            card = {"id": "prod", "name": "x", "ktru": ["32.50.13.190-00007686"],
                    "attributes": [{"key": "Материал", "value": "нержавеющая сталь"}]}
            res = sm2.match_lot("eis_1", [card])
            checks = res["positions"][0]["checks"]
            self.assertEqual(checks[0]["status"], "fail",
                             "флаг выключен — буквальное расхождение значения остаётся нарушением")
        finally:
            os.unlink(path)


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
