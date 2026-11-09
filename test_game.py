"""Проверки движка и экранов бота без подключения к Telegram:  python -m pytest -q"""
import os
import random

os.environ.setdefault("DB_PATH", ":memory:")

import bot  # noqa: E402
import game as g  # noqa: E402

BOOK = bot.BOOK


def fresh(seed=1):
    rng = random.Random(seed)
    s = g.new_state(rng)
    s["stage"] = "spells"
    for k in ("copy", "copy", "fire", "healing", "swimming"):
        g.change_spell(s, k, 1)
    g.go(s, BOOK, 1)
    return s, rng


def test_book_complete():
    assert len(BOOK.paras) == 617
    assert BOOK[617]["ending"] == "victory"
    assert [l["to"] for l in BOOK[1]["links"]] == [86, 110]
    for n, p in BOOK.paras.items():
        for l in p["links"]:
            assert 1 <= l["to"] <= 617, (n, l)


def test_stats_in_range():
    for seed in range(200):
        s = g.new_state(random.Random(seed))
        assert 7 <= s["skill"] <= 12 and 14 <= s["stamina"] <= 24 and 7 <= s["luck"] <= 12


def test_spell_limit():
    s = g.new_state(random.Random(0))
    s["stage"] = "spells"
    for _ in range(10):
        g.change_spell(s, "fire", 1)
    try:
        g.change_spell(s, "copy", 1)
        assert False
    except g.GameError:
        pass


def test_spell_link_consumes_and_blocks():
    s, _ = fresh()
    s["para"] = 4  # Плавания (372) / Левитации (103)
    idx_swim = next(i for i, l in enumerate(BOOK[4]["links"]) if l["spell"] == "swimming")
    assert g.follow_link(s, BOOK, idx_swim) == "Плавания"
    assert s["spells"]["swimming"] == 0 and s["para"] == 372
    s["para"] = 4
    idx_lev = next(i for i, l in enumerate(BOOK[4]["links"]) if l["spell"] == "levitation")
    try:
        g.follow_link(s, BOOK, idx_lev)
        assert False
    except g.GameError:
        assert s["para"] == 4


def test_battle_to_the_end():
    s, rng = fresh()
    g.go(s, BOOK, 16)  # три летучие мыши разом
    g.start_battle(s, BOOK)
    assert s["battle"]["together"] and len(s["battle"]["enemies"]) == 3
    s["stamina"] = s["stamina_max"] = 99
    while g.alive(s["battle"]):
        g.battle_round(s, g.alive(s["battle"])[0], rng)
    g.go(s, BOOK, 120)  # после победы переход разрешён
    assert s["battle"] is None


def test_sequential_battle_order():
    s, rng = fresh()
    g.go(s, BOOK, 33)  # Орк, потом Гоблин
    g.start_battle(s, BOOK)
    assert not s["battle"]["together"]
    try:
        g.battle_round(s, 1, rng)
        assert False
    except g.GameError:
        pass


def test_copy_and_flee():
    s, rng = fresh()
    g.go(s, BOOK, 6)
    g.start_battle(s, BOOK)
    g.copy_fight(s, 0, rng)
    assert s["spells"]["copy"] == 1
    try:
        g.copy_fight(s, 1, rng)
        assert False
    except g.GameError:
        pass
    st = s["stamina"]
    g.flee(s)
    assert s["stamina"] == st - 2 and s["battle"] is None


def test_effects_luck_flask_heal():
    s, rng = fresh()
    g.go(s, BOOK, 6)
    luck = s["luck"]
    g.apply_effect(s, BOOK, 0)
    assert s["luck"] == luck - 1
    try:
        g.apply_effect(s, BOOK, 0)
        assert False
    except g.GameError:
        pass
    roll, _ = g.test_luck(s, rng)
    assert len(roll) == 2 and s["luck"] == luck - 2
    s["stamina"] = 1
    g.drink(s); g.drink(s)
    assert s["stamina"] == 5 and s["flask"] == 0
    g.cast_healing(s)
    assert s["stamina"] == min(13, s["stamina_max"])
    g.change_stat(s, "stamina", -100)
    assert s["stage"] == "dead"


def test_ending():
    s, _ = fresh()
    g.go(s, BOOK, 56)
    assert s["stage"] == "dead"
    g.go(s, BOOK, 617)
    assert s["stage"] == "won"


def test_bag_limit():
    s, _ = fresh()
    for i in range(7):
        g.add_item(s, f"вещь {i}")
    try:
        g.add_item(s, "лишняя")
        assert False
    except g.GameError:
        pass


def test_all_views_render_for_every_paragraph():
    s, rng = fresh()
    for n in BOOK.paras:
        s["stage"], s["battle"] = "play", None
        g.go(s, BOOK, n)
        text, markup = bot.para_view(s)
        assert text and markup.inline_keyboard
        for row in markup.inline_keyboard:
            for b in row:
                assert len(b.callback_data.encode()) <= 64
        if BOOK[n]["enemies"] and s["stage"] == "play":
            g.start_battle(s, BOOK)
            bot.battle_view(s)
    for view in (bot.sheet_view, bot.bag_view, bot.magic_view, bot.roll_view, bot.spells_view):
        view(s)
