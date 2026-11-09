# -*- coding: utf-8 -*-
"""View Manager: чтение и изменение видимости рабочих наборов на активном виде.

Вызывать только из контекста Revit API (ExternalEvent, Idling), не напрямую из WPF.

Куда пишутся изменения:
- у вида нет шаблона, или шаблон не управляет пунктом «Рабочие наборы» —
  в настройки самого вида (как «Видимость/Графика» → «Рабочие наборы»);
- шаблон управляет рабочими наборами — в шаблон, если это разрешено
  в настройках View Manager; иначе изменение не делается.

Каждое действие — одна транзакция «View Manager: …», отменяется Ctrl+Z.
"""
try:
    unicode
except NameError:
    unicode = str

from Autodesk.Revit.DB import (
    BuiltInParameter, ElementId, FilteredElementCollector, FilteredWorksetCollector,
    Transaction, TransactionStatus, View, ViewType, WorksetDefaultVisibilitySettings,
    WorksetKind, WorksetVisibility)

SUPPORTED_TYPES = (
    ViewType.ThreeD, ViewType.FloorPlan, ViewType.CeilingPlan, ViewType.EngineeringPlan,
    ViewType.AreaPlan, ViewType.Section, ViewType.Elevation, ViewType.Detail)

TX_PREFIX = u'View Manager: '


class ViewManagerError(Exception):
    pass


def _id_value(eid):
    value = getattr(eid, 'Value', None)
    if value is None:
        value = eid.IntegerValue
    return value


def supported(view):
    return view is not None and not view.IsTemplate and view.ViewType in SUPPORTED_TYPES


def template_info(doc, view, parameter):
    """Шаблон вида и управляет ли он пунктом ``parameter`` (BuiltInParameter)."""
    info = {'template': None, 'name': u'', 'controlled': False, 'uses': 0}
    tid = view.ViewTemplateId
    if tid is None or _id_value(tid) < 0:
        return info
    template = doc.GetElement(tid)
    if template is None:
        return info
    info['template'] = template
    info['name'] = template.Name
    pid = _id_value(ElementId(parameter))
    free = set(_id_value(x) for x in template.GetNonControlledTemplateParameterIds())
    info['controlled'] = pid not in free
    if info['controlled']:
        tval = _id_value(tid)
        uses = 0
        for v in FilteredElementCollector(doc).OfClass(View):
            if not v.IsTemplate and _id_value(v.ViewTemplateId) == tval:
                uses += 1
        info['uses'] = uses
    return info


def user_worksets(doc):
    """[(имя, WorksetId)] пользовательских рабочих наборов, по имени."""
    if not doc.IsWorkshared:
        return []
    items = [(ws.Name, ws.Id) for ws in
             FilteredWorksetCollector(doc).OfKind(WorksetKind.UserWorkset)]
    items.sort(key=lambda x: x[0].lower())
    return items


def _visible(doc, owner, wsid, defaults):
    state = owner.GetWorksetVisibility(wsid)
    if state == WorksetVisibility.Visible:
        return True
    if state == WorksetVisibility.Hidden:
        return False
    return defaults.IsWorksetVisible(wsid)


def _owner(doc, view, allow_template):
    """Вид или шаблон, в который пишется видимость рабочих наборов."""
    info = template_info(doc, view, BuiltInParameter.VIS_GRAPHICS_WORKSETS)
    if not info['controlled']:
        return view, info
    if not allow_template:
        raise ViewManagerError(
            u'Шаблон «{0}» управляет рабочими наборами, а изменение шаблона запрещено '
            u'в настройках View Manager.'.format(info['name']))
    return info['template'], info


def snapshot(doc, view, allow_template):
    """Состояние активного вида для панели."""
    snap = {
        'doc_title': doc.Title, 'view_name': view.Name if view is not None else u'',
        'supported': False, 'workshared': doc.IsWorkshared, 'worksets': [],
        'template': None, 'blocked': False,
    }
    if doc.IsFamilyDocument or not supported(view):
        return snap
    snap['supported'] = True
    info = template_info(doc, view, BuiltInParameter.VIS_GRAPHICS_WORKSETS)
    snap['template'] = {'name': info['name'], 'controlled': info['controlled'],
                        'uses': info['uses']} if info['template'] is not None else None
    snap['blocked'] = info['controlled'] and not allow_template
    if not doc.IsWorkshared:
        return snap
    owner = info['template'] if info['controlled'] else view
    defaults = WorksetDefaultVisibilitySettings.GetWorksetDefaultVisibilitySettings(doc)
    snap['worksets'] = [{'name': name, 'visible': _visible(doc, owner, wsid, defaults)}
                        for name, wsid in user_worksets(doc)]
    return snap


def set_worksets(doc, view, names, show, allow_template, label):
    """Показать/скрыть рабочие наборы по именам одной транзакцией.

    Возвращает (изменено, не найдено в модели)."""
    if doc.IsReadOnly:
        raise ViewManagerError(u'Модель открыта только для чтения.')
    if not doc.IsWorkshared:
        raise ViewManagerError(u'В модели нет рабочих наборов.')
    owner, _ = _owner(doc, view, allow_template)
    by_name = dict(user_worksets(doc))
    defaults = WorksetDefaultVisibilitySettings.GetWorksetDefaultVisibilitySettings(doc)
    target = WorksetVisibility.Visible if show else WorksetVisibility.Hidden
    todo, missing = [], 0
    for name in names:
        wsid = by_name.get(name)
        if wsid is None:
            missing += 1
        elif _visible(doc, owner, wsid, defaults) != show:
            todo.append(wsid)
    if not todo:
        return 0, missing
    tx = Transaction(doc, TX_PREFIX + label)
    tx.Start()
    try:
        for wsid in todo:
            owner.SetWorksetVisibility(wsid, target)
        if tx.Commit() != TransactionStatus.Committed:
            raise ViewManagerError(u'Revit не сохранил изменения видимости.')
    except Exception:
        if tx.GetStatus() == TransactionStatus.Started:
            tx.RollBack()
        raise
    finally:
        tx.Dispose()
    return len(todo), missing
