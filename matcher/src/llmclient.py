"""Провайдер-агностичный LLM-клиент: единая точка выбора и вызова модели.

Зачем. `extractor.py`/`keymatch.py`/`server/app/spec_llm.py` ходят в LLM для
извлечения требований и семантической сверки полей. До 2026-09 это был всегда
Anthropic — но выяснилось, что VPS физически в России, а Anthropic не обслуживает
оттуда запросы (403 forbidden на КАЖДЫЙ реальный вызов, не разовый сбой ключа, см.
CLAUDE.md «Anthropic API — гео-блок»). DeepSeek (китайский провайдер, OpenAI-
совместимый API) с того же VPS отвечает нормально (проверено: HTTP 401 без ключа,
не таймаут/сброс).

Провайдер выбирается ОДНИМ флагом `LK_LLM_PROVIDER` (env, по умолчанию `deepseek`) —
не отдельным параметром в каждой функции. Anthropic остаётся в коде:
`LK_LLM_PROVIDER=anthropic` включает его обратно одной переменной, без переписывания
вызывающего кода — на случай, если гео-блок снимется, или для сравнения качества.

Диспетчеризация вызова (`structured_create`) идёт по ФОРМЕ клиента (`.messages` →
Anthropic, иначе — OpenAI-совместимый), а не по отдельному параметру provider — так
тесты с моком (`matcher/tests/_llm_mock.py`) продолжают работать без изменений
независимо от того, что стоит в LK_LLM_PROVIDER на машине, где их гоняют.
"""
from __future__ import annotations

import json
import os
from typing import Optional


class ModelOutputError(RuntimeError):
    """Модель ОТВЕТИЛА, но ответ непригоден: отказ, обрыв по max_tokens/length, пустой
    текст. Отдельный тип, а не голый RuntimeError — чтобы вызывающий код (keymatch.py)
    мог мягко деградировать именно на это, не глотая заодно настоящий сбой транспорта
    (сеть, авторизация, лимиты — они бывают любым другим исключением, в т.ч. в тестах
    RuntimeError от мока). Тот сбой обязан долетать до внешнего кэширующего слоя
    (server/app/spec_match.py `_llm_align_cached`/`_align_values_cached`, `spec_llm.py`),
    иначе он закэшируется как «нечего сводить» — навсегда, до следующего бампа кэша."""


# provider -> (base_url, переменная окружения с ключом)
_OPENAI_COMPAT = {
    "deepseek": ("https://api.deepseek.com", "DEEPSEEK_API_KEY"),
}


def _provider() -> str:
    return os.getenv("LK_LLM_PROVIDER", "deepseek").strip().lower()


def has_api_key(provider: Optional[str] = None) -> bool:
    """Настроен ли ключ активного (или указанного) провайдера — для ENABLED-флагов
    вызывающего кода (`spec_llm.py`): не создавать клиент и не пробовать вызов,
    если ключа нет вообще."""
    provider = provider or _provider()
    if provider == "anthropic":
        return bool(os.getenv("ANTHROPIC_API_KEY"))
    cfg = _OPENAI_COMPAT.get(provider)
    return bool(cfg and os.getenv(cfg[1]))


def get_default_client(timeout: Optional[float] = None):
    """Построить боевой клиент активного провайдера (`LK_LLM_PROVIDER`).

    Без инъекции — вызывающие функции сами решают `client or get_default_client()`;
    тесты подменяют `client=` заготовленным моком и сюда не заходят."""
    provider = _provider()
    kwargs = {"timeout": timeout} if timeout else {}
    if provider == "anthropic":
        import anthropic
        return anthropic.Anthropic(**kwargs)
    cfg = _OPENAI_COMPAT.get(provider)
    if cfg is None:
        raise ValueError(
            "Неизвестный LK_LLM_PROVIDER=%r (известны: anthropic, %s)"
            % (provider, ", ".join(_OPENAI_COMPAT))
        )
    base_url, key_env = cfg
    api_key = os.environ.get(key_env)
    if not api_key:
        raise RuntimeError("Не задан %s для LK_LLM_PROVIDER=%s" % (key_env, provider))
    from openai import OpenAI
    return OpenAI(api_key=api_key, base_url=base_url, **kwargs)


def structured_create(client, model: str, system: str, user: str, schema: dict,
                       max_tokens: int = 16000) -> dict:
    """system+user+JSON-схема → распарсенный dict.

    Диспетчерится по ФОРМЕ клиента, не по provider (см. докстринг модуля): `.messages`
    — Anthropic (constrained decoding через `output_config.json_schema` — модель
    ГАРАНТИРОВАННО отдаёт валидный JSON по схеме); иначе — OpenAI-совместимый
    (DeepSeek): у него только мягкий `response_format=json_object` (валидный JSON, но
    БЕЗ гарантии полей/типов) — схему поэтому вшиваем текстом в system-промпт.

    Бросает `ModelOutputError` на отказ/обрыв модели (симметрично для обоих
    провайдеров: stop_reason=refusal/max_tokens у Anthropic, finish_reason=length у
    OpenAI-формы) и json.JSONDecodeError на нераспарсиваемый ответ — вызывающий код
    (extractor.py пробрасывает дальше как есть; keymatch.py ловит оба и мягко
    деградирует). Сам вызов `client.messages.create`/`client.chat.completions.create`
    НЕ оборачивается — его исключения (сеть, авторизация, лимиты) пробрасываются
    как есть, это НЕ ModelOutputError, см. её докстринг."""
    if hasattr(client, "messages"):
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"format": {"type": "json_schema", "schema": schema}, "effort": "medium"},
        )
        if resp.stop_reason == "refusal":
            raise ModelOutputError("Модель отклонила запрос (stop_reason=refusal)")
        if resp.stop_reason == "max_tokens":
            raise ModelOutputError("Ответ обрезан по max_tokens — сократи вход или подними max_tokens")
        text = next((b.text for b in resp.content if b.type == "text"), "")
    else:
        schema_hint = (
            "\n\nОтветь ТОЛЬКО валидным JSON (без markdown-обёртки ```), СТРОГО по этой схеме:\n"
            + json.dumps(schema, ensure_ascii=False)
        )
        resp = client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "system", "content": system + schema_hint},
                      {"role": "user", "content": user}],
            response_format={"type": "json_object"},
        )
        choice = resp.choices[0]
        if choice.finish_reason == "length":
            raise ModelOutputError("Ответ обрезан по max_tokens — сократи вход или подними max_tokens")
        text = choice.message.content or ""

    if not text:
        raise ModelOutputError("Пустой ответ модели")
    return json.loads(text)
