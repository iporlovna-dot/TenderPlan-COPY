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


if __name__ == "__main__":
    unittest.main()
