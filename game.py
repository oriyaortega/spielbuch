"""Игровая логика по правилам книги. Без Telegram — только состояние и правила."""
from __future__ import annotations

import json
import random
from pathlib import Path

BAG_SIZE = 7
SPELL_LIMIT = 10
SPELLS = {
    "levitation": "Левитации",
    "fire": "Огня",
    "illusion": "Иллюзии",
    "strength": "Силы",
    "weakness": "Слабости",
    "copy": "Копии",
    "healing": "Исцеления",
    "swimming": "Плавания",
}
STAT_NAMES = {"skill": "МАСТЕРСТВО", "stamina": "ВЫНОСЛИВОСТЬ", "luck": "УДАЧА", "gold": "Золото"}


class Book:
    def __init__(self, path: str | Path):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        self.title = data["title"]
        self.rules = data["rules"]
        self.preface = data["preface"]
        self.paras = {int(k): v for k, v in data["paragraphs"].items()}

    def __getitem__(self, n: int) -> dict:
        return self.paras[n]

    def __contains__(self, n: int) -> bool:
        return n in self.paras


class GameError(Exception):
    """Действие запрещено правилами — текст ошибки показывается игроку."""


def d6(rng: random.Random, n: int = 1) -> list[int]:
    return [rng.randint(1, 6) for _ in range(n)]


# ---------------------------------------------------------------- герой
def new_state(rng: random.Random) -> dict:
    s = {"stage": "roll", "spells": {k: 0 for k in SPELLS}}
    roll_stats(s, rng)
    return s


def roll_stats(s: dict, rng: random.Random) -> dict:
    sk, st, lu = d6(rng)[0], sum(d6(rng, 2)), d6(rng)[0]
    s.update(
        skill=sk + 6, skill_max=sk + 6,
        stamina=st + 12, stamina_max=st + 12,
        luck=lu + 6, luck_max=lu + 6,
        gold=15, flask=2, items=[], notes=[],
        para=0, history=[], applied=[], battle=None, awaiting=None,
    )
    return s


def spells_total(s: dict) -> int:
    return sum(s["spells"].values())


def change_spell(s: dict, key: str, delta: int):
    if s["stage"] != "spells":
        raise GameError("Заклятия выбираются только перед началом пути.")
    new = s["spells"][key] + delta
    if new < 0:
        return
    if delta > 0 and spells_total(s) >= SPELL_LIMIT:
        raise GameError(f"Не больше {SPELL_LIMIT} заклятий.")
    s["spells"][key] = new


# ---------------------------------------------------------------- переходы
def go(s: dict, book: Book, n: int):
    if n not in book:
        raise GameError(f"Параграфа {n} нет в книге.")
    if s["battle"] and alive(s["battle"]):
        raise GameError("Сначала закончите бой: победите, бегите или нажмите «Выйти из боя».")
    s["para"] = n
    s["history"].append(n)
    s["applied"] = []
    s["battle"] = None
    p = book[n]
    if p["ending"] == "victory":
        s["stage"] = "won"
    elif p["ending"] == "end":
        s["stage"] = "dead"
    else:
        s["stage"] = "play"


def follow_link(s: dict, book: Book, idx: int) -> str | None:
    """Переход по кнопке. Если переход требует заклятия — тратит его."""
    link = book[s["para"]]["links"][idx]
    used = None
    if link["spell"]:
        key = link["spell"]
        if s["spells"].get(key, 0) <= 0:
            raise GameError(f"У вас не осталось заклятия {SPELLS[key]}.")
        if s["battle"]:
            s["battle"] = None  # заклятие прерывает бой: дальше ведёт книга
        s["spells"][key] -= 1
        used = SPELLS[key]
    go(s, book, link["to"])
    return used


def apply_effect(s: dict, book: Book, idx: int) -> str:
    if idx in s["applied"]:
        raise GameError("Уже применено.")
    e = book[s["para"]]["effects"][idx]
    s["applied"].append(idx)
    msg = change_stat(s, e["stat"], e["delta"], nocap=e["nocap"])
    return msg


def change_stat(s: dict, stat: str, delta: int, nocap: bool = False) -> str:
    val = s[stat] + delta
    if stat != "gold":
        cap = s[f"{stat}_max"]
        if val > cap:
            if nocap:
                s[f"{stat}_max"] = val
            else:
                val = cap
    s[stat] = max(0, val)
    if stat == "stamina" and s["stamina"] <= 0:
        s["stage"] = "dead"
    return f"{STAT_NAMES[stat]}: {s[stat]}"


# ---------------------------------------------------------------- удача, фляга, лечение
def test_luck(s: dict, rng: random.Random) -> tuple[list[int], bool]:
    if s["luck"] <= 0:
        return [], False
    roll = d6(rng, 2)
    lucky = sum(roll) <= s["luck"]
    s["luck"] -= 1
    return roll, lucky


def drink(s: dict) -> str:
    if s["battle"]:
        raise GameError("Во время боя пить нельзя.")
    if s["flask"] <= 0:
        raise GameError("Фляга пуста.")
    s["flask"] -= 1
    change_stat(s, "stamina", 2)
    return f"Глоток из фляги: ВЫНОСЛИВОСТЬ {s['stamina']}/{s['stamina_max']}. Глотков осталось: {s['flask']}."


def cast_healing(s: dict) -> str:
    if s["battle"]:
        raise GameError("Заклятие Исцеления нельзя применять во время боя.")
    if s["spells"]["healing"] <= 0:
        raise GameError("Заклятий Исцеления не осталось.")
    s["spells"]["healing"] -= 1
    change_stat(s, "stamina", 8)
    return f"Заклятие Исцеления: ВЫНОСЛИВОСТЬ {s['stamina']}/{s['stamina_max']}."


def spend_spell(s: dict, key: str) -> str:
    """Ручное использование заклятия (когда в тексте нет отдельного номера)."""
    if key == "healing":
        return cast_healing(s)
    if s["spells"][key] <= 0:
        raise GameError(f"Заклятий {SPELLS[key]} не осталось.")
    s["spells"][key] -= 1
    return f"Использовано заклятие {SPELLS[key]}."


# ---------------------------------------------------------------- мешок
def add_item(s: dict, name: str):
    name = name.strip()[:60]
    if not name:
        raise GameError("Пустое название.")
    if len(s["items"]) >= BAG_SIZE:
        raise GameError(f"В мешке только {BAG_SIZE} мест — сначала что-нибудь выложите.")
    s["items"].append(name)


def drop_item(s: dict, idx: int) -> str:
    if s["battle"]:
        raise GameError("Во время боя рыться в мешке нельзя.")
    return s["items"].pop(idx)


# ---------------------------------------------------------------- бой
def start_battle(s: dict, book: Book):
    p = book[s["para"]]
    if not p["enemies"]:
        raise GameError("Здесь не с кем сражаться.")
    s["battle"] = {
        "enemies": [dict(e, max=e["stamina"], mod=0) for e in p["enemies"]],
        "together": p["together"],
        "my_mod": 0,
        "round": 0,
        "log": [],
        "copy_used": False,
    }


def alive(b: dict) -> list[int]:
    return [i for i, e in enumerate(b["enemies"]) if e["stamina"] > 0]


def battle_round(s: dict, target: int, rng: random.Random) -> str:
    b = s["battle"]
    live = alive(b)
    if target not in live:
        raise GameError("Этот противник уже повержен.")
    if not b["together"] and target != live[0]:
        raise GameError("Противники нападают по очереди — сначала разберитесь с первым.")
    b["round"] += 1
    my_roll = d6(rng, 2)
    my_as = sum(my_roll) + s["skill"] + b["my_mod"]
    lines = [f"<b>Раунд {b['round']}</b>. Ваша СИЛА УДАРА: {fmt_roll(my_roll)} + {s['skill'] + b['my_mod']} = <b>{my_as}</b>"]
    fighters = live if b["together"] else [target]
    for i in fighters:
        e = b["enemies"][i]
        r = d6(rng, 2)
        e_as = sum(r) + e["skill"] + e["mod"]
        line = f"{e['name']}: {fmt_roll(r)} + {e['skill'] + e['mod']} = {e_as} — "
        if i == target:
            if my_as > e_as:
                e["stamina"] = max(0, e["stamina"] - 2)
                line += "вы ранили!" + (" Повержен." if e["stamina"] == 0 else "")
            elif my_as < e_as:
                s["stamina"] = max(0, s["stamina"] - 2)
                line += "вас ранили (−2)."
            else:
                line += "удар парирован."
        else:
            if e_as > my_as:
                s["stamina"] = max(0, s["stamina"] - 2)
                line += "задел вас (−2)."
            else:
                line += "мимо."
        lines.append(line)
    if s["stamina"] <= 0:
        s["stage"] = "dead"
        lines.append("💀 Вы погибли.")
    elif not alive(b):
        lines.append("🏆 Все противники повержены!")
    text = "\n".join(lines)
    b["log"] = (b["log"] + [text])[-3:]
    return text


def copy_fight(s: dict, target: int, rng: random.Random) -> str:
    """Заклятие Копии: двойник врага бьётся с ним до конца."""
    b = s["battle"]
    if b["copy_used"]:
        raise GameError("Копия в этом бою уже создавалась.")
    if s["spells"]["copy"] <= 0:
        raise GameError("Заклятий Копии не осталось.")
    e = b["enemies"][target]
    if e["stamina"] <= 0:
        raise GameError("Этот противник уже повержен.")
    s["spells"]["copy"] -= 1
    b["copy_used"] = True
    copy_st = e["stamina"]
    rounds = 0
    while copy_st > 0 and e["stamina"] > 0 and rounds < 200:
        rounds += 1
        c = sum(d6(rng, 2)) + e["skill"]
        o = sum(d6(rng, 2)) + e["skill"] + e["mod"]
        if c > o:
            e["stamina"] = max(0, e["stamina"] - 2)
        elif o > c:
            copy_st -= 2
    if e["stamina"] <= 0:
        text = f"🪞 Копия сразила противника «{e['name']}» за {rounds} раунд(ов)."
    else:
        text = (f"🪞 {e['name']} победил свою Копию за {rounds} раунд(ов). "
                f"У него осталось {e['stamina']} ВЫНОСЛИВОСТИ — теперь драться вам.")
    b["log"] = (b["log"] + [text])[-3:]
    return text


def flee(s: dict) -> str:
    if not s["battle"]:
        raise GameError("Вы не в бою.")
    s["battle"] = None
    change_stat(s, "stamina", -2)
    return "🏃 Вы бежали, получив последний удар (−2 ВЫНОСЛИВОСТИ). Выберите переход из текста параграфа."


def fmt_roll(r: list[int]) -> str:
    return "+".join(map(str, r)) if len(r) > 1 else str(r[0])
