# -*- coding: utf-8 -*-

__title__ = u"Обновить\nэкспликацию"
__doc__ = (
    u"Пересобирает экспликации фрагментов (созданные кнопкой «Экспликация "
    u"фрагмента») по текущим помещениям и настройкам.\n\n"
    u"Клик — все экспликации фрагментов в проекте.\n"
    u"Shift+клик — только выбранные: выделенные спецификации (на листе или в "
    u"диспетчере проекта), открытая экспликация, экспликации открытого плана "
    u"или листа; если ничего не выбрано — список, из которого выбрать."
)
__author__ = "Pipers"

import traceback
from eomtools.i18n import tr

from Autodesk.Revit.DB import SubTransaction

from pyrevit import revit, forms, script, EXEC_PARAMS
from eomtools import select_list

from eomtools import room_explication as rexp
from eomtools import room_explication_settings

doc = revit.doc
uidoc = revit.uidoc
TITLE = tr(u"Обновить экспликацию")

settings = room_explication_settings.get_settings_silent()

try:
    only_selected = bool(EXEC_PARAMS.config_mode)  # Shift+клик
except Exception:
    only_selected = False

all_items = rexp.list_explications(doc)
if not all_items:
    forms.alert(tr(u"В проекте нет экспликаций фрагментов. Создайте их "
                u"кнопкой «Экспликация фрагмента»."), title=TITLE, exitscript=True)

if not only_selected:
    items, scope = all_items, tr(u"все в проекте")
else:
    items, scope = rexp.explications_to_update(doc, uidoc)
    if not items:
        # Ничего не выделено и не открыто — выбрать из списка.
        by_name = {}
        for e in all_items:
            plan = (tr(u"план «{}»").format(rexp.element_name(e[1])) if e[1] is not None
                    else tr(u"план удалён"))
            label = u"{}   ({})".format(rexp.element_name(e[0]), plan)
            if label in by_name:  # одинаковые имена — различаем по Id
                label = u"{} [{}]".format(label, e[0].Id)
            by_name[label] = e
        picked = select_list.SelectFromList.show(
            sorted(by_name.keys()), title=tr(u"Какие экспликации обновить"),
            button_name=tr(u"Обновить"), multiselect=True)
        if not picked:
            script.exit()
        items, scope = [by_name[n] for n in picked], tr(u"выбранные")

done, lost, failed = [], [], []
with revit.Transaction(TITLE):
    for schedule, view, sources in items:
        name = rexp.element_name(schedule)
        if view is None or rexp.unsupported_reason(view):
            lost.append(name)
            continue
        # Ошибка в одной экспликации откатывает только её.
        sub = SubTransaction(doc)
        sub.Start()
        try:
            _s, count, _skipped, notes = rexp.rebuild(
                doc, view, sources, settings, schedule, __revit__.Application)
            sub.Commit()
            done.append(tr(u"{} — {} пом.{}").format(
                name, count, u"".join(u" " + n for n in notes)))
        except Exception:
            sub.RollBack()
            failed.append(u"{}:\n{}".format(name, traceback.format_exc()))

lines = [tr(u"Обновлено ({}): {}").format(scope, len(done))] + done
if lost:
    lines.append(tr(u"\nНе обновлены — план удалён или у него выключена подрезка:"))
    lines += lost
if failed:
    lines.append(tr(u"\nОшибки:"))
    lines += failed
forms.alert(u"\n".join(lines), title=TITLE)
