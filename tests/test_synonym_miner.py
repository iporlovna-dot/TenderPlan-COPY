"""Тесты майнера синонимов (src/synonym_miner.py). Механика на FakeEmbedder, без сети/модели."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ["SPECMATCH_EMBED"] = "off"  # default_embedder не грузит bge-m3; путь эмбеддинга — инъекцией

import numpy as np

from synonym_miner import mine_candidates, suggest_groups


def check(name, ok):
    print(("  ✓ " if ok else "  ✗ ") + name)
    return ok


class FakeEmbedder:
    """Multi-hot по лексике: косинус = доля общих слов. Управляемое сходство без модели."""

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


def test_mines_near_synonym_names():
    # «разрешение камеры» и «разрешение видеокамеры» делят слова «разрешение»+«камер» → высокий косинус
    fields = {"разрешение_камеры": "1280x720", "разрешение_видеокамеры": "1280x720",
              "материал": "сталь"}
    emb = FakeEmbedder(["разрешение", "камер", "1280", "материал", "сталь"])
    cands = mine_candidates(fields, existing_groups=None, embedder=emb, threshold=0.6)
    pairs = {frozenset((a, b)) for _, a, b in cands}
    r = []
    r.append(check("предложена пара разрешение_камеры↔видеокамеры",
                   frozenset(("разрешение_камеры", "разрешение_видеокамеры")) in pairs))
    r.append(check("материал не притянут к разрешению",
                   frozenset(("материал", "разрешение_камеры")) not in pairs))
    return r


def test_excludes_already_grouped():
    fields = {"разрешение_камеры": "1", "разрешение_видеокамеры": "1"}
    emb = FakeEmbedder(["разрешение", "камер", "1"])
    existing = [["разрешение_камеры", "разрешение_видеокамеры"]]
    cands = mine_candidates(fields, existing_groups=existing, embedder=emb, threshold=0.6)
    return [check("уже-сгруппированную пару не предлагаем повторно", cands == [])]


def test_excludes_morphology_duplicates():
    # «объем_памяти» vs «объём_памяти» — только ё/е: _kname равны → детерм. слой берёт, не кандидат
    fields = {"объем_памяти": 8, "объём_памяти": 32}
    emb = FakeEmbedder(["объем", "объём", "памяти"])
    cands = mine_candidates(fields, existing_groups=None, embedder=emb, threshold=0.5)
    return [check("морфологический дубль (ё/е) не предлагается", cands == [])]


def test_suggest_groups_union():
    cands = [(0.9, "a", "b"), (0.88, "b", "c"), (0.86, "x", "y")]
    groups = {frozenset(g) for g in suggest_groups(cands)}
    r = []
    r.append(check("a,b,c слиты в одну группу", frozenset(("a", "b", "c")) in groups))
    r.append(check("x,y — отдельная группа", frozenset(("x", "y")) in groups))
    return r


def test_degradation_without_model():
    cands = mine_candidates({"a": 1, "b": 2}, embedder=None)  # default отключён флагом
    return [check("нет модели → нет кандидатов (не падаем)", cands == [])]


def main():
    r = (test_mines_near_synonym_names() + test_excludes_already_grouped()
         + test_excludes_morphology_duplicates() + test_suggest_groups_union()
         + test_degradation_without_model())
    passed = sum(r)
    print("\n%d/%d passed" % (passed, len(r)))
    sys.exit(0 if passed == len(r) else 1)


if __name__ == "__main__":
    main()
