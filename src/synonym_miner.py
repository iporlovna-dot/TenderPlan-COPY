"""Майнер кандидатов в словарь синонимов ИМЁН полей (plan.md Этап 2 п.3 «самообучение»).

Идея: локальные эмбеддинги ОФЛАЙН предлагают пары имён полей, которые близки по смыслу
(«размер_клинка»↔«размеры»), — кандидаты в `key_synonyms` профиля. Человек подтверждает (и
отсеивает ложное — контрасты вроде «миллер»↔«макинтош», что эмбеддинг путает, см. plan §3.7),
после чего пара уходит в ДЕТЕРМИНИРОВАННЫЙ слой align_keys → в следующий раз сведётся без Haiku.

БЕЗОПАСНО by design: майнер только ПРЕДЛАГАЕТ, никогда не применяет сам. Антонимы среди кандидатов —
норма (эмбеддинг их не отличает); их отбраковывает человек. Порог высокий (как align_keys, 0.85).

Использование логики — `mine_candidates`; CLI-обёртка — `scripts/mine_synonyms.py`.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from embed import cosine_matrix, default_embedder
from keymatch import _field_text, _kname

_THRESHOLD = 0.85  # тот же precision-first порог, что align_keys (plan §3.7)


def _grouped_pairs(existing_groups: Optional[List[list]]) -> set:
    """Множество уже-сгруппированных пар имён (по _kname) — их не предлагаем повторно."""
    pairs = set()
    for g in (existing_groups or []):
        norms = [_kname(x) for x in g]
        for i in range(len(norms)):
            for j in range(i + 1, len(norms)):
                pairs.add(frozenset((norms[i], norms[j])))
    return pairs


def mine_candidates(fields: Dict[str, object], existing_groups: Optional[List[list]] = None,
                    embedder=None, threshold: float = _THRESHOLD) -> List[Tuple[float, str, str]]:
    """Найти пары имён полей, близких по эмбеддингу (кандидаты в key_synonyms).

    fields — {имя_поля: образец_значения} (объединение имён карточек и ТЗ). Возвращает
    [(sim, имя_a, имя_b)] по убыванию, БЕЗ: тривиальных морфологических дублей (их и так берёт
    детерм. слой), уже-сгруппированных пар, пар ниже порога. Эмбеддер недоступен → []."""
    embedder = embedder if embedder is not None else default_embedder()
    names = [n for n in fields if n]
    if embedder is None or len(names) < 2:
        return []
    already = _grouped_pairs(existing_groups)
    vecs = embedder.encode([_field_text(n, fields[n]) for n in names])
    sims = cosine_matrix(vecs, vecs)
    out = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            na, nb = _kname(names[i]), _kname(names[j])
            if na == nb:                                  # морфология — уже детерминированно
                continue
            if frozenset((na, nb)) in already:            # уже в словаре
                continue
            s = float(sims[i][j])
            if s >= threshold:
                out.append((s, names[i], names[j]))
    out.sort(reverse=True)
    return out


def suggest_groups(candidates: List[Tuple[float, str, str]]) -> List[List[str]]:
    """Слить пары-кандидаты в связные группы (union-find) → готовые группы для key_synonyms."""
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        parent[find(a)] = find(b)

    for _, a, b in candidates:
        union(a, b)
    groups: Dict[str, list] = {}
    for name in list(parent):
        groups.setdefault(find(name), []).append(name)
    return [sorted(g) for g in groups.values() if len(g) > 1]
