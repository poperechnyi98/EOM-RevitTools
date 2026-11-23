# -*- coding: utf-8 -*-
"""Project cable sections into the active Revit view and place detail components."""
from math import pi, sqrt

from Autodesk.Revit import DB
from Autodesk.Revit.Exceptions import InvalidOperationException, OperationCanceledException
from Autodesk.Revit.UI.Selection import ObjectSnapTypes

from .layout import fill_label, tray_outline
from .section_data import TRAY_THICKNESS

FAMILY_NAME = u'SMNX_DC_э_Сечение кабеля'
TITLE = u'Сечение лотков'
_POSITION_PARAMETER = u'SMNX_Номер позиции'
_TEXT_TYPE = u'SMNX_2.2'
_PARAMETERS = ((u'Диаметр', 'diameter', DB.UnitTypeId.Millimeters),
               (u'Количество жил', 'cores', None),
               (u'Сечение проводника', 'section', DB.UnitTypeId.SquareMillimeters))


def is_section(view):
    return (isinstance(view, DB.ViewSection) and not view.IsTemplate
            and view.ViewType == DB.ViewType.Section)


def find_symbol(doc):
    symbols = DB.FilteredElementCollector(doc).OfClass(DB.FamilySymbol).OfCategory(
        DB.BuiltInCategory.OST_DetailComponents)
    matches = [symbol for symbol in symbols
               if symbol.Family.Name == FAMILY_NAME
               and symbol.Family.FamilyPlacementType == DB.FamilyPlacementType.ViewBased]
    if not matches:
        raise ValueError(u'Загрузите в проект семейство {}.'.format(FAMILY_NAME))
    named = [(symbol.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM).AsString() or u'',
              symbol) for symbol in matches]
    return min(named, key=lambda item: (item[0] != FAMILY_NAME, item[0]))[1]


def find_resources(doc):
    symbol = find_symbol(doc)
    category = DB.Category.GetCategory(doc, DB.BuiltInCategory.OST_CurvesThinLines)
    line_style = category.GetGraphicsStyle(DB.GraphicsStyleType.Projection) if category else None
    if line_style is None:
        raise ValueError(u'В проекте не найден стиль линий «<Тонкие линии>».')
    text_types = DB.FilteredElementCollector(doc).OfClass(DB.TextNoteType)
    text_type = next((item for item in text_types
                      if DB.Element.Name.GetValue(item) == _TEXT_TYPE), None)
    if text_type is None:
        raise ValueError(u'В проекте не найден тип текста «{}».'.format(_TEXT_TYPE))
    return symbol, line_style, text_type.Id


def _pick(selection):
    snaps = ObjectSnapTypes.Endpoints | ObjectSnapTypes.Intersections
    return selection.PickPoint(snaps, u'Укажите наружный нижний левый угол лотка')


def _point_on_temporary_plane(uidoc, view):
    doc = uidoc.Document
    group = DB.TransactionGroup(doc, TITLE)
    group.Start()
    try:
        name = u'Рабочая плоскость разреза'
        tx = DB.Transaction(doc, name)
        tx.Start()
        try:
            plane = DB.Plane.CreateByNormalAndOrigin(view.ViewDirection, view.Origin)
            view.SketchPlane = DB.SketchPlane.Create(doc, plane)
            if tx.Commit() != DB.TransactionStatus.Committed:
                raise ValueError(u'Не удалось подготовить плоскость разреза.')
        finally:
            if tx.GetStatus() == DB.TransactionStatus.Started:
                tx.RollBack()
        return _pick(uidoc.Selection)
    finally:
        group.RollBack()


def pick_origin(uidoc, view):
    try:
        try:
            point = _pick(uidoc.Selection)
        except InvalidOperationException:
            point = _point_on_temporary_plane(uidoc, view)
    except OperationCanceledException:
        return None
    normal = view.ViewDirection
    return point - normal.Multiply((point - view.Origin).DotProduct(normal))


def _writable_parameter(element, name):
    writable = [param for param in element.GetParameters(name) if not param.IsReadOnly]
    if len(writable) != 1:
        raise ValueError(u'В семействе нужен один доступный параметр экземпляра «{}».'.format(name))
    return writable[0]


def _set_position(element, position):
    parameter = _writable_parameter(element, _POSITION_PARAMETER)
    if parameter.StorageType != DB.StorageType.String:
        raise ValueError(u'Параметр «{}» должен быть текстовым.'.format(_POSITION_PARAMETER))
    if not parameter.Set(position) and parameter.AsString() != position:
        raise ValueError(u'Не удалось заполнить параметр «{}».'.format(_POSITION_PARAMETER))


def _set_parameter(element, name, raw, unit):
    parameter = _writable_parameter(element, name)
    storage = parameter.StorageType
    if storage == DB.StorageType.Double:
        spec = parameter.Definition.GetDataType()
        if spec == DB.SpecTypeId.Number:
            value = float(raw)
        elif (unit is not None and DB.UnitUtils.IsMeasurableSpec(spec)
              and DB.UnitUtils.IsValidUnit(spec, unit)):
            value = DB.UnitUtils.ConvertToInternalUnits(float(raw), unit)
        else:
            raise ValueError(u'Неподдерживаемые единицы параметра «{}».'.format(name))
    elif storage == DB.StorageType.Integer and parameter.Definition.GetDataType() != DB.SpecTypeId.Boolean.YesNo:
        if int(raw) != raw:
            raise ValueError(u'Параметр «{}» принимает только целое число.'.format(name))
        value = int(raw)
    else:
        raise ValueError(u'Неподдерживаемый тип параметра «{}».'.format(name))
    if not parameter.Set(value):
        current = parameter.AsDouble() if storage == DB.StorageType.Double else parameter.AsInteger()
        if abs(current - value) > 1.0e-9:
            raise ValueError(u'Не удалось записать параметр «{}».'.format(name))


def _arcs(geometry):
    if geometry is None:
        return
    for item in geometry:
        if isinstance(item, DB.Arc):
            yield item
        elif isinstance(item, DB.GeometryInstance):
            for arc in _arcs(item.GetInstanceGeometry()):
                yield arc


def _outline_center(instance, view, options, point, diameter):
    tolerance = max(_feet(0.1), diameter * 0.015)
    actual = [arc for arc in _arcs(instance.get_Geometry(options))
              if abs(2.0 * arc.Radius - diameter) <= tolerance
              and abs(arc.Normal.DotProduct(view.ViewDirection)) > 0.999]
    if actual:
        return min(actual, key=lambda arc: (not arc.IsCyclic, arc.Center.DistanceTo(point))).Center

    box = instance.get_BoundingBox(view)
    if box is None:
        return None
    low, high = box.Transform.OfPoint(box.Min), box.Transform.OfPoint(box.Max)
    sizes = (abs(high.X - low.X), abs(high.Y - low.Y), abs(high.Z - low.Z))
    normal = view.ViewDirection
    expected = [diameter * sqrt(max(0.0, 1.0 - component * component))
                for component in (normal.X, normal.Y, normal.Z)]
    if all(abs(size - extent) <= tolerance for size, extent in zip(sizes, expected)):
        return (low + high).Multiply(0.5)
    return None


def _feet(mm):
    return DB.UnitUtils.ConvertToInternalUnits(mm, DB.UnitTypeId.Millimeters)


def _draw_tray(doc, view, point, section, line_style, text_type, legend):
    for start, end in tray_outline(section):
        line = DB.Line.CreateBound(point(*start), point(*end))
        detail = doc.Create.NewDetailCurve(view, line)
        detail.LineStyle = line_style

    paper_gap = 2.0 * view.Scale
    percent_options = DB.TextNoteOptions(text_type)
    percent_options.Rotation = pi / 2.0
    percent_options.KeepRotatedTextReadable = False
    percent_options.HorizontalAlignment = DB.HorizontalTextAlignment.Center
    percent_options.VerticalAlignment = DB.VerticalTextAlignment.Bottom
    center = (section.support_height + section.height) / 2.0
    DB.TextNote.Create(doc, view.Id, point(-paper_gap, center),
                       fill_label(section.fill_percent), percent_options)
    DB.TextNote.Create(doc, view.Id, point(0.0, -paper_gap), legend, text_type)


def place(doc, view, origin, section, layout, resources, legend):
    symbol, line_style, text_type = resources
    direction = view.RightDirection
    up = view.UpDirection

    def point(x, y):
        return origin + direction.Multiply(_feet(x)) + up.Multiply(_feet(y))

    tx = DB.Transaction(doc, TITLE)
    tx.Start()
    try:
        if not symbol.IsActive:
            symbol.Activate()
            doc.Regenerate()
        _draw_tray(doc, view, point, section, line_style, text_type, legend)
        instances = []
        for item in layout.cables:
            target = point(item.x + TRAY_THICKNESS, item.y + section.support_height)
            instance = doc.Create.NewFamilyInstance(target, symbol, view)
            for name, field, units in _PARAMETERS:
                _set_parameter(instance, name, getattr(item.cable, field), units)
            _set_position(instance, item.position)
            instances.append((instance, item.cable, target))
        doc.Regenerate()

        options = DB.Options()
        options.View = view
        offsets = {}
        unchecked_centres = 0
        for instance, cable, target in instances:
            shape = (cable.diameter, cable.cores, cable.section)
            if shape not in offsets:
                center = _outline_center(instance, view, options, target, _feet(cable.diameter))
                offset = None
                if center is not None:
                    move = target - center
                    offset = direction.Multiply(move.DotProduct(direction)) + up.Multiply(move.DotProduct(up))
                offsets[shape] = offset
            offset = offsets[shape]
            if offset is None:
                unchecked_centres += 1
            elif offset.GetLength() > _feet(0.01):
                DB.ElementTransformUtils.MoveElement(doc, instance.Id, offset)

        if tx.Commit() != DB.TransactionStatus.Committed:
            raise ValueError(u'Revit отменил создание сечения.')
        return unchecked_centres
    finally:
        if tx.GetStatus() == DB.TransactionStatus.Started:
            tx.RollBack()
