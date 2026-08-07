"""MVP-бэкенд SpecMatch (Этап 1): API «товар → лента подходящих закупок».

Мультиарендный (изоляция по company_id), защищённая аутентификация (argon2 + JWT +
rate-limit логина). Поверх доказанного ядра: CLI-конвейер (run_auto) пишет вердикты →
ingest в БД → API отдаёт поставщику ранжированную ленту ЕГО компании. Ядро (src/) не тронуто.
"""
import os
import sys

# Ядро (matcher, schema, gapfill …) лежит в src/ — делаем импортируемым из API-слоя.
_SRC = os.path.join(os.path.dirname(os.path.dirname(__file__)), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)
