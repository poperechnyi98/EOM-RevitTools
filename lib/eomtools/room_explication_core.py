# -*- coding: utf-8 -*-
"""Экспликация фрагмента — часть без Revit API (`ToolsRooms.panel/FragmentExplication`,
`UpdateExplication`).

Столбцы, их заголовки и ширины по умолчанию (форма 2 «Экспликация
помещений» ГОСТ 21.501), сортировка номеров «по-человечески», формат
площади, имена спецификации/ключевого параметра, ключевые имена строк. Покрыто
`tests/test_room_explication_core.py`; идёт и под Python 3.
"""

import re

MM_IN_FOOT = 304.8
SQ_FT_TO_SQ_M = 0.09290304

# (ключ столбца, заголовок по умолчанию, ширина по умолчанию, мм).
# Заголовки и размеры — форма 2 «Экспликация помещений» ГОСТ 21.501:
# графы 15/80/20/10 мм (итого 125), шапка 20 мм, строка 8 мм. Меняются в
# настройках кнопки.
COLUMNS = (
    ("number", u"Номер помещения", 15.0),
    ("name", u"Наименование", 80.0),
    ("area", u"Площадь, м²", 20.0),
    ("category", u"Кат. помещения", 10.0),
)
HEADER_HEIGHT_MM = 20.0
ROW_HEIGHT_MM = 8.0

# Умолчания ширин до сверки с формой ГОСТ — сохранённые с ними настройки
# переводятся на новые (room_explication_settings._migrate).
OLD_DEFAULT_WIDTHS = {"name": 60.0, "category": 15.0}

TITLE = u"Экспликация помещений"  # строка названия над таблицей

# Вид таблицы экспликации (настройка «Вид таблицы»).
KIND_HEADER = "header"  # «Ручная спецификация»: таблица текстом в шапке спецификации
KIND_KEY = "key"        # ключевая спецификация (room_explication_key)

# Символы, недопустимые в именах видов и параметров Revit.
_FORBIDDEN = u"\\:{}[]|;<>?`~"


def clean_name(text):
    """Убирает символы, которые Revit не принимает в именах видов/параметров."""
    text = u"".join(ch for ch in (text or u"") if ch not in _FORBIDDEN)
    return u" ".join(text.split())


def schedule_name(view_name):
    """Имя спецификации для фрагмента (занятое — дополняется unique_name)."""
    return clean_name(u"Экспликация - {}".format(view_name))


def key_param_name(schedule_name_text):
    """Имя ключевого параметра помещений, который Revit заводит под ключевую
    спецификацию (вид таблицы «ключевая спецификация»)."""
    return clean_name(u"Ключ - {}".format(schedule_name_text))


def key_names(count):
    """Ключевые имена строк ключевой спецификации: 001, 002, … — по ним сортировка."""
    width = max(3, len(str(count)))
    return [str(i + 1).zfill(width) for i in range(count)]


def unique_name(name, existing):
    """name, а если занято — «name (2)», «name (3)», …"""
    if name not in existing:
        return name
    i = 2
    while u"{} ({})".format(name, i) in existing:
        i += 1
    return u"{} ({})".format(name, i)


def natural_key(text):
    """Ключ сортировки: числа внутри строки сравниваются как числа (1.2 < 1.10)."""
    parts = re.split(r"(\d+)", (text or u"").strip().lower())
    return [(0, int(p), u"") if p.isdigit() else (1, 0, p) for p in parts if p != u""]


def format_area(sq_m, decimals=2, separator=u","):
    """Площадь в м² текстом: фиксированное число знаков, запятая по ГОСТ."""
    decimals = max(0, int(decimals))
    text = u"{:.{}f}".format(float(sq_m), decimals)
    return text.replace(u".", separator)


def area_text(value, decimals=2):
    """Площадь для таблицы: число (м²) — форматом ГОСТ; текст из своего
    параметра — тоже, если это число («12.5» -> «12,50»), иначе как есть."""
    if value is None:
        return u""
    if isinstance(value, (int, float)):
        return format_area(value, decimals)
    text = u"{}".format(value).strip()
    try:
        return format_area(float(text.replace(u",", u".").replace(u" ", u"")), decimals)
    except ValueError:
        return text


# Заголовок группы для помещений, у которых значение параметра пустое.
NO_GROUP = u"Без группы"

# Уровней сортировки/группировки: «Сортировать по» и до трёх «Затем по».
SORT_LEVELS = 4


def _row(r, decimals):
    return (
        (r.get("number") or u"").strip(),
        (r.get("name") or u"").strip(),
        area_text(r.get("area_m2"), decimals),
        (r.get("category") or u"").strip(),
    )


def _level_value(r, i):
    keys = r.get("keys") or []
    return (keys[i] if i < len(keys) else u"") or u""


def build_rows(rooms, decimals=2):
    """
    rooms — список dict {number, name, area_m2, category}; area_m2 — число
    в м² или текст из параметра площади (см. area_text). Возвращает строки
    таблицы (кортежи текстов в порядке COLUMNS) по номеру помещения;
    одинаковые строки (помещение пришло дважды) — один раз.
    """
    return table_rows(build_table(rooms, decimals))


def build_table(rooms, decimals=2, levels=None):
    """
    Таблица экспликации, как сортировка/группировка спецификации Revit:
    список ("group", (уровень, заголовок)) и ("row", строка); уровень —
    номер заголовка среди уровней с заголовком (0 — самый внешний).

    levels — [(по убыванию, показывать заголовок), ...] — уровни «Сортировать
    по», «Затем по»…; значения уровней — в r["keys"] (по порядку). Пустое
    значение уровня всегда идёт после заполненных (заголовок — NO_GROUP).
    Числа внутри значений сравниваются как числа. Последний ключ — номер
    помещения по возрастанию. Заголовок уровня с show_header ставится перед
    первой строкой каждой новой группы этого уровня (вложенные — по порядку).
    Одинаковые строки (помещение пришло дважды) — один раз.
    """
    levels = list(levels or [])
    unique = []
    seen = set()
    for r in rooms:
        key = (_row(r, decimals), tuple(_level_value(r, i) for i in range(len(levels))))
        if key in seen:
            continue
        seen.add(key)
        unique.append(r)

    # Устойчивая сортировка: от последнего ключа к первому.
    ordered = sorted(unique, key=lambda r: (natural_key(r.get("number")),
                                            natural_key(r.get("name"))))
    for i in range(len(levels) - 1, -1, -1):
        descending = levels[i][0]
        ordered = sorted(ordered, key=lambda r: natural_key(_level_value(r, i).strip()),
                         reverse=descending)
        ordered = sorted(ordered, key=lambda r: not _level_value(r, i).strip())

    table = []
    previous = None
    for r in ordered:
        values = [_level_value(r, i).strip() for i in range(len(levels))]
        depth = -1
        for i, (_descending, show_header) in enumerate(levels):
            if show_header:
                depth += 1
            if previous is not None and previous[:i + 1] == values[:i + 1]:
                continue
            if show_header:
                table.append(("group", (depth, values[i] or NO_GROUP)))
        previous = values
        table.append(("row", _row(r, decimals)))
    return table


def table_rows(table):
    """Только строки помещений из build_table."""
    return [item for kind, item in table if kind == "row"]


def in_rect(x, y, rect):
    xmin, ymin, xmax, ymax = rect
    return xmin <= x <= xmax and ymin <= y <= ymax
