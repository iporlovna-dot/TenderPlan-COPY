"""Тесты семантического отсева (Шаг 3, plan.md). Механика на FakeEmbedder (без сети/модели):
ранжирование по сходству, семантический подхват при РАЗНЫХ словах, порог, деградация без модели."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# Дефолтный эмбеддер не должен грузить реальную bge-m3 в тестах: эмбеддинг-путь проверяем инъекцией
# FakeEmbedder, а путь деградации (embedder=None) — этим флагом (default_embedder вернёт None).
os.environ["SPECMATCH_EMBED"] = "off"

import numpy as np

from schema import Attribute, Product, Purchase
from semantic_filter import filter_relevant, product_text, rank


def check(name, ok):
    print(("  ✓ " if ok else "  ✗ ") + name)
    return ok


class FakeEmbedder:
    """Multi-hot по общей лексике: вектор = нормированная сумма осей встреченных слов. Косинус =
    доля общих слов → моделирует семантический подхват (разные формулировки, общий смысл)."""

    def __init__(self, vocab):
        self.vocab = [v.lower() for v in vocab]

    def encode(self, texts):
        out = np.zeros((len(texts), len(self.vocab)), dtype=np.float32)
        for i, t in enumerate(texts):
            tl = t.lower()
            for j, w in enumerate(self.vocab):
                if w in tl:
                    out[i, j] = 1.0
            n = np.linalg.norm(out[i])
            if n > 0:
                out[i] /= n
        return out


VOCAB = ["ларингоскоп", "клинок", "интубац", "видео", "перчат", "нитрил"]

PRODUCT = Product(id="kawe-mac", name="Клинок для ларингоскопа, тип Макинтош", attributes=[
    Attribute(key="тип_клинка", value="макинтош"),
    Attribute(key="назначение", value="интубация трахеи при ИВЛ"),
])

# «Точная» (те же слова), «семантическая» (ДРУГИЕ слова, общий смысл — keyword-фильтр бы пропустил),
# «чужая» (перчатки).
P_EXACT = Purchase(id="1", subject="Ларингоскоп с клинком Макинтош для интубации")
P_SEMANTIC = Purchase(id="2", subject="Устройство для интубации трахеи с видеоканалом")
P_IRRELEVANT = Purchase(id="3", subject="Перчатки нитриловые смотровые нестерильные")


def test_product_text():
    txt = product_text(PRODUCT, extra="Клинки для ларингоскопа")
    r = []
    r.append(check("включает название", "ларингоскоп" in txt.lower()))
    r.append(check("включает идентиф. характеристику (назначение)", "интубац" in txt.lower()))
    r.append(check("включает extra-контекст", "клинки" in txt.lower()))
    return r


def test_rank_orders_by_similarity():
    emb = FakeEmbedder(VOCAB)
    scored = rank([P_IRRELEVANT, P_SEMANTIC, P_EXACT], PRODUCT, embedder=emb)
    order = [p.id for p, _ in scored]
    sims = {p.id: s for p, s in scored}
    r = []
    r.append(check("порядок по убыванию сходства: точная > семантическая > чужая",
                   order == ["1", "2", "3"]))
    r.append(check("семантическая (другие слова) подхвачена выше чужой", sims["2"] > sims["3"]))
    r.append(check("чужая (перчатки) внизу с нулевым сходством", sims["3"] == 0.0))
    return r


def test_filter_relevant_threshold():
    emb = FakeEmbedder(VOCAB)
    # порог между семантической (>0) и чужой (0): чужую отсекаем, релевантные оставляем
    kept = filter_relevant([P_EXACT, P_SEMANTIC, P_IRRELEVANT], PRODUCT, min_sim=0.1, embedder=emb)
    ids = [p.id for p, _ in kept]
    r = []
    r.append(check("чужая отсечена порогом", "3" not in ids))
    r.append(check("обе релевантные оставлены", set(ids) == {"1", "2"}))
    return r


def test_degradation_without_model():
    # embedder не инъектирован, default_embedder отключён флагом → мягкая деградация
    scored = rank([P_EXACT, P_SEMANTIC, P_IRRELEVANT], PRODUCT)
    r = []
    r.append(check("без модели порядок сохранён (не теряем/не путаем)",
                   [p.id for p, _ in scored] == ["1", "2", "3"]))
    r.append(check("без модели sim=None", all(s is None for _, s in scored)))
    kept = filter_relevant([P_EXACT, P_IRRELEVANT], PRODUCT, min_sim=0.9)
    r.append(check("без модели filter не теряет кандидатов", len(kept) == 2))
    return r


def test_empty_input():
    return [check("пустой вход → пустой список", rank([], PRODUCT, embedder=FakeEmbedder(VOCAB)) == [])]


def main():
    r = (test_product_text() + test_rank_orders_by_similarity() + test_filter_relevant_threshold()
         + test_degradation_without_model() + test_empty_input())
    passed = sum(r)
    print("\n%d/%d passed" % (passed, len(r)))
    sys.exit(0 if passed == len(r) else 1)


if __name__ == "__main__":
    main()
