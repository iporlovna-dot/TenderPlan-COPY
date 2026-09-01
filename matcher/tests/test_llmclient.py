"""Тесты llmclient: диспетчеризация structured_create по ФОРМЕ клиента (Anthropic vs
OpenAI-совместимый) и выбор провайдера по LK_LLM_PROVIDER. Без сети — оба клиента моки.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from _llm_mock import ReqsLLM, ReqsLLMOpenAI  # noqa: E402
from llmclient import get_default_client, has_api_key, structured_create  # noqa: E402

_SCHEMA = {"type": "object", "properties": {"requirements": {"type": "array"}},
           "required": ["requirements"]}


def check(name, ok):
    print(("  ✓ " if ok else "  ✗ ") + name)
    return ok


def test_anthropic_form():
    r = []
    llm = ReqsLLM()
    data = structured_create(llm, "some-model", "sys", "user text", _SCHEMA)
    r.append(check("вернул requirements из ответа", data["requirements"][0]["key"] == "материал"))
    r.append(check("ровно один вызов", llm.calls == 1))
    r.append(check("system ушёл отдельным kwarg", llm.last["system"] == "sys"))
    r.append(check("user ушёл единственным сообщением", llm.last["messages"] == [{"role": "user", "content": "user text"}]))
    r.append(check("output_config.json_schema передан", llm.last["output_config"]["format"]["type"] == "json_schema"))
    return r


def test_anthropic_refusal_and_max_tokens():
    r = []

    class RefusalClient(ReqsLLM):
        def create(self, **kwargs):
            resp = super().create(**kwargs)
            resp.stop_reason = "refusal"
            return resp
    try:
        structured_create(RefusalClient(), "m", "s", "u", _SCHEMA)
        r.append(check("refusal кидает RuntimeError", False))
    except RuntimeError:
        r.append(check("refusal кидает RuntimeError", True))

    class TruncatedClient(ReqsLLM):
        def create(self, **kwargs):
            resp = super().create(**kwargs)
            resp.stop_reason = "max_tokens"
            return resp
    try:
        structured_create(TruncatedClient(), "m", "s", "u", _SCHEMA)
        r.append(check("max_tokens кидает RuntimeError", False))
    except RuntimeError:
        r.append(check("max_tokens кидает RuntimeError", True))
    return r


def test_openai_form():
    r = []
    llm = ReqsLLMOpenAI()
    data = structured_create(llm, "deepseek-chat", "sys", "user text", _SCHEMA)
    r.append(check("вернул requirements из ответа", data["requirements"][0]["key"] == "материал"))
    r.append(check("ровно один вызов", llm.calls == 1))
    r.append(check("system+schema слиты в одно сообщение", llm.last["messages"][0]["role"] == "system"
                   and "sys" in llm.last["messages"][0]["content"]
                   and "requirements" in llm.last["messages"][0]["content"]))
    r.append(check("user — второе сообщение", llm.last["messages"][1] == {"role": "user", "content": "user text"}))
    r.append(check("response_format=json_object", llm.last["response_format"] == {"type": "json_object"}))
    return r


def test_openai_length_truncation():
    r = []
    llm = ReqsLLMOpenAI(finish_reason="length")
    try:
        structured_create(llm, "deepseek-chat", "s", "u", _SCHEMA)
        r.append(check("finish_reason=length кидает RuntimeError", False))
    except RuntimeError:
        r.append(check("finish_reason=length кидает RuntimeError", True))
    return r


def test_empty_and_malformed_response():
    r = []

    class EmptyLLM(ReqsLLM):
        def respond(self, request):
            return ""
    try:
        structured_create(EmptyLLM(), "m", "s", "u", _SCHEMA)
        r.append(check("пустой ответ кидает RuntimeError", False))
    except RuntimeError:
        r.append(check("пустой ответ кидает RuntimeError", True))

    class BrokenJsonLLM(ReqsLLM):
        def respond(self, request):
            return "{не json"
    try:
        structured_create(BrokenJsonLLM(), "m", "s", "u", _SCHEMA)
        r.append(check("битый JSON кидает JSONDecodeError", False))
    except json.JSONDecodeError:
        r.append(check("битый JSON кидает JSONDecodeError", True))
    return r


def test_provider_selection(monkeypatch=None):
    r = []
    prev = os.environ.get("LK_LLM_PROVIDER")
    prev_ds_key = os.environ.get("DEEPSEEK_API_KEY")
    prev_an_key = os.environ.get("ANTHROPIC_API_KEY")
    try:
        os.environ.pop("DEEPSEEK_API_KEY", None)
        os.environ.pop("ANTHROPIC_API_KEY", None)

        os.environ["LK_LLM_PROVIDER"] = "deepseek"
        r.append(check("deepseek без ключа → has_api_key=False", has_api_key() is False))
        os.environ["DEEPSEEK_API_KEY"] = "sk-test"
        r.append(check("deepseek с ключом → has_api_key=True", has_api_key() is True))
        client = get_default_client()
        r.append(check("deepseek клиент — OpenAI-форма (.chat.completions)",
                       hasattr(client, "chat") and hasattr(client.chat, "completions")))

        os.environ["LK_LLM_PROVIDER"] = "anthropic"
        r.append(check("anthropic без ключа → has_api_key=False", has_api_key() is False))
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test"
        r.append(check("anthropic с ключом → has_api_key=True", has_api_key() is True))
        client = get_default_client()
        r.append(check("anthropic клиент — Anthropic-форма (.messages)", hasattr(client, "messages")))

        os.environ["LK_LLM_PROVIDER"] = "unknown-provider"
        try:
            get_default_client()
            r.append(check("неизвестный провайдер кидает ValueError", False))
        except ValueError:
            r.append(check("неизвестный провайдер кидает ValueError", True))
    finally:
        for k, v in (("LK_LLM_PROVIDER", prev), ("DEEPSEEK_API_KEY", prev_ds_key),
                     ("ANTHROPIC_API_KEY", prev_an_key)):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return r


def main():
    r = (test_anthropic_form() + test_anthropic_refusal_and_max_tokens()
         + test_openai_form() + test_openai_length_truncation()
         + test_empty_and_malformed_response() + test_provider_selection())
    passed = sum(r)
    print("\n%d/%d passed" % (passed, len(r)))
    sys.exit(0 if passed == len(r) else 1)


if __name__ == "__main__":
    main()
