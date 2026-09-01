"""Тесты LLM-фолбэка извлечения требований: python server/tests/test_spec_llm.py

Мок anthropic-клиента (matcher/tests/_llm_mock.py ReqsLLM) — сети не бьём,
ключ в тестовом окружении фиктивный. Проверяем то, что может ошибиться ТИХО:
порог длины (не тратить вызов зря), кэш (не тратить дважды), и что сетевой
сбой не запирает позицию в кэше навсегда.
"""

from __future__ import annotations

import importlib
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "matcher", "tests")))

from _llm_mock import ReqsLLM  # noqa: E402


def _fresh_spec_llm(db_path):
    """spec_llm читает ENABLED из окружения В МОМЕНТ ИМПОРТА (см. spec_llm.py) —
    для каждого теста поднимаем модуль заново на своём окружении/БД, тем же
    приёмом, что test_spec_match._fresh_module."""
    os.environ["LK_DB_PATH"] = db_path
    # Тесты гоняют против мока anthropic-формы (ReqsLLM) — провайдер пиннуем явно,
    # независимо от LK_LLM_PROVIDER в среде запуска (прод по умолчанию — deepseek,
    # см. matcher/src/llmclient.py); client= инъекция всё равно не даёт get_default_client()
    # реально вызваться, но ENABLED/ENABLED_FULLTEXT читают has_api_key() при импорте.
    os.environ["LK_LLM_PROVIDER"] = "anthropic"
    os.environ["ANTHROPIC_API_KEY"] = "sk-test-fake-key-for-mock-only"
    import app
    if hasattr(app, "db"):
        importlib.reload(app.db)
    if hasattr(app, "spec_llm"):
        mod = importlib.reload(app.spec_llm)
    else:
        mod = importlib.import_module("app.spec_llm")
    app.db.init_db()
    return mod


class TestSpecLlm(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(self.db_path)  # init_db создаст заново
        self.sm = _fresh_spec_llm(self.db_path)

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_короткое_название_llm_не_зовёт(self):
        llm = ReqsLLM()
        reqs = self.sm.extract_cached("Бахилы", client=llm)
        self.assertEqual(reqs, [])
        self.assertEqual(llm.calls, 0, "меньше порога — вызов не стоит денег")

    def test_длинное_название_зовёт_llm_один_раз(self):
        llm = ReqsLLM([{"key": "материал", "operator": "eq", "value": "фанера",
                        "unit": "", "hardness": "soft", "type": "technical", "raw": "материал: фанера"}])
        name = "Материал: фанера высшего сорта, гипоаллергенное покрытие, размеры 75х40 см, для детей"
        reqs = self.sm.extract_cached(name, client=llm)
        self.assertEqual(len(reqs), 1)
        self.assertEqual(reqs[0]["key"], "материал")
        self.assertEqual(llm.calls, 1)

    def test_повтор_идёт_из_кэша_не_в_сеть(self):
        llm = ReqsLLM([{"key": "a", "operator": "present", "value": True,
                        "unit": "", "hardness": "soft", "type": "technical", "raw": "a"}])
        name = "Очень длинное название товара с характеристиками для проверки кэша дважды подряд"
        first = self.sm.extract_cached(name, client=llm)
        second = self.sm.extract_cached(name, client=llm)
        self.assertEqual(first, second)
        self.assertEqual(llm.calls, 1, "второй вызов должен прийти из кэша, не из сети")

    def test_пустой_результат_llm_тоже_кэшируется(self):
        llm = ReqsLLM([])  # LLM честно ничего не нашла
        name = "Длинное название без единой различимой характеристики просто текст ни о чём"
        first = self.sm.extract_cached(name, client=llm)
        second = self.sm.extract_cached(name, client=llm)
        self.assertEqual(first, [])
        self.assertEqual(second, [])
        self.assertEqual(llm.calls, 1, "пустой ответ — тоже успех, кэшируется, не повторяем зря")

    def test_сбой_llm_не_падает_и_не_кэшируется(self):
        class BoomLLM(ReqsLLM):
            def respond(self, request):
                raise RuntimeError("сеть легла")
        llm = BoomLLM()
        name = "Длинное название, на котором модель почему-то ловит сетевую ошибку прямо сейчас"
        first = self.sm.extract_cached(name, client=llm)
        self.assertEqual(first, [], "сбой не должен ронять вызывающего")
        # ⚠️ Если бы сбой закэшировался, второй вызов тоже отдал бы [] без
        # попытки — а он обязан попробовать снова (см. db.py: коммент про pending).
        llm2 = ReqsLLM([{"key": "ok", "operator": "present", "value": True,
                         "unit": "", "hardness": "soft", "type": "technical", "raw": "ok"}])
        second = self.sm.extract_cached(name, client=llm2)
        self.assertEqual(len(second), 1, "сбой не кэшируется — повтор должен попробовать снова")


class TestSpecLlmFulltext(unittest.TestCase):
    """LK_SPEC_LLM_FULLTEXT — извлечение по полному тексту ТЗ (Фаза 4 плана
    piped-forging-flame, extract_from_text_cached). Дальний фолбэк: зовётся, когда
    ни таблицы, ни названия недостаточно. Кэш — по паре (purchase_id, name), НЕ
    по одному названию, как у extract_cached: то же название в другой закупке
    стоит за другим документом."""

    def setUp(self):
        self._prev_flag = os.environ.get("LK_SPEC_LLM_FULLTEXT")
        os.environ["LK_SPEC_LLM_FULLTEXT"] = "1"
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(self.db_path)  # init_db создаст заново
        self.sm = _fresh_spec_llm(self.db_path)

    def tearDown(self):
        if self._prev_flag is None:
            os.environ.pop("LK_SPEC_LLM_FULLTEXT", None)
        else:
            os.environ["LK_SPEC_LLM_FULLTEXT"] = self._prev_flag
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_короткое_название_llm_всё_равно_зовёт(self):
        # ⚠️ В отличие от extract_cached — здесь НЕТ порога длины названия:
        # короткое имя само по себе не говорит, что в тексте нет характеристик.
        llm = ReqsLLM([{"key": "материал", "operator": "eq", "value": "фанера",
                        "unit": "", "hardness": "soft", "type": "technical", "raw": "материал: фанера"}])
        reqs = self.sm.extract_from_text_cached("eis_1", "Бахилы", "текст ТЗ про фанеру", client=llm)
        self.assertEqual(len(reqs), 1)
        self.assertEqual(llm.calls, 1)

    def test_пустой_текст_llm_не_зовёт(self):
        llm = ReqsLLM()
        reqs = self.sm.extract_from_text_cached("eis_1", "Бахилы", "", client=llm)
        self.assertEqual(reqs, [])
        self.assertEqual(llm.calls, 0)

    def test_флаг_выключен_llm_не_зовётся(self):
        os.environ["LK_SPEC_LLM_FULLTEXT"] = "0"
        sm2 = _fresh_spec_llm(self.db_path)
        llm = ReqsLLM([{"key": "a", "operator": "present", "value": True,
                        "unit": "", "hardness": "soft", "type": "technical", "raw": "a"}])
        reqs = sm2.extract_from_text_cached("eis_1", "Бахилы", "текст ТЗ", client=llm)
        self.assertEqual(reqs, [])
        self.assertEqual(llm.calls, 0, "флаг выключен — деньги не тратим")

    def test_одно_название_в_разных_закупках_не_путает_кэш(self):
        # ⚠️ Ровно то, ради чего кэш здесь по (purchase_id, name), а не по name:
        # одинаковое короткое имя, но РАЗНЫЙ текст документа — обязаны дать
        # РАЗНЫЙ результат, а не второй раз вернуть кэш первой закупки.
        llm1 = ReqsLLM([{"key": "материал", "operator": "eq", "value": "фанера",
                         "unit": "", "hardness": "soft", "type": "technical", "raw": "фанера"}])
        r1 = self.sm.extract_from_text_cached("eis_1", "Изделие", "текст про фанеру", client=llm1)
        llm2 = ReqsLLM([{"key": "материал", "operator": "eq", "value": "металл",
                         "unit": "", "hardness": "soft", "type": "technical", "raw": "металл"}])
        r2 = self.sm.extract_from_text_cached("eis_2", "Изделие", "текст про металл", client=llm2)
        self.assertEqual(r1[0]["value"], "фанера")
        self.assertEqual(r2[0]["value"], "металл")
        self.assertEqual(llm1.calls, 1)
        self.assertEqual(llm2.calls, 1, "вторая закупка обязана позвать LLM заново, не взять чужой кэш")

    def test_повтор_той_же_пары_идёт_из_кэша(self):
        llm = ReqsLLM([{"key": "a", "operator": "present", "value": True,
                        "unit": "", "hardness": "soft", "type": "technical", "raw": "a"}])
        first = self.sm.extract_from_text_cached("eis_1", "Изделие", "текст ТЗ", client=llm)
        second = self.sm.extract_from_text_cached("eis_1", "Изделие", "текст ТЗ", client=llm)
        self.assertEqual(first, second)
        self.assertEqual(llm.calls, 1, "второй вызов той же пары — из кэша, не из сети")

    def test_пустой_результат_тоже_кэшируется(self):
        llm = ReqsLLM([])
        first = self.sm.extract_from_text_cached("eis_1", "Изделие", "текст ни о чём", client=llm)
        second = self.sm.extract_from_text_cached("eis_1", "Изделие", "текст ни о чём", client=llm)
        self.assertEqual(first, [])
        self.assertEqual(second, [])
        self.assertEqual(llm.calls, 1, "честный пустой ответ — тоже успех, кэшируется")

    def test_сбой_llm_не_кэшируется(self):
        class BoomLLM(ReqsLLM):
            def respond(self, request):
                raise RuntimeError("сеть легла")
        first = self.sm.extract_from_text_cached("eis_1", "Изделие", "текст ТЗ", client=BoomLLM([]))
        self.assertEqual(first, [], "сбой не должен ронять вызывающего")
        llm2 = ReqsLLM([{"key": "ok", "operator": "present", "value": True,
                         "unit": "", "hardness": "soft", "type": "technical", "raw": "ok"}])
        second = self.sm.extract_from_text_cached("eis_1", "Изделие", "текст ТЗ", client=llm2)
        self.assertEqual(len(second), 1, "сбой не кэшируется — повтор должен попробовать снова")

    def test_others_передаётся_как_прицел_на_позицию(self):
        # extractor.extract_requirements сам скоупит извлечение по position={name,others} —
        # здесь только проверяем, что параметр реально доезжает до вызова модели.
        llm = ReqsLLM([{"key": "a", "operator": "present", "value": True,
                        "unit": "", "hardness": "soft", "type": "technical", "raw": "a"}])
        self.sm.extract_from_text_cached("eis_1", "Клинок", "текст многолотового ТЗ",
                                          others=["Рукоятка", "Сумка"], client=llm)
        self.assertIn("Рукоятка", llm.last_user)
        self.assertIn("Клинок", llm.last_user)


if __name__ == "__main__":
    unittest.main()
