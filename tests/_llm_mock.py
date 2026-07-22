"""Общий мок anthropic-клиента для юнит-тестов LLM-функций (keymatch, extractor).

Юнит-тесты LLM-обёрток не ходят в сеть: подменяют `client`, отдавая заготовленный
JSON-ответ и записывая исходящий запрос (какой промпт/полезная нагрузка ушла в модель).
`client.messages.create(**kwargs)` разрешается в `self.create` (messages = self).

Подкласс переопределяет `respond(request) -> str` — JSON-текст ответа модели.
`request` — dict kwargs вызова (`model`, `max_tokens`, `system`, `messages`, `output_config`).
Полезные срезы: `.calls`, `.requests`, `.last`, `.last_user` (контент первого user-сообщения).
"""
from __future__ import annotations

import json


class _Block:
    type = "text"

    def __init__(self, text):
        self.text = text


class _Resp:
    def __init__(self, text):
        self.content = [_Block(text)]
        self.stop_reason = "end_turn"


class RecordingClient:
    """База: считает вызовы, пишет запросы, возвращает _Resp(respond(request))."""

    def __init__(self):
        self.calls = 0
        self.requests = []
        self.messages = self  # client.messages.create(...) → self.create(...)

    def create(self, **kwargs):
        self.calls += 1
        self.requests.append(kwargs)
        return _Resp(self.respond(kwargs))

    @property
    def last(self):
        return self.requests[-1] if self.requests else None

    @property
    def last_user(self):
        return self.last["messages"][0]["content"] if self.last else None

    def respond(self, request) -> str:  # noqa: D401
        raise NotImplementedError("подкласс задаёт JSON-ответ модели")


class ChecksLLM(RecordingClient):
    """Для keymatch.align_values: satisfies=true для ключей из ok_keys."""

    def __init__(self, ok_keys):
        super().__init__()
        self.ok_keys = set(ok_keys)

    @property
    def last_keys(self):
        return {p["key"] for p in json.loads(self.last_user)}

    def respond(self, request):
        payload = json.loads(request["messages"][0]["content"])
        return json.dumps({"checks": [
            {"key": p["key"], "satisfies": p["key"] in self.ok_keys} for p in payload
        ]})


class ReqsLLM(RecordingClient):
    """Для extractor.extract_requirements: фиксированный requirements[]."""

    def __init__(self, requirements=None):
        super().__init__()
        self._reqs = requirements if requirements is not None else [
            {"key": "материал", "operator": "eq", "value": "нитриловый латекс",
             "unit": "", "hardness": "hard", "type": "technical", "raw": "материал: нитрил"},
        ]

    def respond(self, request):
        return json.dumps({"requirements": self._reqs}, ensure_ascii=False)
