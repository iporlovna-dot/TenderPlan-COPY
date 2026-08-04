#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Проставить версии локальным css/js-ссылкам во всех страницах site/.

Зачем: на VPS nginx не отдаёт Cache-Control, поэтому браузер спокойно держит
старую копию файла после деплоя. Пользователь обновляет страницу и не видит
изменений — так уже было. Версия в ссылке (?v=<хэш содержимого>) заставляет
браузер забрать новый файл, потому что меняется сам URL.

Версия — sha1 от содержимого, а не дата и не ручной счётчик: не изменил файл →
версия та же → кэш продолжает работать. Ничего бампать руками не нужно.

Особенно важно для js: если стили обновятся, а скрипт останется старым,
разметка и оформление разъедутся (проверено на обёртке раскрытия карточки —
без неё содержимое вылезало из свёрнутого блока).

Запуск (перед коммитом):  python tools/stamp_assets.py
"""

import glob
import hashlib
import io
import os
import re
import sys

SITE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "site")
# href/src="<путь>[?v=...]" — только локальные css/js, внешние не трогаем
LINK = re.compile(r'((?:href|src)=")((?:css|js)/[\w.-]+\.(?:css|js))(\?v=[0-9a-f]+)?(")')


def digest(path):
    # text-режим: CRLF на Windows и LF на сервере дают одинаковый хэш
    with io.open(path, encoding="utf-8") as f:
        return hashlib.sha1(f.read().encode("utf-8")).hexdigest()[:8]


def main():
    site = os.path.normpath(SITE)
    if not os.path.isdir(site):
        sys.exit("Не найдена папка site: %s" % site)

    cache, changed, missing = {}, 0, []

    def stamp(m):
        rel = m.group(2)
        target = os.path.join(site, *rel.split("/"))
        if not os.path.isfile(target):
            missing.append(rel)
            return m.group(0)          # чужой/отсутствующий файл — оставляем как есть
        if rel not in cache:
            cache[rel] = digest(target)
        return "%s%s?v=%s%s" % (m.group(1), rel, cache[rel], m.group(4))

    for page in sorted(glob.glob(os.path.join(site, "*.html"))):
        src = io.open(page, encoding="utf-8").read()
        out = LINK.sub(stamp, src)
        if out != src:
            io.open(page, "w", encoding="utf-8", newline="").write(out)
            changed += 1
            print("  обновлена %s" % os.path.basename(page))

    print()
    for rel, ver in sorted(cache.items()):
        print("  %-22s ?v=%s" % (rel, ver))
    if missing:
        print("\n  пропущены (файла нет): %s" % ", ".join(sorted(set(missing))))
    print("\nСтраниц изменено: %d" % changed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
