# -*- coding: utf-8 -*-

__title__ = u"Марки\nлотков"
__doc__ = (
    u"Как «Марки оборудования», но для кабельных лотков. «Поставить "
    u"марки» — выберите лотки: марки встают на прямые участки без марки "
    u"на виде (соединительные детали и лотки под наклоном не "
    u"маркируются) и сразу раскладываются ровно и без пересечений. "
    u"«Разложить марки» — выбранные марки лотков (или все марки "
    u"выбранных лотков) раскладываются заново.\n\n"
    u"Shift+клик — настройки (отступ, зазор, полка, «рядом», допуск наклона)."
)
__author__ = "Pipers"

import traceback
from eomtools.i18n import tr

from Autodesk.Revit.UI.Selection import ObjectType
from Autodesk.Revit.Exceptions import OperationCanceledException
from pyrevit import revit, forms, EXEC_PARAMS
from eomtools import select_list

from eomtools import equipment_tags, tray_tags, tray_tags_settings

doc = revit.doc
uidoc = revit.uidoc

MODE_LAYOUT = tr(u"Разложить выбранные марки")
MODE_CREATE = tr(u"Поставить марки на лотки")


def preselected():
    return [e for e in (doc.GetElement(i) for i in uidoc.Selection.GetElementIds())
            if e is not None]


def choose_mode(picked):
    """Только марки в выделении — сразу «разложить»; иначе спросить."""
    has_tags = any(isinstance(e, equipment_tags.IndependentTag) for e in picked)
    has_trays = any(tray_tags.is_tray(e) for e in picked)
    if has_tags and not has_trays:
        return MODE_LAYOUT
    mode = select_list.CommandSwitchWindow.show(
        [MODE_LAYOUT, MODE_CREATE],
        message=tr(u"Марки лотков — что сделать?")
    )
    if not mode:
        forms.alert(tr(u"Отменено."), exitscript=True)
    return mode


def pick(selection_filter, prompt):
    try:
        refs = uidoc.Selection.PickObjects(ObjectType.Element, selection_filter, prompt)
    except OperationCanceledException:
        forms.alert(tr(u"Выбор отменён."), exitscript=True)
    return [doc.GetElement(r) for r in refs]


def layout_entries(view, picked):
    pairs = tray_tags.resolve_tag_pairs(doc, view, picked)
    if not pairs:
        picked = pick(tray_tags.TrayTagFilter(),
                      tr(u"Выберите марки лотков (рамкой и/или кликами), Enter — готово"))
        pairs = tray_tags.resolve_tag_pairs(doc, view, picked)
    if not pairs:
        forms.alert(tr(u"Среди выбранного нет марок лотков."), exitscript=True)
    return [(tray, tag, None) for tray, tag in pairs]


def choose_tag_type(category):
    """Тип марки лотков: единственный — сразу, иначе список (последний
    выбранный — первым)."""
    types = equipment_tags.tag_types_for_category(doc, category)
    if not types:
        forms.alert(tr(u"В проекте нет загруженных марок для кабельных лотков "
                    u"(ни своих, ни мультикатегорийных)."), exitscript=True)
    options = [(equipment_tags.tag_type_label(s), s.Id) for s in types]
    if len(options) == 1:
        return options[0][1]
    last = tray_tags_settings.load_tag_type_label()
    default_id = equipment_tags.default_tag_type_id(doc, category)
    options.sort(key=lambda o: (o[0] != last, default_id is None or o[1] != default_id))
    label = select_list.SelectFromList.show(
        [name for name, _tid in options],
        title=tr(u"Марки лотков — какой маркой"),
        button_name=tr(u"Поставить"),
        multiselect=False,
    )
    if not label:
        forms.alert(tr(u"Отменено."), exitscript=True)
    tray_tags_settings.save_tag_type_label(label)
    return dict(options)[label]


def create_entries(view, picked, settings):
    trays = tray_tags.resolve_trays(doc, picked)
    if not trays:
        trays = tray_tags.resolve_trays(doc, pick(
            tray_tags.TrayFilter(),
            tr(u"Выберите лотки (рамкой и/или кликами; соединительные детали "
            u"не выбираются), Enter — готово")))
    if not trays:
        forms.alert(tr(u"Не выбрано ни одного лотка."), exitscript=True)

    sloped = [t for t in trays if tray_tags.is_sloped(t, settings["max_slope_deg"])]
    flat = [t for t in trays if not tray_tags.is_sloped(t, settings["max_slope_deg"])]
    tagged = equipment_tags.tags_by_element(doc, view)
    untagged = [t for t in flat if equipment_tags.id_int(t.Id) not in tagged]
    counts = (len(sloped), len(flat) - len(untagged))
    if not untagged:
        forms.alert(tr(u"Маркировать нечего: под наклоном — {}, уже с маркой на "
                    u"этом виде — {}. Чтобы переложить марки — режим «{}».").format(
                        counts[0], counts[1], MODE_LAYOUT), exitscript=True)

    type_id = choose_tag_type(untagged[0].Category)
    return [(t, None, type_id) for t in untagged], counts


try:
    config_mode = bool(EXEC_PARAMS.config_mode)
except Exception:
    config_mode = False

if config_mode:
    edited = tray_tags_settings.get_settings_interactive()
    forms.alert(tr(u"Отменено, настройки не изменены.") if edited is None
                else tr(u"Настройки сохранены."), exitscript=True)

view = doc.ActiveView
if not equipment_tags.is_taggable_view(view):
    forms.alert(tr(u"Откройте план, разрез или фасад — на этом виде марки "
                u"лотков не ставятся."), exitscript=True)

settings = tray_tags_settings.load_settings()
picked = preselected()
mode = choose_mode(picked)
skipped = (0, 0)
if mode == MODE_LAYOUT:
    entries = layout_entries(view, picked)
else:
    entries, skipped = create_entries(view, picked, settings)

try:
    with revit.Transaction(tr(u"Марки лотков")):
        stats = tray_tags.run(doc, view, entries, settings)
except Exception:
    forms.alert(
        tr(u"Сбой при расстановке марок (транзакция отменена, изменения не "
        u"сохранены):\n\n{}").format(traceback.format_exc()),
        title=tr(u"Марки лотков"), exitscript=True
    )

lines = [tr(u"Разложено марок: {}").format(stats["moved"])]
if mode == MODE_CREATE:
    lines.append(tr(u"Из них новых: {}").format(stats["created"]))
    if skipped[0]:
        lines.append(tr(u"Лотки под наклоном (не маркировались): {}").format(skipped[0]))
    if skipped[1]:
        lines.append(tr(u"Уже с маркой на виде (не трогались): {}").format(skipped[1]))
if stats["failed"]:
    lines.append(tr(u"Не удалось поставить/сдвинуть: {}").format(stats["failed"]))
if stats["upper_floor"]:
    lines.append(tr(u"Только верхний этаж (тип без «/» в двухэтажном "
                 u"семействе): {}").format(stats["upper_floor"]))
if stats["no_bbox"]:
    lines.append(tr(u"Не видно на виде (пропущено): {}").format(stats["no_bbox"]))
if stats["overlaps"] or stats["crossings"]:
    lines.append(tr(u"\nМеста не хватило: марок внахлёст — {}, пересечений "
                 u"выносок — {}. Попробуйте уменьшить «рядом» или "
                 u"отступ в настройках (Shift+клик) либо выбрать "
                 u"лотки частями.").format(stats["overlaps"], stats["crossings"]))
forms.alert(u"\n".join(lines), title=tr(u"Марки лотков"))
