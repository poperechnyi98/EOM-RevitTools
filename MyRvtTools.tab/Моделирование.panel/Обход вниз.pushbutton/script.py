# -*- coding: utf-8 -*-
"""Обход препятствия на прямом кабельном лотке."""
from Autodesk.Revit.DB import (BuiltInCategory, BuiltInParameter, ElementId,
                               XYZ)
from Autodesk.Revit.DB.Electrical import CableTray
from Autodesk.Revit.DB.Mechanical import Duct
from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType
from pyrevit import forms, revit


__title__ = u"Обход\nвниз"
__doc__ = u"Создаёт обход на лотке или воздуховоде: -200 мм, низ 300 мм, переходы 45°."

doc = revit.doc
uidoc = revit.uidoc
MM = 1.0 / 304.8
RISE = 200.0 * MM
TOP_LENGTH = 300.0 * MM
SIZE_PARAMETER_NAMES = (
    "RBS_CABLETRAY_WIDTH_PARAM",
    "RBS_CABLETRAY_HEIGHT_PARAM",
    "RBS_CURVE_WIDTH_PARAM",
    "RBS_CURVE_HEIGHT_PARAM",
    "RBS_CURVE_DIAMETER_PARAM",
)


def _is_tray(element):
    try:
        return (int(element.Category.Id.IntegerValue) ==
                int(BuiltInCategory.OST_CableTray))
    except Exception:
        return False


def _is_duct(element):
    try:
        return (int(element.Category.Id.IntegerValue) ==
                int(BuiltInCategory.OST_DuctCurves))
    except Exception:
        return False


def _is_supported(element):
    return _is_tray(element) or _is_duct(element)


class TrayPointFilter(ISelectionFilter):
    def AllowElement(self, element):
        return _is_supported(element)

    def AllowReference(self, reference, point):
        return True


def _connectors(element):
    try:
        return [connector for connector in element.ConnectorManager.Connectors]
    except Exception:
        return []


def _nearest_connector(element, point):
    connectors = _connectors(element)
    if not connectors:
        return None
    return min(connectors, key=lambda connector: connector.Origin.DistanceTo(point))


def _level_id(tray):
    try:
        return tray.ReferenceLevel.Id
    except Exception:
        parameter = tray.get_Parameter(BuiltInParameter.RBS_START_LEVEL_PARAM)
        return parameter.AsElementId() if parameter else ElementId.InvalidElementId


def _duct_system_type_id(duct):
    try:
        if duct.MEPSystem is not None:
            return duct.MEPSystem.GetTypeId()
    except Exception:
        pass
    parameter = duct.get_Parameter(BuiltInParameter.RBS_DUCT_SYSTEM_TYPE_PARAM)
    return parameter.AsElementId() if parameter else ElementId.InvalidElementId


def _create_segment(source, source_type_id, level_id, start_point, end_point):
    if _is_tray(source):
        return CableTray.Create(doc, source_type_id, start_point, end_point, level_id)
    system_type_id = _duct_system_type_id(source)
    if system_type_id == ElementId.InvalidElementId:
        raise Exception(u"Не удалось определить тип системы воздуховода.")
    return Duct.Create(doc, system_type_id, source_type_id, level_id,
                       start_point, end_point)


def _size_values(element):
    values = []
    for parameter_name in SIZE_PARAMETER_NAMES:
        parameter_id = getattr(BuiltInParameter, parameter_name, None)
        if parameter_id is None:
            continue
        try:
            parameter = element.get_Parameter(parameter_id)
            if parameter and parameter.HasValue:
                values.append((parameter_id, parameter.AsDouble()))
        except Exception:
            pass
    return values


def _apply_size_values(element, values):
    for parameter_id, value in values:
        try:
            parameter = element.get_Parameter(parameter_id)
            if parameter and not parameter.IsReadOnly:
                parameter.Set(value)
        except Exception:
            pass


selected = [doc.GetElement(item_id) for item_id in uidoc.Selection.GetElementIds()]
selected = [element for element in selected if element and _is_supported(element)]
if len(selected) != 1:
    forms.alert(u"Выберите один прямой кабельный лоток или воздуховод.",
                title=u"Разорвать ЭОМ", exitscript=True)

tray = selected[0]
try:
    source_curve = tray.Location.Curve
    start = source_curve.GetEndPoint(0)
    end = source_curve.GetEndPoint(1)
    raw_direction = end.Subtract(start)
    direction = XYZ(raw_direction.X, raw_direction.Y, 0.0)
    if direction.GetLength() < 0.000001 or abs(raw_direction.Z) > 0.001:
        raise Exception()
    direction = direction.Normalize()
except Exception:
    forms.alert(u"Команда работает только с прямым горизонтальным лотком или воздуховодом.",
                title=u"Разорвать ЭОМ", exitscript=True)

try:
    point_reference = uidoc.Selection.PickObject(
        ObjectType.PointOnElement,
        TrayPointFilter(),
        u"Щёлкните по выбранному лотку в центре будущего обхода.")
    centre = source_curve.Project(point_reference.GlobalPoint).XYZPoint
except Exception:
    forms.alert(u"Точка обхода не выбрана.", title=u"Разорвать ЭОМ", exitscript=True)

half_width = TOP_LENGTH * 0.5 + RISE
if (centre.DistanceTo(start) <= half_width or
        centre.DistanceTo(end) <= half_width):
    forms.alert(
        u"Недостаточно длины лотка вокруг выбранной точки. Нужно минимум 350 мм до каждого конца.",
        title=u"Разорвать ЭОМ", exitscript=True)

left_low = centre.Subtract(direction.Multiply(half_width))
left_top = left_low.Add(direction.Multiply(RISE)).Subtract(XYZ(0, 0, RISE))
right_top = left_top.Add(direction.Multiply(TOP_LENGTH))
right_low = right_top.Add(direction.Multiply(RISE)).Add(XYZ(0, 0, RISE))

tray_type_id = tray.GetTypeId()
level_id = _level_id(tray)
original_sizes = _size_values(tray)
if level_id == ElementId.InvalidElementId:
    forms.alert(u"Не удалось определить уровень выбранного лотка.",
                title=u"Разорвать ЭОМ", exitscript=True)

try:
    with revit.Transaction(u"Обход препятствия кабельного лотка"):
        left = _create_segment(tray, tray_type_id, level_id, start, left_low)
        rise = _create_segment(tray, tray_type_id, level_id, left_low, left_top)
        top = _create_segment(tray, tray_type_id, level_id, left_top, right_top)
        fall = _create_segment(tray, tray_type_id, level_id, right_top, right_low)
        right = _create_segment(tray, tray_type_id, level_id, right_low, end)

        for new_element in (left, rise, top, fall, right):
            _apply_size_values(new_element, original_sizes)

        for first, point, second in ((left, left_low, rise),
                                     (rise, left_top, top),
                                     (top, right_top, fall),
                                     (fall, right_low, right)):
            first_connector = _nearest_connector(first, point)
            second_connector = _nearest_connector(second, point)
            if first_connector is None or second_connector is None:
                raise Exception(u"Не найдены коннекторы нового лотка.")
            doc.Create.NewElbowFitting(first_connector, second_connector)

        doc.Delete(tray.Id)
except Exception as error:
    forms.alert(
        u"Обход не создан. Проверьте, что для типа лотка настроены фитинги 45°.\n\n{}".format(
            unicode(error)), title=u"Разорвать ЭОМ")
