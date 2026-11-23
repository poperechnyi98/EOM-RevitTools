# -*- coding: utf-8 -*-
from Autodesk.Revit.DB import BuiltInParameter, DimensionStyleType, DimensionType, FilteredElementCollector, Grid, Line, Reference, ReferenceArray, XYZ
from pyrevit import forms, revit


__title__ = u"Размеры\nосей"
__doc__ = u"Создаёт размерные цепочки для выделенных осей."

doc = revit.doc
uidoc = revit.uidoc
view = doc.ActiveView
MM_IN_FOOT = 304.8
OFFSET = 1400.0 / MM_IN_FOOT
TOTAL_OFFSET = 2100.0 / MM_IN_FOOT


class DimensionTypeOption(object):
    def __init__(self, dimension_type):
        self.dimension_type = dimension_type
        name_parameter = dimension_type.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM)
        self.name = name_parameter.AsString() if name_parameter else u"Тип размера {}".format(dimension_type.Id.IntegerValue)

    def __str__(self):
        return self.name


def _grids():
    result = []
    for item_id in uidoc.Selection.GetElementIds():
        element = doc.GetElement(item_id)
        if isinstance(element, Grid):
            result.append(element)
    return result


def _is_vertical(grid):
    first = grid.Curve.GetEndPoint(0)
    last = grid.Curve.GetEndPoint(1)
    return abs(last.Y - first.Y) >= abs(last.X - first.X)


def _sorted(grids, vertical):
    if vertical:
        return sorted(grids, key=lambda grid: (grid.Curve.GetEndPoint(0).X + grid.Curve.GetEndPoint(1).X) / 2.0)
    return sorted(grids, key=lambda grid: (grid.Curve.GetEndPoint(0).Y + grid.Curve.GetEndPoint(1).Y) / 2.0)


def _dimension_type():
    types = []
    for item in FilteredElementCollector(doc).OfClass(DimensionType):
        try:
            if item.StyleType == DimensionStyleType.Linear:
                types.append(DimensionTypeOption(item))
        except Exception:
            pass
    choice = forms.SelectFromList.show(sorted(types, key=lambda item: unicode(item)),
                                       title=u"Стиль размеров", button_name=u"Создать", multiselect=False)
    return choice.dimension_type if choice else None


def _placement_side():
    choice = forms.SelectFromList.show(
        [u"Сверху", u"Снизу"],
        title=u"Расположение размеров осей",
        button_name=u"Создать",
        multiselect=False)
    if choice is None:
        return None
    return choice == u"Сверху"


def _create(grids, vertical, offset, dimension_type, place_top):
    if len(grids) < 2:
        return None
    references = ReferenceArray()
    for grid in grids:
        references.Append(Reference(grid))
    points = [grid.Curve.GetEndPoint(index) for grid in grids for index in (0, 1)]
    z = points[0].Z
    if vertical:
        values = [(grid.Curve.GetEndPoint(0).X + grid.Curve.GetEndPoint(1).X) / 2.0 for grid in grids]
        boundary = (max(point.Y for point in points) if place_top
                    else min(point.Y for point in points))
        y = boundary + offset if place_top else boundary - offset
        line = Line.CreateBound(XYZ(min(values), y, z), XYZ(max(values), y, z))
    else:
        values = [(grid.Curve.GetEndPoint(0).Y + grid.Curve.GetEndPoint(1).Y) / 2.0 for grid in grids]
        boundary = (max(point.X for point in points) if place_top
                    else min(point.X for point in points))
        x = boundary + offset if place_top else boundary - offset
        line = Line.CreateBound(XYZ(x, min(values), z), XYZ(x, max(values), z))
    dimension = doc.Create.NewDimension(view, line, references)
    dimension.ChangeTypeId(dimension_type.Id)
    return dimension


selected = _grids()
if len(selected) < 2:
    forms.alert(u"Выберите минимум две оси на активном виде.", exitscript=True)

dimension_type = _dimension_type()
if dimension_type is None:
    forms.alert(u"Операция отменена.", exitscript=True)

place_top = _placement_side()
if place_top is None:
    forms.alert(u"Операция отменена.", exitscript=True)

vertical = _sorted([grid for grid in selected if _is_vertical(grid)], True)
horizontal = _sorted([grid for grid in selected if not _is_vertical(grid)], False)
created = 0
with revit.Transaction(u"Размеры осей"):
    for group, is_vertical in ((vertical, True), (horizontal, False)):
        if _create(group, is_vertical, OFFSET, dimension_type, place_top):
            created += 1
        if len(group) >= 3:
            _create([group[0], group[-1]], is_vertical, TOTAL_OFFSET,
                    dimension_type, place_top)
            created += 1

forms.alert(u"Создано размеров: {}".format(created), title=u"Размеры осей")
