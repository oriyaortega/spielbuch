"""
Сборка book.json из PDF «Подземелья Чёрного замка» (Д. Браславский, 1991).

    python build_book.py путь/к/книге.pdf [data/book.json]

Что делает:
  * расшифровывает текст (в PDF кириллица сдвинута на 0x228 в кастомном шрифте);
  * раскладывает развороты на левую/правую колонки и склеивает строки;
  * режет текст на 617 пронумерованных параграфов;
  * для каждого параграфа вытаскивает переходы (с подписями для кнопок),
    врагов (Мастерство/Выносливость), изменения характеристик, проверки удачи
    и признак концовки.

Результат — обычный JSON, его можно поправить руками, если парсер где-то ошибся.
"""
import json
import re
import sys
from pathlib import Path

import pymupdf

TOTAL = 617
SHIFT = 0x228
SPELLS = {
    "Левитации": "levitation",
    "Огня": "fire",
    "Иллюзии": "illusion",
    "Силы": "strength",
    "Слабости": "weakness",
    "Копии": "copy",
    "Исцеления": "healing",
    "Плавания": "swimming",
}


# ---------------------------------------------------------------- извлечение
def decode(s: str) -> str:
    return "".join(chr(ord(c) + SHIFT) if 0x1E8 <= ord(c) <= 0x227 else c for c in s)


def pdf_lines(path: str) -> list[str]:
    doc = pymupdf.open(path)
    out: list[str] = []
    for page in doc:
        mid = page.rect.width / 2
        words = page.get_text("words")
        for right in (False, True):
            col = [w for w in words if ((w[0] + w[2]) / 2 >= mid) == right]
            rows: list[list] = []
            for x0, y0, x1, y1, w, *_ in sorted(col, key=lambda t: ((t[1] + t[3]) / 2, t[0])):
                yc = (y0 + y1) / 2
                if rows and abs(rows[-1][0] - yc) < 3:
                    rows[-1][1].append((x0, w))
                else:
                    rows.append([yc, [(x0, w)]])
            for _, ws in rows:
                line = decode(" ".join(w for _, w in sorted(ws))).strip()
                if line and not line.startswith("1991 ©") and "База знаний" not in line:
                    out.append(line)
    return out


def join_lines(lines: list[str]) -> str:
    """Склеивает строки вёрстки в абзацы."""
    text = ""
    prev = ""
    for line in lines:
        if not text:
            text = line
        elif prev.endswith("-") and not prev.endswith(" -"):
            text += line  # перенос: «куда-» + «то»
        elif len(prev) < 50 and re.search(r"[.!?:…»)]$", prev):
            text += "\n" + line
        elif (is_caps(line) or is_caps(prev) or STAT_RE.fullmatch(line)
              or STAT_RE.fullmatch(prev) or re.search(r"[-–]\s*\d{1,3}[.,]$", prev)):
            text += "\n" + line
        else:
            text += " " + line
        prev = line
    return fix_text(text)


def is_caps(line: str) -> bool:
    letters = [c for c in line if c.isalpha()]
    return len(letters) >= 3 and all(c.isupper() for c in letters)


PARTICLE_RE = re.compile(
    r"\b(кто|что|где|куда|когда|как|какой|какая|какое|какие|каким|какую|какого|"
    r"чей|чего|чем|кого|кому|почему|зачем|откуда|сколько|чьи|чья|чьё|чье|"
    r"Кто|Что|Где|Куда|Когда|Как|Какой|Какая|Какие|Почему|Зачем|Откуда|Сколько) "
    r"(то|нибудь|либо)\b"
)


def fix_text(t: str) -> str:
    t = t.replace("©", "«")  # открывающая кавычка в шрифте книги
    t = PARTICLE_RE.sub(r"\1-\2", t)
    t = re.sub(r"\b([Ии]з) (за|под)\b", r"\1-\2", t)
    t = re.sub(r"\b(Теперь|теперь|Вот|вот|Уж|уж) то\b(?= [а-яё])", r"\1-то", t)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r" ([,.;:!?])", r"\1", t)
    return t.strip()


# ---------------------------------------------------------------- разбор
STAT_RE = re.compile(r"Мастерство\s+(\d+)\s+Выносливость\s+(\d+)")
LINK_PATTERNS = [
    re.compile(r"\((\d{1,3})\)"),                                   # (123)
    re.compile(r"(?:\s|^)[-–—]\s*(\d{1,3})(?=[\s.,;!?)]|$)"),       # - 123
    re.compile(r"\b(?:на|то|к)\s+(\d{1,3})(?=[\s.,;!?)]|$)(?!\s*(?:золот|раз|ВЫНОС|МАСТЕР|УДАЧ|мин|час|шаг|дн|мест|предмет|к\b))"),
    re.compile(r"параграф\w*\s+(\d{1,3})\b"),
]
EFFECT_RE = re.compile(
    r"(потеряйте|отнимите|вычтите|уменьшите|прибавьте|добавьте|восстановите|верните|"
    r"заплатите|отдайте|потратьте|возьмите)\s+(?:себе\s+|ему\s+|им\s+)?(\d{1,2})\s+"
    r"(ВЫНОСЛИВОСТ\w*|МАСТЕРСТВ\w*|УДАЧ\w*|золот\w*)",
    re.IGNORECASE,
)
NEG = {"потеряйте", "отнимите", "вычтите", "уменьшите", "заплатите", "отдайте", "потратьте"}
STAT_KEY = {"вынослив": "stamina", "мастерств": "skill", "удач": "luck", "золот": "gold"}
SHORT_STAT = {"stamina": "ВЫН", "skill": "МАС", "luck": "УДАЧА", "gold": "золото"}


def find_links(text: str) -> list[dict]:
    found: dict[int, dict] = {}
    for pat in LINK_PATTERNS:
        for m in pat.finditer(text):
            n = int(m.group(1))
            if not 1 <= n <= TOTAL:
                continue
            # число перед словом-счётчиком — не ссылка («прибавьте 60 к номеру»)
            after = text[m.end():m.end() + 12]
            before = text[max(0, m.start() - 14):m.start()].lower()
            if re.match(r"\s*(к номеру|из номера|к своей|золот|раз\b|ВЫНОС|МАСТЕР|УДАЧ|СИЛ)", after):
                continue
            if re.search(r"(прибавьте|вычтите|прибавить|вычесть|добавьте)\s*$", before):
                continue
            pos = m.start()
            if n not in found or pos < found[n]["pos"]:
                found[n] = {"to": n, "pos": pos, "label": make_label(text, pos)}
    links = sorted(found.values(), key=lambda l: l["pos"])
    for l in links:
        l["spell"] = spell_of(l["label"])
        del l["pos"]
    return links


def make_label(text: str, pos: int) -> str:
    head = text[:pos].rstrip(" -–—?(")
    strong = max(head.rfind(ch) for ch in ".!?;:\n()")
    weak = max(strong, head.rfind(","))
    seg = head[weak + 1:]
    if len(re.sub(r"^\s*(а|и|или|либо|то|тогда)\b", "", seg).strip()) < 4:
        seg = head[strong + 1:]
        if "," in seg and len(seg.split(",")[0]) > 3:
            seg = seg.split(",")[0]
    for sep in (" или ", " либо "):
        i = seg.rfind(sep)
        if i >= 0 and len(seg) - i > 4:
            seg = seg[i + len(sep):]
    seg = seg.strip(" -–—?)")
    seg = re.sub(r"^(а|и|или|либо|то|тогда)\s+", "", seg, flags=re.I).strip()
    if seg.lower() in ("", "то", "на", "к") or len(seg) < 3:
        seg = "Дальше"
    if len(seg) > 40:
        seg = "…" + seg[-39:].split(" ", 1)[-1]
    return seg[:1].upper() + seg[1:]


def spell_of(label: str) -> str | None:
    for word, key in SPELLS.items():
        if re.search(rf"\b{word}\b", label):
            return key
    return None


def find_enemies(text: str) -> list[dict]:
    enemies = []
    lines = text.split("\n")
    for i, line in enumerate(lines):
        for m in STAT_RE.finditer(line):
            name = line[: m.start()].strip()
            if not name and i > 0 and is_caps(lines[i - 1]):
                name = lines[i - 1].strip()
            name = name.capitalize() if name else f"Враг {len(enemies) + 1}"
            enemies.append({"name": name, "skill": int(m.group(1)), "stamina": int(m.group(2))})
    return enemies


LOSS_RE = re.compile(
    r"(теряете|потеряете|лишаетесь)\b[^.]{0,50}?\(?и?\s(\d{1,2})\s+"
    r"(ВЫНОСЛИВОСТ\w*|МАСТЕРСТВ\w*|УДАЧ\w*)",
)


def find_effects(text: str) -> list[dict]:
    effects = []
    matches = list(EFFECT_RE.finditer(text))
    for m in LOSS_RE.finditer(text):
        if not any(m.start() <= x.start() < m.end() for x in matches):
            matches.append(m)
    for m in sorted(matches, key=lambda m: m.start()):
        verb, num, stat = m.group(1).lower(), int(m.group(2)), m.group(3).lower()
        if verb in ("теряете", "потеряете", "лишаетесь"):
            verb = "потеряйте"
        key = next(v for k, v in STAT_KEY.items() if stat.startswith(k))
        if verb == "возьмите" and key != "gold":
            continue
        sign = -1 if verb in NEG else 1
        # «вычтите 2 из ВЫНОСЛИВОСТИ врага» — эффект на врага, пропускаем
        tail = text[m.end():m.end() + 25].lower()
        if re.match(r"\s*(у|из)\s+(него|врага|противника|дракона)", tail):
            continue
        sentence_end = text.find(".", m.end())
        sentence = text[m.start(): sentence_end if sentence_end > 0 else None]
        delta = sign * num
        effects.append({
            "stat": key,
            "delta": delta,
            "nocap": "даже если" in sentence,
            "label": f"{'+' if delta > 0 else '−'}{abs(delta)} {SHORT_STAT[key]}",
        })
    return effects


def build(pdf: str) -> dict:
    lines = pdf_lines(pdf)
    start = next(i for i, l in enumerate(lines) if "перевернит" in l)
    front, body = lines[: start + 1], lines[start + 1:]

    raw: dict[int, list[str]] = {}
    cur, expected = None, 1
    for l in body:
        if re.fullmatch(r"\d{1,3}", l) and int(l) == expected:
            cur, expected = expected, expected + 1
            raw[cur] = []
        elif re.fullmatch(r"\(\d{1,3}\)", l):
            continue  # подпись к иллюстрации, не переход
        elif cur is not None:
            raw[cur].append(l)
    if len(raw) != TOTAL:
        print(f"ВНИМАНИЕ: найдено {len(raw)} параграфов вместо {TOTAL}", file=sys.stderr)

    # вступление: правила + предисловие
    ftext = join_lines(front)
    rules_at = ftext.find("ПРАВИЛА ИГРЫ")
    pref_at = ftext.find("ПРЕДИСЛОВИЕ")
    rules = ftext[rules_at:pref_at].strip() if rules_at >= 0 else ""
    preface = ftext[pref_at:].strip() if pref_at >= 0 else ftext

    paras = {}
    for n, ls in raw.items():
        text = join_lines(ls)
        links = find_links(text)
        ending = None
        if not links:
            ending = "victory" if n == TOTAL else "end"
        paras[str(n)] = {
            "text": text,
            "links": links,
            "enemies": find_enemies(text),
            "together": bool(re.search(r"одновременно", text)),
            "effects": find_effects(text),
            "luck": "ПРОВЕРЬТЕ СВОЮ УДАЧУ" in text.upper(),
            "ending": ending,
        }
    return {
        "title": "Подземелья Чёрного замка",
        "author": "Дмитрий Браславский",
        "rules": rules,
        "preface": preface,
        "paragraphs": paras,
    }


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    out = Path(sys.argv[2] if len(sys.argv) > 2 else "data/book.json")
    book = build(sys.argv[1])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(book, ensure_ascii=False, indent=1), encoding="utf-8")
    p = book["paragraphs"]
    print(f"Готово: {out} — параграфов {len(p)}, "
          f"с боями {sum(1 for x in p.values() if x['enemies'])}, "
          f"концовок {sum(1 for x in p.values() if x['ending'])}")


if __name__ == "__main__":
    main()
