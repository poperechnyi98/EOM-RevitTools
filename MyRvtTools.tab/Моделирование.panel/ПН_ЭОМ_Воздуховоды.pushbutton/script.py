# -*- coding: utf-8 -*-
"""Назначение типов систем воздуховодам по имени их типа."""
from Autodesk.Revit.DB import (BuiltInCategory, BuiltInParameter, Element,
                               MEPSystemType,
                               FilteredElementCollector)
from pyrevit import forms, revit


__title__ = u"ПН_ЭОМ_Воздуховоды"
__doc__ = u"Назначает тип системы только воздуховодам по имени их типа."

doc = revit.doc
uidoc = revit.uidoc

SCOPE_PROJECT = u"Весь проект"
SCOPE_SELECTION = u"Только выбранные"

EXACT_TYPE_SYSTEMS = {
    u"SMNX_DT_с_Короб огнезащитный": u"SMNX_Короб огнезащитный",
    u"SMNX_DT_с_Зона прокладки": u"SMNX_Зона прокладки",
    u"SMNX_DT_с_Полоса_Сталь горячеоцинкованная_Вертикальный участок": u"SMNX_Молниезащита",
    u"SMNX_DT_с_Полоса_Сталь горячеоцинкованная_Горизональный участок": u"SMNX_Молниезащита",
    u"SMNX_DT_с_Катанка_Сталь горячеоцинкованная": u"SMNX_Молниезащита",
}
BUSWAY_TYPE_PREFIX = u"SMNX_DT_с_Шинопровод"
BUSWAY_SYSTEM_TYPE = u"SMNX_Шинопровод"
TARGET_SYSTEM_NAMES = set(EXACT_TYPE_SYSTEMS.values())
TARGET_SYSTEM_NAMES.add(BUSWAY_SYSTEM_TYPE)

ALLOWED_CATEGORIES = (
    int(BuiltInCategory.OST_DuctCurves),
)


def _element_id(element):
    return element.Id.IntegerValue


def _is_allowed(element):
    try:
        return int(element.Category.Id.IntegerValue) in ALLOWED_CATEGORIES
    except Exception:
        return False


def _type_name(element):
    try:
        element_type = doc.GetElement(element.GetTypeId())
        return element_type.Name if element_type else None
    except Exception:
        return None


def _normal_name(value):
    """Сопоставляет имена без учёта регистра и неразрывных пробелов."""
    if value is None:
        return u""
    return value.replace(u"\xa0", u" ").strip().lower()


def _target_system_name(type_name):
    if not type_name:
        return None
    normalized_type_name = _normal_name(type_name)
    for rule_name, target_name in EXACT_TYPE_SYSTEMS.items():
        if normalized_type_name == _normal_name(rule_name):
            return target_name
    if normalized_type_name.startswith(_normal_name(BUSWAY_TYPE_PREFIX)):
        return BUSWAY_SYSTEM_TYPE
    return None


def _system_type_parameter(element):
    parameter_ids = [BuiltInParameter.RBS_DUCT_SYSTEM_TYPE_PARAM]
    fallback_id = getattr(BuiltInParameter, "RBS_SYSTEM_TYPE_PARAM", None)
    if fallback_id is not None:
        parameter_ids.append(fallback_id)
    for parameter_id in parameter_ids:
        try:
            parameter = element.get_Parameter(parameter_id)
            if parameter is not None:
                return parameter
        except Exception:
            pass
    return None


def _register_system_type(types, system_type, display_name=None):
    """Добавляет целевой тип под его именем, сокращением или отображением."""
    if system_type is None:
        return
    values = [display_name]
    try:
        values.append(system_type.Name)
    except Exception:
        pass
    try:
        values.append(system_type.Abbreviation)
    except Exception:
        pass
    for value in values:
        try:
            value = value.strip()
            if value in TARGET_SYSTEM_NAMES:
                types[value] = system_type
        except Exception:
            pass


def _system_types_by_name():
    """Находит ID систем в первую очередь по уже назначенным воздуховодам.

    Семейство воздуховода не участвует в правилах: используется только имя
    его типа. Ссылка в параметре «Тип системы» уже содержит подходящий
    ElementId, поэтому этот путь устойчивее class/category-фильтров Revit.
    """
    types = {}
    all_elements = []

    # Сначала используем фактические ссылки «Тип системы» на воздуховодах.
    # Это позволяет получить System Type даже если Revit не выдаёт его через
    # FilteredElementCollector по классу или категории.
    try:
        ducts = (FilteredElementCollector(doc)
                 .OfCategory(BuiltInCategory.OST_DuctCurves)
                 .WhereElementIsNotElementType()
                 .ToElements())
    except Exception:
        ducts = []
    for duct in ducts:
        parameter = _system_type_parameter(duct)
        if parameter is None:
            continue
        try:
            system_type = doc.GetElement(parameter.AsElementId())
            _register_system_type(types, system_type, parameter.AsValueString())
            if system_type:
                all_elements.append(system_type)
        except Exception:
            pass

    if len(types) == len(TARGET_SYSTEM_NAMES):
        return types, all_elements

    # Резервный путь для систем, которые ещё не назначены ни одному воздуховоду.
    try:
        all_elements.extend(FilteredElementCollector(doc)
                        .OfClass(MEPSystemType)
                        .ToElements())
    except Exception:
        pass
    if not all_elements:
        try:
            all_elements = (FilteredElementCollector(doc)
                            .OfClass(Element)
                            .ToElements())
        except Exception:
            try:
                all_elements = (FilteredElementCollector(doc)
                                .WhereElementIsNotElementType()
                                .ToElements())
                all_elements.extend(
                    FilteredElementCollector(doc)
                    .WhereElementIsElementType()
                    .ToElements())
            except Exception:
                all_elements = []
    for system_type in all_elements:
        _register_system_type(types, system_type)
    return types, all_elements


def _similar_smnx_objects(elements):
    """Короткая read-only-диагностика реальных имён объектов проекта."""
    result = []
    seen_ids = set()
    for element in elements:
        try:
            name = element.Name.strip()
            if not name.startswith(u"SMNX_"):
                continue
            element_id = _element_id(element)
            if element_id in seen_ids:
                continue
            seen_ids.add(element_id)
            category_name = element.Category.Name if element.Category else u"без категории"
            class_name = element.GetType().Name
            abbreviation = getattr(element, "Abbreviation", u"")
            result.append(u"{} | ID {} | {} | {} | сокращение: {}".format(
                name, element_id, class_name, category_name, abbreviation))
        except Exception:
            pass
    return sorted(result)[:30]


def _elements_for_scope(scope):
    if scope == SCOPE_SELECTION:
        selected = [doc.GetElement(item_id)
                    for item_id in uidoc.Selection.GetElementIds()]
        return [element for element in selected if element and _is_allowed(element)]

    elements = []
    for category in (BuiltInCategory.OST_DuctCurves,):
        elements.extend(
            FilteredElementCollector(doc).OfCategory(category)
            .WhereElementIsNotElementType().ToElements())
    return elements


def _matching_elements(elements, system_types):
    matches = []
    missing_systems = set()
    for element in elements:
        type_name = _type_name(element)
        target_name = _target_system_name(type_name)
        if target_name is None:
            continue
        target_system = system_types.get(target_name)
        if target_system is None:
            missing_systems.add(target_name)
            continue
        matches.append({
            "element": element,
            "type_name": type_name,
            "target_name": target_name,
            "target_system": target_system,
            "parameter": _system_type_parameter(element),
        })
    return matches, missing_systems


def _processed_types_info(elements):
    """Показывает типы, прочитанные именно у обрабатываемых воздуховодов."""
    values = []
    for element in elements:
        type_name = _type_name(element)
        values.append(u"ID {} | тип: {}".format(
            _element_id(element), type_name or u"не определён"))
    return values[:30]


scope = forms.alert(
    u"Где назначить типы систем?",
    title=__title__,
    sub_msg=u"Изменятся только воздуховоды с заданными именами типов. Соединительные детали наследуют их систему.",
    ok=False,
    options=[SCOPE_PROJECT, SCOPE_SELECTION],
    warn_icon=False)
if not scope:
    forms.alert(u"Операция отменена.", title=__title__, exitscript=True)

elements = _elements_for_scope(scope)
if scope == SCOPE_SELECTION and not elements:
    forms.alert(
        u"В выделении нет воздуховодов.",
        title=__title__, exitscript=True)

system_types, all_document_elements = _system_types_by_name()
if not system_types:
    similar_objects = _similar_smnx_objects(all_document_elements)
    diagnostic = u""
    if similar_objects:
        diagnostic = (u"\n\nНайдены объекты с похожими именами:\n{}"
                      .format(u"\n".join(similar_objects)))
    forms.alert(
        u"В проекте не найдены целевые типы систем с именами из правил.\n\n"
        u"Проверка типов систем не зависит от выбранного охвата.{}".format(diagnostic),
        title=__title__, exitscript=True)

matches, missing_systems = _matching_elements(elements, system_types)
if missing_systems:
    forms.alert(
        u"В проекте не найдены целевые типы систем:\n\n{}\n\nИзменения не выполнялись."
        .format(u"\n".join(sorted(missing_systems))),
        title=__title__, exitscript=True)

if not matches:
    actual_types = _processed_types_info(elements)
    forms.alert(
        u"Среди обработанных элементов нет типов с заданными правилами.\n\n"
        u"Охват: {}\n\nПрочитанные имена типов:\n{}"
        .format(scope, u"\n".join(actual_types) if actual_types else u"нет элементов"),
        title=__title__, exitscript=True)

ready = []
already_assigned = 0
read_only = []
for match in matches:
    parameter = match["parameter"]
    if parameter is None or parameter.IsReadOnly:
        read_only.append(match)
        continue
    try:
        if parameter.AsElementId() == match["target_system"].Id:
            already_assigned += 1
            continue
    except Exception:
        pass
    ready.append(match)

summary = [
    u"Охват: {}".format(scope),
    u"Элементов с правилами: {}".format(len(matches)),
    u"Будет изменено: {}".format(len(ready)),
    u"Уже назначен нужный тип: {}".format(already_assigned),
    u"Недоступен параметр типа системы: {}".format(len(read_only)),
]
if not forms.alert(u"\n".join(summary) + u"\n\nВыполнить назначение?",
                   title=__title__, yes=True, no=True, warn_icon=True):
    forms.alert(u"Операция отменена.", title=__title__, exitscript=True)

changed = 0
errors = []
with revit.Transaction(u"ПН ЭОМ — типы систем воздуховодов"):
    for match in ready:
        try:
            match["parameter"].Set(match["target_system"].Id)
            changed += 1
        except Exception as error:
            errors.append(
                u"ID {} | {}: {}".format(
                    _element_id(match["element"]),
                    match["type_name"], unicode(error)))

result = [
    u"Изменено: {}".format(changed),
    u"Уже было назначено: {}".format(already_assigned),
    u"Пропущено из-за недоступного параметра: {}".format(len(read_only)),
]
if errors:
    result.append(u"Ошибки:\n{}".format(u"\n".join(errors[:20])))
forms.alert(u"\n".join(result), title=__title__)
