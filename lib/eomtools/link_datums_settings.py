# -*- coding: utf-8 -*-
"""
Настройки кнопки «Координация с КФП»: выбор связи, допуск сравнения
и маркер уровней КР.

Хранится в %APPDATA%\\pyRevit\\EOMLinkDatums_settings.json через
settings_core.TextSettings (Shift+клик по кнопке).
"""

from eomtools import settings_core
from eomtools.link_datums_core import DEFAULT_EXCLUDED_LEVEL_MARKER


SETTINGS = settings_core.TextSettings(
    file_name="EOMLinkDatums_settings.json",
    button_name=u"Координация с КФП",
    heading=u"Координация с КФП",
    transfer_label=u"сравнения осей и уровней",
    fields=[
        settings_core.TextField(
            "link_name_filter",
            u"① Связь КФП",
            u"Часть имени связи",
            hint=(
                u"Связь, в имени которой есть это слово, берётся сразу (регистр "
                u"не важен). Нашлось несколько или ни одной — кнопка предложит "
                u"выбрать из списка. Пусто — всегда спрашивать."
            ),
            default=u"КФП",
        ),
        settings_core.NumberField(
            "tolerance_mm",
            u"② Допуск",
            u"Допуск, мм",
            hint=(
                u"Расхождение отметки уровня или положения оси не больше "
                u"допуска считается совпадением."
            ),
            default=1.0, minimum=0.0,
        ),
        settings_core.TextField(
            "excluded_level_marker",
            u"③ Исключение уровней",
            u"Маркер в имени уровня связи",
            hint=(u"Уровни КФП с этим маркером пропускаются. Например, «КР» "
                  u"подходит для «КР_Этаж 01» и «КР01». Пусто — проверять все уровни."),
            default=DEFAULT_EXCLUDED_LEVEL_MARKER,
        ),
    ],
    width=640, height=480,
)

load_saved_values = SETTINGS.load_saved_values
save_values = SETTINGS.save_values
show_settings_form = SETTINGS.show_settings_form
get_settings_interactive = SETTINGS.get_settings_interactive
get_settings_silent = SETTINGS.get_settings_silent
