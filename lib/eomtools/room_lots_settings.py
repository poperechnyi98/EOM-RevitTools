# -*- coding: utf-8 -*-
"""
Окно настроек параметров для кнопки «Двухуровневые лоты» (ToolsRooms.panel)
+ их хранение между запусками.

Хранится в JSON-файле %APPDATA%\\pyRevit\\EOMRoomLots_settings.json —
тот же подход, что room_finder_settings.py/scs_settings.py (простой файл
вместо pyrevit.script.get_config(), см. их докстринги про причину).

Имена параметров — соглашение конкретного проекта/ФОП, поэтому не зашиты
константой в коде, только через это окно.

Обвязка (хранение, окно, require, Shift+клик/тихий режим) — общая,
settings_core.TextSettings; здесь только описание полей.
"""

from eomtools import settings_core


SETTINGS = settings_core.TextSettings(
    file_name="EOMRoomLots_settings.json",
    button_name=u"Двухуровневые лоты",
    heading=u"Параметры имени лота и номера секции",
    transfer_label=u"двухуровневых лотов",
    fields=[
        settings_core.TextField(
            "lot_param_name",
            u"① Имя лота",
            u"Параметр помещения (в связи АР)",
            hint=(
                u"Имя параметра Room, в котором записано имя лота. Помещения с "
                u"одинаковым значением на разных уровнях считаются одним "
                u"двухуровневым лотом. По нему же сортируется список."
            ),
            default=u"", required=True,
        ),
        settings_core.TextField(
            "room_section_param",
            u"② Номер секции",
            u"Параметр помещения (в связи АР)",
            hint=(
                u"Имя параметра Room с номером секции — вторая ступень сортировки "
                u"после имени лота, показывается в отчёте. Пусто — секция не "
                u"читается."
            ),
            default=u"", required=False,
        ),
    ],
    width=720, height=440,
    renames={"section_param_name": "room_section_param"},
)

load_saved_values = SETTINGS.load_saved_values
save_values = SETTINGS.save_values
require = SETTINGS.require
show_settings_form = SETTINGS.show_settings_form
get_settings_interactive = SETTINGS.get_settings_interactive
get_settings_silent = SETTINGS.get_settings_silent
