# -*- coding: utf-8 -*-
"""
Настройки кнопки «Марки оборудования» + их хранение между запусками.

Хранится в JSON-файле %APPDATA%\\pyRevit\\EOMEquipmentTags_settings.json —
тот же подход, что room_info_settings.py/scs_settings.py (простой файл
вместо pyrevit.script.get_config(), см. их докстринги про причину).

Все расстояния — в миллиметрах НА ЛИСТЕ: при раскладке умножаются на
масштаб вида, поэтому на 1:50 и 1:100 марки выглядят одинаково.

Обвязка (хранение, окно с проверкой чисел, Shift+клик) — общая,
settings_core.TextSettings; здесь только описание полей. Битые/
отрицательные сохранённые значения заменяются значениями по умолчанию.
"""

from eomtools import settings_core


SETTINGS = settings_core.TextSettings(
    file_name="EOMEquipmentTags_settings.json",
    button_name=u"Марки оборудования",
    heading=u"Раскладка марок оборудования",
    transfer_label=u"марок оборудования",
    intro=(
        u"Все расстояния — в мм на листе, как на распечатке: при масштабе "
        u"вида 1:100 отступ 3 мм — это 300 мм в модели. "
        u"Значения сохраняются между запусками."
    ),
    fields=[
        settings_core.NumberField(
            "offset_mm",
            u"Отступ марок от оборудования, мм",
            u"",
            hint=(
                u"На каком расстоянии от габарита оборудования встаёт колонка/"
                u"стопка марок. Если места нет, кнопка сама пробует 2× и 3× отступ."
            ),
            default=3.0, minimum=0,
        ),
        settings_core.NumberField(
            "gap_mm",
            u"Зазор между марками, мм",
            u"",
            hint=u"Минимальный промежуток между соседними марками в колонке/стопке.",
            default=1.0, minimum=0,
        ),
        settings_core.NumberField(
            "shelf_mm",
            u"Длина полки выноски, мм",
            u"",
            hint=(
                u"Короткий горизонтальный отрезок линии-выноски у самой марки "
                u"(«полочка»), от которого наклонная часть идёт к оборудованию."
            ),
            default=3.0, minimum=0,
        ),
        settings_core.NumberField(
            "cluster_mm",
            u"«Рядом» — если оборудование ближе, мм",
            u"",
            hint=(
                u"Оборудование, стоящее друг от друга ближе этого расстояния, "
                u"получает марки одним блоком: рядом по горизонтали — стопкой "
                u"друг над другом, одно над другим — колонкой сбоку на одном "
                u"отступе."
            ),
            default=8.0, minimum=0,
        ),
    ],
    width=620, height=520,
)

load_settings = SETTINGS.load_saved_values
show_settings_form = SETTINGS.show_settings_form
get_settings_interactive = SETTINGS.get_settings_interactive
