# -*- coding: utf-8 -*-
"""
Выгрузка/загрузка настроек дисциплины в отдельный JSON-файл — чтобы
переносить настроенные параметры с проекта на проект (и между
компьютерами), не заполняя окно настроек заново.

Каждая дисциплина (СКС/СКУД/СПС/СОУЭ/СОТ/Помещения) хранит ВСЕ свои
настройки одним JSON-файлом в %APPDATA%\\pyRevit\\ (см. _settings_file_path
и _read_all/_write_all в соответствующем *_settings.py). Этот модуль
просто копирует такой файл наружу и обратно, поэтому переносится сразу
всё: и текстовые поля, и выбранные типы (по ElementId), и таблицы
категорий структурной схемы.

Важно про ElementId: id типов/шаблонов/строк справочника кабелей —
это id элементов КОНКРETНОГО проекта. На другом проекте те же семейства
почти наверняка имеют другие id, поэтому после загрузки настроек выбор
типов, скорее всего, придётся переназначить в окне. Имена параметров,
ключевые слова, форматы, коэффициенты — переносятся как есть, ради них
всё и затевалось.

Общий для всех окон настроек, добавляется в нижний ряд кнопок через
add_transfer_buttons().
"""

try:
    unicode  # noqa: F821 — IronPython/Python 2
except NameError:  # Python 3
    unicode = str


import io
from eomtools.i18n import tr
import json

import clr
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')

from pyrevit import forms

from System.Windows import Thickness
from System.Windows.Controls import Button


# show_settings_form возвращает эту строку вместо словаря значений, когда
# пользователь загрузил настройки из файла. Вызывающий get_settings_interactive
# должен в этом случае перечитать сохранённые значения и открыть окно заново
# (проще, чем перерисовывать все поля/пикеры уже открытого окна).
RELOAD = u"__lowlife_settings_reloaded__"


def _export(read_all, discipline_label):
    data = read_all()

    if not data:
        forms.alert(
            tr(u"Настройки {} ещё не заданы — выгружать нечего. Сначала "
            u"заполните параметры и нажмите «Сохранить».").format(tr(discipline_label))
        )
        return

    path = forms.save_file(
        file_ext="json",
        default_name=u"EOM_{}_settings".format(discipline_label),
    )
    if not path:
        return

    try:
        with io.open(path, "w", encoding="utf-8") as f:
            f.write(unicode(json.dumps(
                data, ensure_ascii=False, indent=2, sort_keys=True
            )))
    except Exception as exc:
        forms.alert(tr(u"Не удалось записать файл:\n{}\n\n{}").format(path, exc))
        return

    forms.alert(
        tr(u"Настройки {} ({} значений) выгружены в файл:\n{}\n\n"
        u"Файл можно передать на другой проект/компьютер и там нажать "
        u"«Загрузить настройки…».").format(tr(discipline_label), len(data), path)
    )


def _import(read_all, write_all, discipline_label, on_imported):
    path = forms.pick_file(file_ext="json")
    if not path:
        return

    try:
        with io.open(path, "r", encoding="utf-8") as f:
            text = f.read()
        imported = json.loads(text) if text.strip() else None
    except Exception as exc:
        forms.alert(
            tr(u"Не удалось прочитать файл настроек:\n{}\n\n{}").format(path, exc)
        )
        return

    if not isinstance(imported, dict) or not imported:
        forms.alert(
            tr(u"Файл не похож на файл настроек EOM-RevitTools (ожидался JSON-объект "
            u"с парами «параметр: значение»).")
        )
        return

    if not forms.alert(
        tr(u"Загрузить {} значений настроек {} из файла?\n{}\n\n"
        u"Текущие настройки будут перезаписаны загруженными (значения, "
        u"которых нет в файле, останутся). Выбор типов из проекта, скорее "
        u"всего, придётся переназначить — id элементов на другом проекте "
        u"другие.").format(len(imported), tr(discipline_label), path),
        yes=True, no=True,
    ):
        return

    data = read_all()
    data.update(imported)
    write_all(data)

    forms.alert(
        tr(u"Настройки {} загружены. Окно откроется заново с новыми "
        u"значениями.").format(tr(discipline_label))
    )

    on_imported()


def add_transfer_buttons(buttons_panel, read_all, write_all,
                         discipline_label, on_imported):
    """
    Добавляет в начало нижнего ряда кнопок окна настроек пару
    «Выгрузить настройки…» / «Загрузить настройки…».

    buttons_panel      — StackPanel нижнего ряда кнопок (Orientation.Horizontal).
    read_all / write_all — _read_all / _write_all соответствующего *_settings.py.
    discipline_label   — «СКС», «СКУД», … — для подписей, имён файлов и сообщений.
    on_imported        — вызывается после успешной загрузки; должен закрыть
                         окно так, чтобы show_settings_form вернул RELOAD.
    """
    export_btn = Button()
    export_btn.Content = tr(u"Выгрузить настройки…")
    export_btn.Padding = Thickness(10, 4, 10, 4)
    export_btn.Margin = Thickness(0, 0, 8, 0)
    export_btn.Click += lambda sender, args: _export(read_all, discipline_label)

    import_btn = Button()
    import_btn.Content = tr(u"Загрузить настройки…")
    import_btn.Padding = Thickness(10, 4, 10, 4)
    import_btn.Margin = Thickness(0, 0, 8, 0)
    import_btn.Click += lambda sender, args: _import(
        read_all, write_all, discipline_label, on_imported
    )

    buttons_panel.Children.Insert(0, export_btn)
    buttons_panel.Children.Insert(1, import_btn)
