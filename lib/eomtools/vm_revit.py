# -*- coding: utf-8 -*-
"""View Manager: чтение и изменение видимости на активном виде.

Вызывать только из контекста Revit API (ExternalEvent, Idling), не напрямую из WPF.

Куда пишутся изменения (отдельно для рабочих наборов, модельных категорий и
категорий аннотаций — это три разных пункта шаблона вида):
- у вида нет шаблона, или шаблон не управляет этим пунктом — в сам вид
  (как «Видимость/Графика»);
- шаблон управляет пунктом — в шаблон, если это разрешено в настройках
  View Manager; иначе изменение не делается.

Каждое действие — одна транзакция «View Manager: …», отменяется Ctrl+Z.
"""
try:
    unicode
except NameError:
    unicode = str

import uuid

from Autodesk.Revit.DB import (
    BuiltInCategory, BuiltInParameter, Category, CategoryType, CheckoutStatus, ElementId,
    FailureProcessingResult, FilteredElementCollector, FilteredWorksetCollector,
    IFailuresPreprocessor, Transaction, TransactionStatus, View, ViewType,
    WorksetDefaultVisibilitySettings, WorksetKind, WorksetVisibility, WorksharingUtils)

_NAMESPACE = 'EOM.ViewManager.Revit.N' + uuid.uuid4().hex

SUPPORTED_TYPES = (
    ViewType.ThreeD, ViewType.FloorPlan, ViewType.CeilingPlan, ViewType.EngineeringPlan,
    ViewType.AreaPlan, ViewType.Section, ViewType.Elevation, ViewType.Detail)

TX_PREFIX = u'View Manager: '

# что меняем -> пункт шаблона вида, который этим управляет
SCOPES = {
    'worksets': BuiltInParameter.VIS_GRAPHICS_WORKSETS,
    'model': BuiltInParameter.VIS_GRAPHICS_MODEL,
    'annotation': BuiltInParameter.VIS_GRAPHICS_ANNOTATION,
    'filters': BuiltInParameter.VIS_GRAPHICS_FILTERS,
}
SCOPE_TITLES = {'worksets': u'рабочими наборами', 'model': u'модельными категориями',
                'annotation': u'категориями аннотаций', 'filters': u'фильтрами'}
CATEGORY_TYPES = {'model': CategoryType.Model, 'annotation': CategoryType.Annotation}


class ViewManagerError(Exception):
    pass


def _id_value(eid):
    value = getattr(eid, 'Value', None)
    if value is None:
        value = eid.IntegerValue
    return value


def supported(view):
    return view is not None and not view.IsTemplate and view.ViewType in SUPPORTED_TYPES


# ---------- вид или шаблон ----------
def targets(doc, view):
    """Для каждого пункта: шаблон, управляет ли он пунктом, сколько видов с ним."""
    result = dict((scope, {'template': None, 'name': u'', 'controlled': False, 'uses': 0})
                  for scope in SCOPES)
    tid = view.ViewTemplateId
    if tid is None or _id_value(tid) < 0:
        return result
    template = doc.GetElement(tid)
    if template is None:
        return result
    free = set(_id_value(x) for x in template.GetNonControlledTemplateParameterIds())
    tval = _id_value(tid)
    uses = sum(1 for v in FilteredElementCollector(doc).OfClass(View)
               if not v.IsTemplate and _id_value(v.ViewTemplateId) == tval)
    for scope, param in SCOPES.items():
        info = result[scope]
        info['template'] = template
        info['name'] = template.Name
        info['controlled'] = _id_value(ElementId(param)) not in free
        info['uses'] = uses
    return result


def _check_not_borrowed(doc, element, what):
    """В совместной модели: элемент не должен быть занят другим пользователем."""
    if not doc.IsWorkshared:
        return
    status = WorksharingUtils.GetCheckoutStatus(doc, element.Id)
    if status == CheckoutStatus.OwnedByOtherUser:
        owner = u''
        try:
            owner = WorksharingUtils.GetWorksharingTooltipInfo(doc, element.Id).Owner
        except Exception:
            pass
        raise ViewManagerError(
            u'{0} «{1}» сейчас редактирует {2}. Попросите освободить его '
            u'(синхронизация с «Освободить все элементы») и повторите.'.format(
                what, element.Name, owner or u'другой пользователь'))


def _owner(doc, view, info, scope, allow_template):
    if not info['controlled']:
        return view
    if not allow_template:
        raise ViewManagerError(
            u'Шаблон «{0}» управляет {1}, а изменение шаблона запрещено в настройках '
            u'View Manager.'.format(info['name'], SCOPE_TITLES[scope]))
    _check_not_borrowed(doc, info['template'], u'Шаблон')
    return info['template']


class _CollectFailures(IFailuresPreprocessor):
    """Любое предупреждение или ошибка Revit при нашем изменении — откат целиком.

    Изменения видимости сами по себе предупреждений не дают; если они есть
    (например, «нет прав на редактирование, изменения будут временными»),
    лучше ничего не менять и показать причину в панели, чем оставить
    модель с временными изменениями."""
    __namespace__ = _NAMESPACE

    def __init__(self):
        self.messages = []

    def PreprocessFailures(self, accessor):
        failures = list(accessor.GetFailureMessages())
        if not failures:
            return FailureProcessingResult.Continue
        for f in failures:
            try:
                text = f.GetDescriptionText()
            except Exception:
                text = u''
            if text and text not in self.messages:
                self.messages.append(text)
        return FailureProcessingResult.ProceedWithRollBack


def _run(doc, label, action):
    if doc.IsReadOnly:
        raise ViewManagerError(u'Модель открыта только для чтения.')
    collector = _CollectFailures()
    tx = Transaction(doc, TX_PREFIX + label)
    options = tx.GetFailureHandlingOptions()
    options.SetFailuresPreprocessor(collector)
    options.SetClearAfterRollback(True)
    tx.SetFailureHandlingOptions(options)
    tx.Start()
    try:
        action()
        if tx.Commit() != TransactionStatus.Committed:
            reason = u' '.join(collector.messages[:2])
            raise ViewManagerError(
                u'Revit отклонил изменение, ничего не изменено.' +
                (u' Причина: {0}'.format(reason) if reason else u''))
    except Exception:
        if tx.GetStatus() == TransactionStatus.Started:
            tx.RollBack()
        raise
    finally:
        tx.Dispose()


# ---------- рабочие наборы ----------
def user_worksets(doc):
    """[(имя, WorksetId)] пользовательских рабочих наборов, по имени."""
    if not doc.IsWorkshared:
        return []
    items = [(ws.Name, ws.Id) for ws in
             FilteredWorksetCollector(doc).OfKind(WorksetKind.UserWorkset)]
    items.sort(key=lambda x: x[0].lower())
    return items


def _workset_visible(owner, wsid, defaults):
    state = owner.GetWorksetVisibility(wsid)
    if state == WorksetVisibility.Visible:
        return True
    if state == WorksetVisibility.Hidden:
        return False
    return defaults.IsWorksetVisible(wsid)


def set_worksets(doc, view, names, show, allow_template, label):
    """Показать/скрыть рабочие наборы по именам. Возвращает (изменено, нет в модели)."""
    if not doc.IsWorkshared:
        raise ViewManagerError(u'В модели нет рабочих наборов.')
    owner = _owner(doc, view, targets(doc, view)['worksets'], 'worksets', allow_template)
    by_name = dict(user_worksets(doc))
    defaults = WorksetDefaultVisibilitySettings.GetWorksetDefaultVisibilitySettings(doc)
    target = WorksetVisibility.Visible if show else WorksetVisibility.Hidden
    todo, missing = [], 0
    for name in names:
        wsid = by_name.get(name)
        if wsid is None:
            missing += 1
        elif _workset_visible(owner, wsid, defaults) != show:
            todo.append(wsid)
    if todo:
        def action():
            for wsid in todo:
                owner.SetWorksetVisibility(wsid, target)
        _run(doc, label, action)
    return len(todo), missing


# ---------- категории ----------
def _bic_key(category):
    bic = getattr(category, 'BuiltInCategory', None)
    if bic is None or int(bic) == -1:
        try:
            from System import Enum
            bic = Enum.ToObject(BuiltInCategory, int(_id_value(category.Id)))
        except Exception:
            return None
    key = unicode(bic)
    if not key.startswith(u'OST_'):
        return None
    return key


def _category(doc, key):
    try:
        bic = getattr(BuiltInCategory, key)
    except Exception:
        return None
    try:
        return Category.GetCategory(doc, bic)
    except Exception:
        return None


def category_catalog(doc):
    """Все категории верхнего уровня: {'model': [(ключ, имя)], 'annotation': [...]}."""
    result = {'model': [], 'annotation': []}
    for cat in doc.Settings.Categories:
        if getattr(cat, 'IsVisibleInUI', True) is False:
            continue
        for kind, ctype in CATEGORY_TYPES.items():
            if cat.CategoryType == ctype:
                key = _bic_key(cat)
                if key:
                    result[kind].append((key, cat.Name))
    for kind in result:
        result[kind].sort(key=lambda x: x[1].lower())
    return result


def _category_states(doc, view, owner, keys):
    items = []
    for key in keys:
        cat = _category(doc, key)
        if cat is None:
            items.append({'key': key, 'name': key, 'exists': False, 'available': False,
                          'visible': False})
            continue
        # проверять там, куда пишется изменение: при шаблоне, который управляет
        # категориями, сам вид всегда отвечает «нельзя»
        available = owner.CanCategoryBeHidden(cat.Id)
        visible = available and not owner.GetCategoryHidden(cat.Id)
        items.append({'key': key, 'name': cat.Name, 'exists': True, 'available': available,
                      'visible': visible})
    return items


def set_categories(doc, view, keys_by_kind, show, allow_template, label):
    """Показать/скрыть категории одной транзакцией.

    ``keys_by_kind`` — {'model': [ключи], 'annotation': [ключи]}. Возвращает (изменено, пропущено)."""
    tg = targets(doc, view)
    todo, skipped = [], 0
    for kind, keys in keys_by_kind.items():
        if not keys:
            continue
        owner = _owner(doc, view, tg[kind], kind, allow_template)
        for key in keys:
            cat = _category(doc, key)
            if cat is None or not owner.CanCategoryBeHidden(cat.Id):
                skipped += 1
            elif owner.GetCategoryHidden(cat.Id) != (not show):
                todo.append((owner, cat.Id))
    if todo:
        def action():
            for owner, cid in todo:
                owner.SetCategoryHidden(cid, not show)
        _run(doc, label, action)
    return len(todo), skipped


# ---------- снимок для панели ----------
def snapshot(doc, view, profile):
    allow = profile['allow_template']
    snap = {
        'doc_title': doc.Title, 'view_name': view.Name if view is not None else u'',
        'supported': False, 'workshared': doc.IsWorkshared, 'worksets': [],
        'targets': None, 'blocked': {}, 'categories': {'model': [], 'annotation': []},
        'catalog': {'model': [], 'annotation': []},
    }
    if doc.IsFamilyDocument:
        return snap
    snap['catalog'] = category_catalog(doc)
    if not supported(view):
        return snap
    snap['supported'] = True
    tg = targets(doc, view)
    snap['targets'] = dict((s, {'name': i['name'], 'controlled': i['controlled'], 'uses': i['uses'],
                                'has_template': i['template'] is not None})
                           for s, i in tg.items())
    snap['blocked'] = dict((s, i['controlled'] and not allow) for s, i in tg.items())

    def owner_of(scope):
        return tg[scope]['template'] if tg[scope]['controlled'] else view

    for kind in ('model', 'annotation'):
        snap['categories'][kind] = _category_states(doc, view, owner_of(kind),
                                                    profile['categories'][kind])
    if doc.IsWorkshared:
        owner = owner_of('worksets')
        defaults = WorksetDefaultVisibilitySettings.GetWorksetDefaultVisibilitySettings(doc)
        snap['worksets'] = [{'name': name, 'visible': _workset_visible(owner, wsid, defaults)}
                            for name, wsid in user_worksets(doc)]
    return snap
