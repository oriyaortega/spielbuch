"""Telegram-бот: книга-игра «Подземелья Чёрного замка».

Запуск:  BOT_TOKEN=... python bot.py   (или положите токен в файл .env)
"""
from __future__ import annotations

import asyncio
import html
import logging
import os
import random
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton as Btn, InlineKeyboardMarkup, Message

import game as g
from storage import Storage

ROOT = Path(__file__).parent


def load_env():
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"'))


load_env()
BOOK = g.Book(os.getenv("BOOK_PATH", ROOT / "data" / "book.json"))
DB = Storage(os.getenv("DB_PATH", ROOT / "data" / "players.db"))
RNG = random.SystemRandom()
router = Router()


# ================================================================ разметка
def kb(*rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [Btn(text=t, callback_data=d) for t, d in row] for row in rows if row
    ])


MENU_ROW = [("📜 Лист", "sheet"), ("🎒 Мешок", "bag"), ("✨ Магия", "magic")]


def chunks(text: str, limit: int = 3800) -> list[str]:
    parts, cur = [], ""
    for para in text.split("\n"):
        if len(cur) + len(para) + 1 > limit and cur:
            parts.append(cur)
            cur = ""
        cur = f"{cur}\n{para}" if cur else para
    return parts + [cur]


def status_line(s: dict) -> str:
    return (f"⚔️ {s['skill']}/{s['skill_max']}  ❤️ {s['stamina']}/{s['stamina_max']}  "
            f"🍀 {s['luck']}/{s['luck_max']}  💰 {s['gold']}")


def para_view(s: dict) -> tuple[str, InlineKeyboardMarkup]:
    n = s["para"]
    p = BOOK[n]
    text = f"<b>§ {n}</b>\n\n{html.escape(p['text'])}\n\n<i>{status_line(s)}</i>"
    if s["stage"] == "won":
        return text + "\n\n🏆 <b>Победа! Принцесса свободна.</b>", kb([("🔄 Новая игра", "new!")], MENU_ROW[:1])
    if s["stage"] == "dead":
        reason = "Путь окончен." if p["ending"] else "Ваша ВЫНОСЛИВОСТЬ упала до нуля."
        return text + f"\n\n💀 <b>{reason}</b>", kb([("🔄 Начать заново", "new!")], MENU_ROW[:1])

    rows = []
    b = s["battle"]
    if b and g.alive(b):
        rows.append([("⚔️ Вернуться к бою", "fight")])
    for i, l in enumerate(p["links"]):
        mark = "✨ " if l["spell"] else ""
        rows.append([(f"{mark}{l['label']} → {l['to']}", f"L:{n}:{i}")])
    eff = [(f"Применить {e['label']}", f"E:{n}:{i}")
           for i, e in enumerate(p["effects"]) if i not in s["applied"]]
    for i in range(0, len(eff), 2):
        rows.append(eff[i:i + 2])
    extra = []
    if p["enemies"] and not b:
        extra.append(("⚔️ Начать бой", "fight"))
    if p["luck"]:
        extra.append(("🍀 Проверить удачу", "luck"))
    rows += [extra, MENU_ROW]
    return text, kb(*rows)


def battle_view(s: dict) -> tuple[str, InlineKeyboardMarkup]:
    b = s["battle"]
    lines = [f"⚔️ <b>Бой</b> (§ {s['para']}) — " + ("все нападают разом" if b["together"] else "по очереди")]
    for e in b["enemies"]:
        mod = f" (СИЛА УДАРА {e['mod']:+d})" if e["mod"] else ""
        state = "☠️" if e["stamina"] <= 0 else f"❤️ {e['stamina']}/{e['max']}"
        lines.append(f"• {html.escape(e['name'])}: МАС {e['skill']}{mod}, {state}")
    my_mod = f" (СИЛА УДАРА {b['my_mod']:+d})" if b["my_mod"] else ""
    lines.append(f"• <b>Вы</b>: МАС {s['skill']}{my_mod}, ❤️ {s['stamina']}/{s['stamina_max']}")
    if b["log"]:
        lines += ["", b["log"][-1]]
    text = "\n".join(lines)

    if s["stage"] == "dead":
        return text, kb([("🔄 Начать заново", "new!")])
    live = g.alive(b)
    if not live:
        return text, kb([("📖 Дальше по тексту", "show")])
    if b.get("mods_open"):
        text += ("\n\n⚙️ <b>Модификаторы СИЛЫ УДАРА</b>\n"
                 "Нужны, когда книга меняет силу удара: например, «Прибавьте 2 к своей СИЛЕ УДАРА» "
                 "(заклятие Силы) или «уменьшить на 2 СИЛУ УДАРА» врага (заклятие Слабости). "
                 "Действуют до конца боя.")
        rows = [[(f"{b['enemies'][i]['name']}: −1", f"B:mod:{i}:-1"),
                 (f"{b['enemies'][i]['name']}: +1", f"B:mod:{i}:1")] for i in live]
        rows.append([("Вы: −1", "B:mod:me:-1"), ("Вы: +1", "B:mod:me:1")])
        rows.append([("✅ Готово", "B:mods")])
        return text, kb(*rows)

    targets = live if b["together"] else live[:1]
    rows = [[(f"🗡 Ударить: {b['enemies'][i]['name']}", f"B:hit:{i}")] for i in targets]
    if s["spells"]["copy"] > 0 and not b["copy_used"]:
        rows.append([(f"🪞 Копия: {b['enemies'][i]['name']}", f"B:copy:{i}") for i in live][:3])
    rows.append([("🏃 Бежать (−2)", "B:flee"), ("⏹ Выйти из боя", "B:exit")])
    rows.append([("⚙️ Модификаторы", "B:mods"), ("📖 Текст параграфа", "show")])
    return text, kb(*rows)


def sheet_view(s: dict) -> tuple[str, InlineKeyboardMarkup]:
    spells = ", ".join(f"{g.SPELLS[k]} ×{v}" for k, v in s["spells"].items() if v) or "нет"
    items = ", ".join(map(html.escape, s["items"])) or "пусто"
    notes = "\n".join(f"  {i + 1}. {html.escape(t)}" for i, t in enumerate(s["notes"])) or "  —"
    text = (
        "📜 <b>Листок путешественника</b>\n\n"
        f"МАСТЕРСТВО: <b>{s['skill']}</b> / {s['skill_max']}\n"
        f"ВЫНОСЛИВОСТЬ: <b>{s['stamina']}</b> / {s['stamina_max']}\n"
        f"УДАЧА: <b>{s['luck']}</b> / {s['luck_max']}\n"
        f"Золото: <b>{s['gold']}</b>\n"
        f"Фляга: {s['flask']} глотк. (+2 ВЫН каждый)\n"
        f"Заклятия: {spells}\n"
        f"Мешок ({len(s['items'])}/{g.BAG_SIZE}): {items}\n"
        f"Пройдено параграфов: {len(s['history'])}\n\n"
        f"📝 Заметки:\n{notes}"
    )
    rows = [
        [("МАС −1", "S:skill:-1"), ("МАС +1", "S:skill:1")],
        [("ВЫН −1", "S:stamina:-1"), ("ВЫН +1", "S:stamina:1"), ("ВЫН −2", "S:stamina:-2")],
        [("УДАЧА −1", "S:luck:-1"), ("УДАЧА +1", "S:luck:1")],
        [("💰 −5", "S:gold:-5"), ("−1", "S:gold:-1"), ("+1", "S:gold:1"), ("+5", "S:gold:5")],
        [("💧 Глоток из фляги", "drink"), ("📝 Заметка", "ask:note")],
        [("➡️ Перейти к §…", "ask:go"), ("🗑 Стереть заметки", "clearnotes")],
        [("📖 К параграфу", "show")],
    ]
    return text, kb(*rows)


def bag_view(s: dict) -> tuple[str, InlineKeyboardMarkup]:
    text = (f"🎒 <b>Заплечный мешок</b> ({len(s['items'])}/{g.BAG_SIZE})\n"
            "Деньги и оружие в мешок не кладутся. Еду, съев, выкиньте и добавьте ВЫН на Листке.\n\n"
            + ("\n".join(f"{i + 1}. {html.escape(t)}" for i, t in enumerate(s["items"])) or "Пусто."))
    rows = [[(f"🗑 {t[:30]}", f"drop:{i}")] for i, t in enumerate(s["items"])]
    rows.append([("➕ Положить предмет", "ask:item"), ("📖 К параграфу", "show")])
    return text, kb(*rows)


def magic_view(s: dict) -> tuple[str, InlineKeyboardMarkup]:
    text = ("✨ <b>Заклятия</b>\n\nОбычно заклятие тратится само, когда вы жмёте кнопку перехода со ✨. "
            "Здесь можно потратить его вручную — если книга велит применить заклятие без номера параграфа.\n"
            "Исцеление: +8 ВЫН (не в бою).")
    rows = [[(f"{g.SPELLS[k]} ×{v}", f"spell:{k}")] for k, v in s["spells"].items() if v]
    rows.append([("📖 К параграфу", "show")])
    return text, kb(*rows)


def roll_view(s: dict) -> tuple[str, InlineKeyboardMarkup]:
    text = ("🎲 <b>Ваш герой</b>\n\n"
            f"МАСТЕРСТВО: <b>{s['skill']}</b>  (1 кубик + 6)\n"
            f"ВЫНОСЛИВОСТЬ: <b>{s['stamina']}</b>  (2 кубика + 12)\n"
            f"УДАЧА: <b>{s['luck']}</b>  (1 кубик + 6)\n\n"
            "С собой: меч, фляга с водой (2 глотка), заплечный мешок и 15 золотых.")
    return text, kb([("🎲 Перебросить", "reroll"), ("✅ В путь", "accept")])


def spells_view(s: dict) -> tuple[str, InlineKeyboardMarkup]:
    total = g.spells_total(s)
    text = (f"✨ <b>Выберите заклятия</b> — всего не больше {g.SPELL_LIMIT} (выбрано: {total}).\n"
            "Каждое заклятие используется один раз. Одно и то же можно взять несколько раз.\n\n"
            "Левитация — перелететь препятствие. Огонь — огненный шар. Иллюзия — обмануть врага. "
            "Сила — усилить удар. Слабость — ослабить врага. Копия — двойник врага сражается с ним. "
            "Исцеление — +8 ВЫНОСЛИВОСТИ. Плавание — переплыть водную преграду.")
    rows = [[("−", f"sp:{k}:-1"), (f"{name}: {s['spells'][k]}", "noop"), ("+", f"sp:{k}:1")]
            for k, name in g.SPELLS.items()]
    rows.append([("✅ Готово, начать путь", "spdone")])
    return text, kb(*rows)


INTRO_KB = kb([("📖 Предисловие", "preface"), ("📏 Правила", "rules")], [("🎲 Создать героя", "create")])


# ================================================================ отправка
async def send(target: Message, view: tuple[str, InlineKeyboardMarkup]):
    text, markup = view
    parts = chunks(text)
    for part in parts[:-1]:
        await target.answer(part)
    await target.answer(parts[-1], reply_markup=markup)


async def edit(cb: CallbackQuery, view: tuple[str, InlineKeyboardMarkup]):
    text, markup = view
    try:
        await cb.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as e:
        if "not modified" not in str(e):
            await send(cb.message, view)


async def drop_keyboard(cb: CallbackQuery):
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass


def current_view(s: dict):
    if s["stage"] == "roll":
        return roll_view(s)
    if s["stage"] == "spells":
        return spells_view(s)
    if s["battle"] and g.alive(s["battle"]):
        return battle_view(s)
    return para_view(s)


# ================================================================ команды
@router.message(CommandStart())
async def cmd_start(m: Message):
    s = DB.load(m.from_user.id)
    if s and s["stage"] in ("play", "roll", "spells"):
        await m.answer("С возвращением! Продолжаем с того места, где вы остановились.")
        await send(m, current_view(s))
        return
    await m.answer(
        f"🏰 <b>{BOOK.title}</b>\nКнига-игра Дмитрия Браславского (1991).\n\n"
        "Здесь вы — главный герой. Кубики бросает бот, листок путешественника он ведёт сам, "
        "а все решения принимаете вы.\n\n"
        "Команды: /sheet — листок, /bag — мешок, /go N — перейти к параграфу N, "
        "/note текст — заметка, /new — начать заново, /help — справка.",
        reply_markup=INTRO_KB,
    )


@router.message(Command("help"))
async def cmd_help(m: Message):
    await m.answer(
        "<b>Как играть</b>\n"
        "• Жмите кнопки переходов под параграфом. Кнопки со ✨ сами тратят нужное заклятие.\n"
        "• «Применить −2 ВЫН» и т.п. — бот нашёл в тексте изменение характеристик; нажмите, если оно касается вас.\n"
        "• «⚔️ Начать бой» — бой по правилам книги. Если текст даёт бонус (например, +2 к СИЛЕ УДАРА), "
        "выставьте его кнопками ±1.\n"
        "• В книге есть скрытые ходы: «прибавьте 60 к номеру параграфа…». Запишите подсказку через /note, "
        "а когда придёт время — посчитайте и наберите /go N.\n"
        "• Листок (/sheet) позволяет поправить любую характеристику вручную.\n"
        "• /rules — правила книги, /new — новая игра."
    )


@router.message(Command("rules"))
async def cmd_rules(m: Message):
    for part in chunks(html.escape(BOOK.rules)):
        await m.answer(part)


@router.message(Command("new"))
async def cmd_new(m: Message):
    await m.answer("Начать новую игру? Текущий герой будет потерян.",
                   reply_markup=kb([("Да, заново", "new!"), ("Нет", "show")]))


async def with_game(m: Message) -> dict | None:
    s = DB.load(m.from_user.id)
    if not s or s["stage"] in ("roll", "spells"):
        await m.answer("Сначала создайте героя: /start")
        return None
    return s


@router.message(Command("sheet"))
async def cmd_sheet(m: Message):
    if s := await with_game(m):
        await send(m, sheet_view(s))


@router.message(Command("bag"))
async def cmd_bag(m: Message):
    if s := await with_game(m):
        await send(m, bag_view(s))


@router.message(Command("para"))
async def cmd_para(m: Message):
    if s := await with_game(m):
        await send(m, current_view(s))


@router.message(Command("go"))
async def cmd_go(m: Message, command: CommandObject):
    if not (s := await with_game(m)):
        return
    if not command.args or not command.args.strip().isdigit():
        await m.answer("Напишите номер: /go 123")
        return
    await do_go(m, s, int(command.args.strip()))


@router.message(Command("note"))
async def cmd_note(m: Message, command: CommandObject):
    if not (s := await with_game(m)):
        return
    if not command.args:
        await m.answer("Напишите заметку после команды: /note латы: +60 к номеру двери")
        return
    s["notes"].append(f"§{s['para']}: {command.args.strip()[:200]}")
    DB.save(m.from_user.id, s)
    await m.answer("📝 Записано.")


async def do_go(m: Message, s: dict, n: int):
    try:
        g.go(s, BOOK, n)
    except g.GameError as e:
        await m.answer(str(e))
        return
    DB.save(m.from_user.id, s)
    await send(m, para_view(s))


@router.message(F.text & ~F.text.startswith("/"))
async def free_text(m: Message):
    s = DB.load(m.from_user.id)
    if not s or not s.get("awaiting"):
        await m.answer("Используйте кнопки под параграфом. /para — показать текущий параграф, /help — справка.")
        return
    what, s["awaiting"] = s["awaiting"], None
    text = m.text.strip()
    try:
        if what == "item":
            g.add_item(s, text)
            DB.save(m.from_user.id, s)
            await send(m, bag_view(s))
        elif what == "note":
            s["notes"].append(f"§{s['para']}: {text[:200]}")
            DB.save(m.from_user.id, s)
            await m.answer("📝 Записано.")
        elif what == "go":
            DB.save(m.from_user.id, s)
            if not text.isdigit():
                await m.answer("Нужен номер параграфа, например 214.")
                return
            await do_go(m, s, int(text))
    except g.GameError as e:
        DB.save(m.from_user.id, s)
        await m.answer(str(e))


# ================================================================ кнопки
@router.callback_query()
async def on_button(cb: CallbackQuery):
    uid = cb.from_user.id
    data = cb.data or ""
    s = DB.load(uid)
    try:
        note = await handle(cb, s, data)
        await cb.answer(note or "")
    except g.GameError as e:
        await cb.answer(str(e), show_alert=True)


async def handle(cb: CallbackQuery, s: dict | None, data: str) -> str | None:
    uid = cb.from_user.id
    msg = cb.message

    # --- вступление и создание героя
    if data == "preface":
        for part in chunks(html.escape(BOOK.preface)):
            await msg.answer(part)
        await msg.answer("Готовы?", reply_markup=kb([("🎲 Создать героя", "create")]))
        return None
    if data == "rules":
        for part in chunks(html.escape(BOOK.rules)):
            await msg.answer(part)
        await msg.answer("Бот сам бросает кубики и ведёт листок.", reply_markup=kb([("🎲 Создать героя", "create")]))
        return None
    if data in ("create", "new!"):
        s = g.new_state(RNG)
        DB.save(uid, s)
        await drop_keyboard(cb)
        await send(msg, roll_view(s))
        return None
    if s is None:
        raise g.GameError("Игра не найдена. Нажмите /start.")
    if data == "noop":
        return None
    if data == "reroll":
        if s["stage"] != "roll":
            raise g.GameError("Герой уже в пути.")
        g.roll_stats(s, RNG)
        DB.save(uid, s)
        await edit(cb, roll_view(s))
        return None
    if data == "accept":
        if s["stage"] != "roll":
            raise g.GameError("Герой уже в пути.")
        s["stage"] = "spells"
        DB.save(uid, s)
        await edit(cb, spells_view(s))
        return None
    if data.startswith("sp:"):
        _, key, d = data.split(":")
        g.change_spell(s, key, int(d))
        DB.save(uid, s)
        await edit(cb, spells_view(s))
        return None
    if data == "spdone":
        if s["stage"] != "spells":
            raise g.GameError("Заклятия уже выбраны.")
        if g.spells_total(s) == 0:
            raise g.GameError("Выберите хотя бы одно заклятие.")
        g.go(s, BOOK, 1)
        DB.save(uid, s)
        await drop_keyboard(cb)
        await msg.answer("Майлин научил вас заклятиям. Герольд прощается с вами у границы Зачарованного леса…")
        await send(msg, para_view(s))
        return None
    if s["stage"] in ("roll", "spells"):
        await send(msg, current_view(s))
        return None

    # --- навигация
    if data == "show":
        await drop_keyboard(cb)
        await send(msg, para_view(s))
        return None
    if data.startswith("L:"):
        _, para, idx = data.split(":")
        if int(para) != s["para"]:
            raise g.GameError("Это кнопка из старого параграфа.")
        used = g.follow_link(s, BOOK, int(idx))
        DB.save(uid, s)
        await drop_keyboard(cb)
        await send(msg, para_view(s))
        return f"Потрачено заклятие {used}" if used else None
    if data.startswith("E:"):
        _, para, idx = data.split(":")
        if int(para) != s["para"]:
            raise g.GameError("Это кнопка из старого параграфа.")
        res = g.apply_effect(s, BOOK, int(idx))
        DB.save(uid, s)
        await edit(cb, para_view(s))
        return res
    if data == "luck":
        roll, lucky = g.test_luck(s, RNG)
        DB.save(uid, s)
        if not roll:
            await msg.answer("🍀 УДАЧА равна нулю — вы неудачливы.")
        else:
            await msg.answer(
                f"🍀 Проверка удачи: {g.fmt_roll(roll)} = {sum(roll)} против УДАЧИ {s['luck'] + 1}.\n"
                + ("<b>Вы удачливы!</b>" if lucky else "<b>Вам не повезло.</b>")
                + f"\nУДАЧА теперь {s['luck']}. Выберите переход в параграфе выше.")
        return None

    # --- бой
    if data == "fight":
        if not s["battle"]:
            g.start_battle(s, BOOK)
            DB.save(uid, s)
        await send(msg, battle_view(s))
        return None
    if data.startswith("B:"):
        if not s["battle"]:
            raise g.GameError("Бой уже закончен.")
        parts = data.split(":")
        b = s["battle"]
        if parts[1] == "hit":
            g.battle_round(s, int(parts[2]), RNG)
        elif parts[1] == "mods":
            b["mods_open"] = not b.get("mods_open")
        elif parts[1] == "mod":
            if parts[2] == "me":
                b["my_mod"] += int(parts[3])
            else:
                b["enemies"][int(parts[2])]["mod"] += int(parts[3])
        elif parts[1] == "copy":
            g.copy_fight(s, int(parts[2]), RNG)
        elif parts[1] == "flee":
            res = g.flee(s)
            DB.save(uid, s)
            await drop_keyboard(cb)
            await msg.answer(res)
            await send(msg, para_view(s))
            return None
        elif parts[1] == "exit":
            s["battle"] = None
            DB.save(uid, s)
            await drop_keyboard(cb)
            await send(msg, para_view(s))
            return "Бой прерван без штрафа"
        DB.save(uid, s)
        await edit(cb, battle_view(s))
        return None

    # --- листок, мешок, магия
    if data == "sheet":
        await send(msg, sheet_view(s))
        return None
    if data == "bag":
        await send(msg, bag_view(s))
        return None
    if data == "magic":
        await send(msg, magic_view(s))
        return None
    if data.startswith("S:"):
        _, stat, d = data.split(":")
        res = g.change_stat(s, stat, int(d))
        DB.save(uid, s)
        await edit(cb, sheet_view(s))
        if s["stage"] == "dead":
            await send(msg, para_view(s))
        return res
    if data == "drink":
        res = g.drink(s)
        DB.save(uid, s)
        await edit(cb, sheet_view(s))
        return res
    if data == "clearnotes":
        s["notes"] = []
        DB.save(uid, s)
        await edit(cb, sheet_view(s))
        return "Заметки стёрты"
    if data.startswith("drop:"):
        name = g.drop_item(s, int(data[5:]))
        DB.save(uid, s)
        await edit(cb, bag_view(s))
        return f"Выложено: {name}"
    if data.startswith("spell:"):
        res = g.spend_spell(s, data[6:])
        DB.save(uid, s)
        await edit(cb, magic_view(s))
        return res
    if data.startswith("ask:"):
        s["awaiting"] = data[4:]
        DB.save(uid, s)
        prompts = {"item": "Напишите название предмета:", "note": "Напишите заметку:",
                   "go": "Напишите номер параграфа:"}
        await msg.answer(prompts[s["awaiting"]])
        return None
    return None


async def main():
    logging.basicConfig(level=logging.INFO)
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise SystemExit("Укажите BOT_TOKEN в переменных окружения или в файле .env")
    bot = Bot(token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
