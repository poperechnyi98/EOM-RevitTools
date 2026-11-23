# -*- coding: utf-8 -*-
from Autodesk.Revit.DB import ElementId, LocationCurve, LocationPoint, XYZ
from System.Collections.Generic import List
from pyrevit import forms, revit


__title__ = u"Зум к\nэлементу"
__doc__ = u"Приближает вид к выделенному элементу или элементу по ID."

doc = revit.doc
uidoc = revit.uidoc
view = doc.ActiveView


def _ask_for_element():
    value = forms.ask_for_string(
        default=u"", prompt=u"Введите ID элемента:", title=u"Зум к элементу")
    if value is None or not value.strip():
        forms.alert(u"ID не введён.", exitscript=True)
    try:
        element = doc.GetElement(ElementId(int(value.strip())))
    except Exception:
        element = None
    if element is None:
        forms.alert(u"Элемент с таким ID не найден в текущей модели.", exitscript=True)
    return element


def _target_point(element):
    location = getattr(element, "Location", None)
    if isinstance(location, LocationPoint):
        return location.Point
    if isinstance(location, LocationCurve):
        return location.Curve.Evaluate(0.5, True)
    bbox = element.get_BoundingBox(view) or element.get_BoundingBox(None)
    if bbox is None:
        return None
    midpoint = XYZ((bbox.Min.X + bbox.Max.X) / 2.0,
                   (bbox.Min.Y + bbox.Max.Y) / 2.0,
                   (bbox.Min.Z + bbox.Max.Z) / 2.0)
    try:
        return bbox.Transform.OfPoint(midpoint)
    except Exception:
        return midpoint


def _zoom(points):
    open_view = None
    for ui_view in uidoc.GetOpenUIViews():
        if ui_view.ViewId == view.Id:
            open_view = ui_view
            break
    if open_view is None:
        forms.alert(u"Откройте обычный вид модели, а не лист, и повторите команду.",
                    exitscript=True)

    min_x = min(point.X for point in points)
    max_x = max(point.X for point in points)
    min_y = min(point.Y for point in points)
    max_y = max(point.Y for point in points)
    z = sum(point.Z for point in points) / len(points)
    padding = 6.0
    open_view.ZoomAndCenterRectangle(
        XYZ(min_x - padding, min_y - padding, z),
        XYZ(max_x + padding, max_y + padding, z))
    uidoc.RefreshActiveView()


elements = [doc.GetElement(item_id) for item_id in uidoc.Selection.GetElementIds()]
elements = [element for element in elements if element]
if not elements:
    elements = [_ask_for_element()]
    selected_ids = List[ElementId]()
    selected_ids.Add(elements[0].Id)
    uidoc.Selection.SetElementIds(selected_ids)

points = [_target_point(element) for element in elements]
points = [point for point in points if point]
if not points:
    forms.alert(u"Не удалось определить положение выбранного элемента на этом виде.",
                title=u"Зум к элементу")
else:
    _zoom(points)
