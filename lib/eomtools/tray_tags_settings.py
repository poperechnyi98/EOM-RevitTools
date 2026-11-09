# -*- coding: utf-8 -*-
"""
Настройки кнопки «Марки лотков» (Tools.panel/TagTrays) + их хранение
между запусками (%APPDATA%\\pyRevit\\EOMTrayTags_settings.json).

Те же поля раскладки, что у «Марок оборудования»
(``equipment_tags_settings``), но свой файл — у лотков обычно другие
расстояния, — плюс допуск наклона: лотки круче него не маркируются.
Расстояния — в мм на листе (умножаются на масштаб вида).
"""

from eomtools import settings_core


SETTINGS = settings_core.TextSettings(
    file_name="EOMTrayTags_settings.json",
    button_name=u"Марки лотков",
    heading=u"Раскладка марок лотков",
    transfer_label=u"марок лотков",
    intro=(
        u"Расстояния — в мм на листе, как на распечатке: при масштабе "
        u"вида 1:100 отступ 3 мм — это 300 мм в модели. "
        u"Значения сохраняются между запусками."
    ),
    fields=[
        settings_core.NumberField(
            "offset_mm",
            u"Отступ марок от лотка, мм",
            u"",
            hint=(
                u"На каком расстоянии от края лотка встаёт колонка/стопка "
                u"марок. Если места нет, кнопка сама пробует 2× и 3× отступ."
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
            hint=u"Короткий горизонтальный отрезок линии-выноски у самой марки.",
            default=3.0, minimum=0,
        ),
        settings_core.NumberField(
            "cluster_mm",
            u"«Рядом» — если середины лотков ближе, мм",
            u"",
            hint=(
                u"Лотки, у которых места марок ближе этого расстояния (например, "
                u"параллельные трассы), получают марки одним блоком: стопкой "
                u"или колонкой на одном отступе."
            ),
            default=8.0, minimum=0,
        ),
        settings_core.NumberField(
            "max_slope_deg",
            u"Наклонный — если круче, градусов",
            u"",
            hint=(
                u"Лотки, ось которых наклонена к горизонтали больше этого угла "
                u"(в том числе вертикальные), не маркируются."
            ),
            default=0.5, minimum=0,
        ),
    ],
    width=620, height=600,
)

load_settings = SETTINGS.load_saved_values
get_settings_interactive = SETTINGS.get_settings_interactive


# выбранный тип марки (подпись «Семейство: Тип») — отдельным файлом, не
# полем окна настроек: выбирается списком при каждом «Поставить марки»
_TYPES = settings_core.JsonStore("EOMTrayTags_types.json", u"марок лотков")


def load_tag_type_label():
    value = _TYPES.read().get("tag_type")
    return value if value else u""


def save_tag_type_label(label):
    _TYPES.update({"tag_type": label or u""})
