# -*- coding: utf-8 -*-
"""View Manager, вкладка «Цвета»: окраска по рабочим наборам, в том числе внутри связей.

Вызывать только из контекста Revit API (ExternalEvent, Idling).

Как красим. Для каждого включённого рабочего набора создаётся фильтр вида
``3_ВСЕ_РБ_<имя РБ>`` (правило отдела: 3 — фильтр, ВСЕ — все категории, РБ — по чему
фильтруем, дальше слово) с условием «Рабочий набор = <имя>». Фильтр вида действует
на нашу модель и на все связи «По основному виду» — Revit проверяет имя рабочего
набора и у элементов внутри связей (проверено тестом).

Одинаковый РБ у нас и в связи (например, 2_КНК у нас и у СС) различаем параметром
из настройки (по умолчанию SMNX_Фильтр — у наших элементов заполнен, у смежников нет):
- наш:   «РБ = имя» и «параметр заполнен» → ``3_ВСЕ_РБ_<имя>``
- связи: «РБ = имя» и «параметр пуст»     → ``3_ВСЕ_РБ_<имя>_связь``

Фильтры пишутся в вид или в его шаблон (если шаблон управляет фильтрами и это
разрешено). «Снять» убирает с вида/шаблона только фильтры с префиксом ``3_ВСЕ_РБ_``;
сами элементы фильтров остаются в модели — ими могут пользоваться другие виды.
"""
try:
    unicode
except NameError:
    unicode = str

from Autodesk.Revit.DB import (
    BuiltInParameter, Color, ElementId, ElementParameterFilter, FilteredElementCollector,
    FilteredWorksetCollector, FillPatternElement, FilterRule, OverrideGraphicSettings,
    ParameterElement, ParameterFilterElement, ParameterFilterRuleFactory,
    ParameterFilterUtilities, RevitLinkInstance, WorksetKind)
from System.Collections.Generic import List

from eomtools import vm_revit
from eomtools.vm_revit import ViewManagerError, _id_value

PREFIX = u'3_ВСЕ_РБ_'
LINK_SUFFIX = u'_связь'
BAD_CHARS = u'{}[]:\\|;<>?`~'


# ---------- что есть в модели ----------
def scan(doc):
    """Рабочие наборы нашей модели и загруженных связей.

    {'own': [имена], 'links': [{'title': имя файла связи, 'worksets': [имена]}]}"""
    result = {'own': [], 'links': []}
    if doc.IsWorkshared:
        result['own'] = sorted((ws.Name for ws in
                                FilteredWorksetCollector(doc).OfKind(WorksetKind.UserWorkset)),
                               key=lambda n: n.lower())
    seen = set()
    for link in FilteredElementCollector(doc).OfClass(RevitLinkInstance):
        ldoc = link.GetLinkDocument()
        if ldoc is None:
            continue
        title = ldoc.Title
        if title in seen:
            continue
        seen.add(title)
        names = []
        if ldoc.IsWorkshared:
            names = sorted((ws.Name for ws in
                            FilteredWorksetCollector(ldoc).OfKind(WorksetKind.UserWorkset)),
                           key=lambda n: n.lower())
        result['links'].append({'title': title, 'worksets': names})
    result['links'].sort(key=lambda x: x['title'].lower())
    return result


def shared_names(info):
    """Имена РБ, которые есть и у нас, и хотя бы в одной связи."""
    own = set(info['own'])
    linked = set()
    for link in info['links']:
        linked.update(link['worksets'])
    return own & linked


def filter_name(item, shared):
    name = PREFIX + item['name']
    if item['scope'] == 'link' and item['name'] in shared:
        name += LINK_SUFFIX
    for ch in BAD_CHARS:
        name = name.replace(ch, u'_')
    return name


# ---------- применение ----------
def _param_id_by_name(doc, name):
    """Id параметра проекта/общего параметра по имени (для правила фильтра)."""
    from Autodesk.Revit.DB import SharedParameterElement
    for cls in (SharedParameterElement, ParameterElement):
        for pe in FilteredElementCollector(doc).OfClass(cls):
            if pe.Name == name:
                return pe.Id
    return None


def _categories_with(doc, param_ids):
    """Категории, в которых все параметры из param_ids доступны для фильтра."""
    wanted = set(_id_value(p) for p in param_ids)
    cats = List[ElementId]()
    for cid in ParameterFilterUtilities.GetAllFilterableCategories():
        try:
            ids = ParameterFilterUtilities.GetFilterableParametersInCommon(doc, List[ElementId]([cid]))
        except Exception:
            continue
        have = set(_id_value(p) for p in ids)
        if wanted <= have:
            cats.Add(cid)
    return cats


def _equals_rule(param_id, text):
    try:
        return ParameterFilterRuleFactory.CreateEqualsRule(param_id, text)
    except TypeError:
        return ParameterFilterRuleFactory.CreateEqualsRule(param_id, text, True)


def _solid_fill(doc):
    for fp in FilteredElementCollector(doc).OfClass(FillPatternElement):
        if fp.GetFillPattern().IsSolidFill:
            return fp.Id
    return None


def _overrides(item, solid_id):
    r, g, b = item['color']
    color = Color(r, g, b)
    ogs = OverrideGraphicSettings()
    ogs.SetProjectionLineColor(color)
    ogs.SetCutLineColor(color)
    if solid_id is not None:
        ogs.SetSurfaceForegroundPatternId(solid_id)
        ogs.SetSurfaceForegroundPatternColor(color)
        ogs.SetCutForegroundPatternId(solid_id)
        ogs.SetCutForegroundPatternColor(color)
    ogs.SetSurfaceTransparency(int(item['transparency']))
    return ogs


def _our_filters(doc, owner):
    """{имя: id} фильтров с префиксом 3_ВСЕ_РБ_, применённых к owner."""
    result = {}
    for fid in owner.GetFilters():
        f = doc.GetElement(fid)
        if f is not None and f.Name.startswith(PREFIX):
            result[f.Name] = fid
    return result


def applied_count(doc, view):
    tg = vm_revit.targets(doc, view)['filters']
    owner = tg['template'] if tg['controlled'] else view
    return len(_our_filters(doc, owner))


def apply(doc, view, colors, allow_template, label):
    """Создать/обновить фильтры схемы и привести к ней вид (или шаблон).

    Возвращает (применено фильтров, снято лишних)."""
    owner = vm_revit._owner(doc, view, vm_revit.targets(doc, view)['filters'], 'filters',
                            allow_template)
    info = scan(doc)
    shared = shared_names(info)
    existing_names = set(info['own'])
    for link in info['links']:
        existing_names.update(link['worksets'])

    items = [it for it in colors['items'] if it['enabled'] and it['name'] in existing_names]
    ws_param = ElementId(BuiltInParameter.ELEM_PARTITION_PARAM)
    need_split = any(it['name'] in shared for it in items)
    split_param = None
    if need_split:
        split_param = _param_id_by_name(doc, colors['param'])
        if split_param is None:
            raise ViewManagerError(
                u'Рабочие наборы {0} есть и у нас, и в связях, а параметра «{1}», которым их '
                u'различать, в модели нет. Укажите параметр в настройке цветов.'.format(
                    u', '.join(sorted(n for n in shared if any(i['name'] == n for i in items))),
                    colors['param']))

    cats_ws = _categories_with(doc, [ws_param])
    cats_split = _categories_with(doc, [ws_param, split_param]) if need_split else None
    if cats_ws.Count == 0:
        raise ViewManagerError(u'Revit не даёт фильтровать по рабочему набору.')
    solid_id = _solid_fill(doc)
    by_name = dict((f.Name, f) for f in FilteredElementCollector(doc).OfClass(ParameterFilterElement))

    def action():
        wanted = set()
        for it in items:
            name = filter_name(it, shared)
            wanted.add(name)
            rules = List[FilterRule]()
            rules.Add(_equals_rule(ws_param, it['name']))
            cats = cats_ws
            if it['name'] in shared:
                cats = cats_split
                if it['scope'] == 'own':
                    rules.Add(ParameterFilterRuleFactory.CreateHasValueParameterRule(split_param))
                else:
                    rules.Add(ParameterFilterRuleFactory.CreateHasNoValueParameterRule(split_param))
            element_filter = ElementParameterFilter(rules)
            pfe = by_name.get(name)
            if pfe is None:
                pfe = ParameterFilterElement.Create(doc, name, cats, element_filter)
            else:
                pfe.SetCategories(cats)
                pfe.SetElementFilter(element_filter)
            if not owner.IsFilterApplied(pfe.Id):
                owner.AddFilter(pfe.Id)
            owner.SetFilterOverrides(pfe.Id, _overrides(it, solid_id))
            owner.SetFilterVisibility(pfe.Id, True)
        removed = 0
        for name, fid in _our_filters(doc, owner).items():
            if name not in wanted:
                owner.RemoveFilter(fid)
                removed += 1
        result.append((len(wanted), removed))

    result = []
    vm_revit._run(doc, label, action)
    applied, removed = result[0] if result else (0, 0)
    return applied, removed


def clear(doc, view, allow_template, label):
    """Убрать с вида/шаблона все фильтры 3_ВСЕ_РБ_. Возвращает число снятых."""
    owner = vm_revit._owner(doc, view, vm_revit.targets(doc, view)['filters'], 'filters',
                            allow_template)
    ours = _our_filters(doc, owner)
    if not ours:
        return 0

    def action():
        for fid in ours.values():
            owner.RemoveFilter(fid)
    vm_revit._run(doc, label, action)
    return len(ours)
