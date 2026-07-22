"""Тесты сверки КТРУ (src/ktru.py). Коды — реальные из закупок клинков/ларингоскопов."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from ktru import EXACT, GROUP, NONE, ktru_relation, relevant, split_ktru

# коды товаров-клинков KaWe и коды позиций из реальных закупок (см. scratchpad/fullinfo)
CLINOK = "32.50.13.190-00007686"          # клинок одноразовый (товар)
HANDLE = "32.50.13.190-00007697"          # рукоять (товар)
LARYNGO_ASSEMBLY = "32.50.13.190-00007689"  # ларингоскоп В СБОРЕ (закупка 6a6097ac)
PARENT_ONLY = "32.50.13.190"              # родитель без позиции (закупки клинков 6a5f*)
SUCTION = "32.50.50.123"                  # отсасыватель (закупка 6a609cf7)
DIAG = "26.60.12.119"                     # электродиагностика (закупка 6a609cf7)


def check(name, got, exp):
    ok = got == exp
    print(("  ✓ " if ok else "  ✗ ") + "%s: %s (ожидали %s)" % (name, got, exp))
    return ok


def main():
    results = []

    # split
    results.append(check("split полного", split_ktru(CLINOK), ("32.50.13.190", "00007686")))
    results.append(check("split родителя", split_ktru(PARENT_ONLY), ("32.50.13.190", "")))

    # exact — та же позиция каталога
    results.append(check("exact 686=686", ktru_relation([CLINOK], [CLINOK]), EXACT))

    # group — тот же ОКПД2, но клинок vs ларингоскоп в сборе (реальный кейс 6a6097ac)
    results.append(check("group клинок↔сборка", ktru_relation([CLINOK], [LARYNGO_ASSEMBLY]), GROUP))

    # group — закупка указала только родителя (реальный кейс клинков KaWe 6a5f*)
    results.append(check("group клинок↔родитель", ktru_relation([CLINOK], [PARENT_ONLY]), GROUP))

    # none — чужая группа (отсасыватель, электродиагностика — закупка 6a609cf7)
    results.append(check("none клинок↔отсос", ktru_relation([CLINOK], [SUCTION]), NONE))
    results.append(check("none клинок↔диаг", ktru_relation([CLINOK], [DIAG]), NONE))

    # многопозиционный лот: товар совпал с ОДНОЙ из позиций → не теряем (§11.4)
    results.append(check("многолот: одна позиция наша",
                         ktru_relation([CLINOK], [SUCTION, DIAG, PARENT_ONLY]), GROUP))

    # relevant(): отсасыватель+диагностика без клинков → отсекаем
    results.append(check("relevant чужой лот = False", relevant([CLINOK, HANDLE], [SUCTION, DIAG]), False))
    results.append(check("relevant свой лот = True", relevant([CLINOK], [PARENT_ONLY]), True))

    passed = sum(results)
    print("\n%d/%d passed" % (passed, len(results)))
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
