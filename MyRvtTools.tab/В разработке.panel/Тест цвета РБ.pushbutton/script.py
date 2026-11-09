# -*- coding: utf-8 -*-
"""Технический тест: красит ли фильтр «Рабочий набор содержит <текст>» элементы внутри связей.

Создаёт на активном виде (или его шаблоне, если шаблон управляет фильтрами и это
разрешено в View Manager) фильтр VM_TEST_РБ_<текст> с красной заливкой и линиями.
Одна транзакция — Ctrl+Z убирает всё. Плюс отчёт: в каких связях есть такой РБ.
"""
from pyrevit import forms, revit, script

from Autodesk.Revit.DB import (
    BuiltInParameter, Color, ElementId, FilteredElementCollector, FilteredWorksetCollector,
    FillPatternElement, ElementParameterFilter, OverrideGraphicSettings, ParameterFilterElement,
    ParameterFilterRuleFactory, ParameterFilterUtilities, RevitLinkInstance, Transaction,
    TransactionStatus, WorksetKind)
from System.Collections.Generic import List

from eomtools import vm_profile, vm_revit

doc = revit.doc
view = doc.ActiveView
out = script.get_output()

if not vm_revit.supported(view):
    forms.alert(u'Откройте план, разрез, фасад или 3D-вид.', exitscript=True)

text = forms.ask_for_string(
    default=u'Канализация',
    prompt=u'Часть имени рабочего набора ВНУТРИ связи (например, «Канализация»):',
    title=u'Тест цвета РБ')
if not text:
    script.exit()
text = text.strip()

# ---------- отчёт: где есть такой рабочий набор ----------
out.print_md(u'## Тест цвета РБ: «{0}»'.format(text))
rows = []
for link in FilteredElementCollector(doc).OfClass(RevitLinkInstance):
    ldoc = link.GetLinkDocument()
    if ldoc is None:
        rows.append([link.Name, u'не загружена', u''])
        continue
    if not ldoc.IsWorkshared:
        rows.append([link.Name, u'без рабочих наборов', u''])
        continue
    names = [ws.Name for ws in FilteredWorksetCollector(ldoc).OfKind(WorksetKind.UserWorkset)
             if text.lower() in ws.Name.lower()]
    rows.append([link.Name, u'да' if names else u'нет', u', '.join(names)])
own = []
if doc.IsWorkshared:
    own = [ws.Name for ws in FilteredWorksetCollector(doc).OfKind(WorksetKind.UserWorkset)
           if text.lower() in ws.Name.lower()]
out.print_table(rows, columns=[u'Связь', u'Есть такой РБ', u'Подходящие РБ внутри связи'])
out.print_md(u'В нашей модели подходящие РБ: {0}'.format(u', '.join(own) or u'нет'))

# ---------- категории, где параметр «Рабочий набор» доступен для фильтра ----------
param_id = ElementId(BuiltInParameter.ELEM_PARTITION_PARAM)
cats = List[ElementId]()
for cid in ParameterFilterUtilities.GetAllFilterableCategories():
    try:
        ids = ParameterFilterUtilities.GetFilterableParametersInCommon(doc, List[ElementId]([cid]))
        if any(vm_revit._id_value(p) == vm_revit._id_value(param_id) for p in ids):
            cats.Add(cid)
    except Exception:
        pass
if cats.Count == 0:
    forms.alert(u'Revit не даёт фильтровать по параметру «Рабочий набор» ни в одной категории.',
                exitscript=True)

try:
    rule = ParameterFilterRuleFactory.CreateContainsRule(param_id, text)
except TypeError:
    rule = ParameterFilterRuleFactory.CreateContainsRule(param_id, text, False)

solid = None
for fp in FilteredElementCollector(doc).OfClass(FillPatternElement):
    if fp.GetFillPattern().IsSolidFill:
        solid = fp
        break

red = Color(230, 30, 30)
ogs = OverrideGraphicSettings()
ogs.SetProjectionLineColor(red)
ogs.SetCutLineColor(red)
if solid is not None:
    ogs.SetSurfaceForegroundPatternId(solid.Id)
    ogs.SetSurfaceForegroundPatternColor(red)
    ogs.SetCutForegroundPatternId(solid.Id)
    ogs.SetCutForegroundPatternColor(red)

# вид или шаблон — по тем же правилам, что View Manager
profile = vm_profile.load()
from Autodesk.Revit.DB import BuiltInParameter as BIP
owner = view
tid = view.ViewTemplateId
if vm_revit._id_value(tid) > 0:
    template = doc.GetElement(tid)
    free = set(vm_revit._id_value(x) for x in template.GetNonControlledTemplateParameterIds())
    if vm_revit._id_value(ElementId(BIP.VIS_GRAPHICS_FILTERS)) not in free:
        if not profile['allow_template']:
            forms.alert(u'Шаблон «{0}» управляет фильтрами, а изменение шаблона запрещено в '
                        u'View Manager. Поставьте галочку или откройте вид без шаблона.'
                        .format(template.Name), exitscript=True)
        owner = template

name = u'VM_TEST_РБ_{0}'.format(text)
for bad in u'{}[]:\\|;<>?`~':
    name = name.replace(bad, u'_')

tx = Transaction(doc, u'View Manager: тест цвета РБ «{0}»'.format(text))
tx.Start()
try:
    existing = [f for f in FilteredElementCollector(doc).OfClass(ParameterFilterElement)
                if f.Name == name]
    if existing:
        pfe = existing[0]
        pfe.SetCategories(cats)
        pfe.SetElementFilter(ElementParameterFilter(rule))
    else:
        pfe = ParameterFilterElement.Create(doc, name, cats, ElementParameterFilter(rule))
    if not owner.IsFilterApplied(pfe.Id):
        owner.AddFilter(pfe.Id)
    owner.SetFilterOverrides(pfe.Id, ogs)
    owner.SetFilterVisibility(pfe.Id, True)
    status = tx.Commit()
except Exception as exc:
    if tx.GetStatus() == TransactionStatus.Started:
        tx.RollBack()
    out.print_md(u'**Ошибка при создании фильтра:** {0}'.format(exc))
    forms.alert(u'Не удалось создать фильтр:\n{0}'.format(exc))
    script.exit()

out.print_md(u'Фильтр **{0}** применён к {1} «{2}» (категорий: {3}). Статус: {4}.'.format(
    name, u'шаблону' if owner is not view else u'виду', owner.Name, cats.Count, status))
out.print_md(u'Что проверить: стали ли **красными элементы внутри связей**, лежащие в РБ '
             u'«{0}». Отменить тест — Ctrl+Z.'.format(text))
