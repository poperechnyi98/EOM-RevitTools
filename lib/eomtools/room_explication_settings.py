# -*- coding: utf-8 -*-
"""
Настройки кнопок «Экспликация фрагмента» / «Обновить экспликацию»
(ToolsRooms.panel): название таблицы, заголовки и ширины граф, высота строк,
параметры помещения (номер, имя, площадь, категория), сортировка/группировка
(до 4 уровней, как в спецификации Revit), знаки площади.

Хранятся в %APPDATA%\\pyRevit\\EOMRoomExplication_settings.json через
settings_core.TextSettings (Shift+клик по кнопке). Умолчания заголовков и
ширин — из room_explication_core.COLUMNS (форма 2 ГОСТ 21.501).
"""

from eomtools import settings_core
from eomtools.i18n import N_
from eomtools.room_explication_core import (
    COLUMNS, HEADER_HEIGHT_MM, KIND_HEADER, KIND_KEY, OLD_DEFAULT_WIDTHS,
    ROW_HEIGHT_MM, SORT_LEVELS, TITLE)

ORDER_ASC = "asc"
ORDER_DESC = "desc"

_LABELS = {
    "number": N_(u"Столбец «Номер помещения»"),
    "name": N_(u"Столбец «Наименование»"),
    "area": N_(u"Столбец «Площадь»"),
    "category": N_(u"Столбец «Категория»"),
}


def _column_fields():
    fields = []
    for i, (key, heading, width) in enumerate(COLUMNS):
        section = u"{} {}".format(u"①②③④"[i], _LABELS[key])
        fields.append(settings_core.TextField(
            "header_" + key, section, u"Заголовок",
            hint=N_(u"Текст в шапке таблицы. Пусто — заголовок по умолчанию."),
            default=heading, required=False,
        ))
        fields.append(settings_core.NumberField(
            "width_" + key, u"", u"Ширина, мм",
            hint=N_(u"Ширина графы на листе. «Сбросить» вернёт ширину по умолчанию."),
            default=width, minimum=3.0,
        ))
    return fields


def _sort_fields():
    """Как «Сортировка/группировка» спецификации Revit: «Сортировать по» и
    до трёх «Затем по» — параметр, порядок, заголовок группы."""
    fields = []
    for n in range(1, SORT_LEVELS + 1):
        first = n == 1
        fields.append(settings_core.TextField(
            "room_sort{}_param".format(n),
            N_(u"⑥ Сортировка/группировка") if first else u"",
            N_(u"Сортировать по") if first else N_(u"Затем по"),
            hint=(N_(u"Имя параметра помещения. Пусто — уровень не используется; "
                  u"если пусты все — по номеру помещения. Последним ключом "
                  u"всегда идёт номер помещения по возрастанию.")
                  if first else u""),
            default=u"", required=False,
        ))
        fields.append(settings_core.ChoiceField(
            "room_sort{}_order".format(n), u"", u"",
            [(ORDER_ASC, u"По возрастанию"), (ORDER_DESC, u"По убыванию")],
            default=ORDER_ASC,
        ))
        fields.append(settings_core.ChoiceField(
            "room_sort{}_header".format(n), u"", u"",
            [("no", u"Без заголовка"), ("yes", u"Заголовок группы")],
            hint=(N_(u"Заголовок группы — строка на всю ширину таблицы перед "
                  u"помещениями с новым значением параметра (пустое значение — "
                  u"«Без группы», такие помещения — в конце).")
                  if first else u""),
            default="no",
        ))
    return fields


def _migrate(saved, values):
    """Ширины, сохранённые со старыми умолчаниями (60/15), — на форму ГОСТ.
    Прежние «Группировать по» / «Сортировать внутри группы» — в уровни 1/2."""
    old = all(_num(saved.get("width_" + k)) == w for k, w in OLD_DEFAULT_WIDTHS.items())
    if old:
        for key, _heading, width in COLUMNS:
            if key in OLD_DEFAULT_WIDTHS:
                values["width_" + key] = width
    if not any((saved.get("room_sort{}_param".format(n)) or u"").strip()
               for n in range(1, SORT_LEVELS + 1)):
        group = (saved.get("room_group_param") or u"").strip()
        inner = (saved.get("room_sort_param") or u"").strip()
        level = 1
        if group:
            values["room_sort1_param"] = group
            values["room_sort1_header"] = "yes"
            level = 2
        if inner:
            values["room_sort{}_param".format(level)] = inner


def _num(value):
    try:
        return float(value)
    except Exception:
        return None


def sort_levels(settings):
    """Заполненные уровни сортировки по порядку: [(имя параметра, по
    убыванию, показывать заголовок)]."""
    levels = []
    for n in range(1, SORT_LEVELS + 1):
        name = (settings.get("room_sort{}_param".format(n)) or u"").strip()
        if name:
            levels.append((name,
                           settings.get("room_sort{}_order".format(n)) == ORDER_DESC,
                           settings.get("room_sort{}_header".format(n)) == "yes"))
    return levels


# Ключ настройки на каждое поле помещения. Номер — room_number_param, как у
# других кнопок, читающих номер помещения (поиск помещения, двери СКУД).
ROOM_PARAM_KEYS = {
    "number": "room_number_param",
    "name": "name_param",
    "area": "area_param",
    "category": "category_param",
}

RENAMES = {"number_param": "room_number_param"}


def room_params(settings):
    """{"number", "name", "area", "category": имя параметра или "",
    "levels": [имена параметров уровней сортировки]}."""
    params = dict((key, (settings.get(ROOM_PARAM_KEYS[key]) or u"").strip())
                  for key in ("number", "name", "area", "category"))
    params["levels"] = [name for name, _d, _h in sort_levels(settings)]
    return params


def kind(settings):
    """Вид новой экспликации: KIND_KEY или KIND_HEADER."""
    return KIND_KEY if settings.get("kind") == KIND_KEY else KIND_HEADER


def title(settings):
    return (settings.get("title") or u"").strip() or TITLE


def heights(settings):
    """(высота шапки, высота строки) в мм."""
    return (float(settings.get("header_height") or HEADER_HEIGHT_MM),
            float(settings.get("row_height") or ROW_HEIGHT_MM))


SETTINGS = settings_core.TextSettings(
    file_name="EOMRoomExplication_settings.json",
    button_name=u"Экспликация фрагмента",
    heading=u"Экспликация фрагмента: столбцы таблицы",
    transfer_label=u"экспликации фрагмента",
    fields=[
        settings_core.ChoiceField(
            "kind", u"Вид таблицы", u"",
            [(KIND_HEADER, u"Ручная спецификация"),
             (KIND_KEY, u"Ключевая спецификация")],
            hint=(
                u"Ручная спецификация — свободная таблица, текст которой лежит в "
                u"шапке спецификации: в модели не появляется никаких параметров, "
                u"высота строк задаётся точно; "
                u"на листе её нельзя разделить на части.\n"
                u"Ключевая спецификация — делится на части на листе. Но столбцы "
                u"ключевой спецификации в Revit — всегда параметры: кнопка "
                u"заведёт помещениям 4 текстовых параметра LL_Экспликация_*, а "
                u"Revit добавит помещениям ключевой параметр на каждую такую "
                u"экспликацию. Высоту строк помещений Revit может не принять — "
                u"тогда она идёт от размера текста.\n"
                u"Действует на новые экспликации; чтобы сменить вид у готовой, "
                u"удалите её и создайте заново."
            ),
            default=KIND_HEADER,
            legacy={1: KIND_HEADER, 2: KIND_KEY},
        ),
        settings_core.TextField(
            "title", u"Таблица", u"Название над таблицей",
            hint=N_(u"Пусто — «Экспликация помещений»."),
            default=TITLE, required=False,
        ),
    ] + _column_fields() + [
        settings_core.TextField(
            "room_number_param", u"⑤ Параметры помещения (в модели или связи АР)",
            u"Номер помещения",
            hint=u"Имя параметра с номером. Пусто — встроенный «Номер».",
            default=u"", required=False,
        ),
        settings_core.TextField(
            "name_param", u"", u"Наименование",
            hint=u"Имя параметра с наименованием. Пусто — встроенное «Имя».",
            default=u"", required=False,
        ),
        settings_core.TextField(
            "area_param", u"", u"Площадь",
            hint=(u"Имя параметра с площадью. Пусто — встроенная «Площадь». "
                  u"Параметр типа «Площадь» переводится в м², другое число "
                  u"берётся как есть, текст — как написан (число — с нужным "
                  u"числом знаков)."),
            default=u"", required=False,
        ),
        settings_core.TextField(
            "category_param", u"", u"Категория помещения",
            hint=(
                u"Имя параметра помещения с категорией по взрывопожарной и "
                u"пожарной опасности (в модели или связи АР). Пусто — графа "
                u"«Категория» остаётся незаполненной."
            ),
            default=u"", required=False,
        ),
    ] + _sort_fields() + [
        settings_core.NumberField(
            "area_decimals", u"⑦ Площадь", u"Знаков после запятой в площади",
            hint=u"По умолчанию 2 (12,35).",
            default=2, minimum=0, integer=True,
        ),
        settings_core.NumberField(
            "header_height", u"⑧ Высота строк", u"Шапка таблицы, мм",
            hint=u"По умолчанию 20 мм (форма 2 ГОСТ 21.501).",
            default=HEADER_HEIGHT_MM, minimum=3.0,
        ),
        settings_core.NumberField(
            "row_height", u"", u"Строка помещения, мм",
            hint=u"По умолчанию 8 мм (форма 2 ГОСТ 21.501). Заголовок группы — той же высоты.",
            default=ROW_HEIGHT_MM, minimum=3.0,
        ),
    ],
    migrate=_migrate,
    reset_button=True,
    width=760, height=920,
    renames=RENAMES,
)

load_saved_values = SETTINGS.load_saved_values
save_values = SETTINGS.save_values
get_settings_interactive = SETTINGS.get_settings_interactive
get_settings_silent = SETTINGS.get_settings_silent


def columns(settings):
    """[(ключ, заголовок, ширина мм)] с учётом настроек (пустой заголовок — умолчание)."""
    result = []
    for key, heading, width in COLUMNS:
        text = (settings.get("header_" + key) or u"").strip() or heading
        result.append((key, text, float(settings.get("width_" + key) or width)))
    return result
