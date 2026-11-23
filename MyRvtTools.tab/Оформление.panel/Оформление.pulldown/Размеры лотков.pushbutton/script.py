# -*- coding: utf-8 -*-
"""Размеры выбранных кабельных лотков и фитингов.

Работает только с объектами, выбранными пользователем. Центральные линии,
оси и невыбранные соседние элементы намеренно не используются.
"""
import codecs
import json
import os

from Autodesk.Revit.DB import (BuiltInCategory, BuiltInParameter,
                               Dimension, DimensionStyleType, DimensionType,
                               Edge, ElementId,
                               FamilyInstanceReferenceType,
                               FilteredElementCollector, GeometryInstance,
                               Line, Options, PlanarFace, Reference, ReferenceArray,
                               Solid, Transform, ViewType, XYZ)
from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType
from pyrevit import forms, revit, script


__title__ = u"Размеры\nлотков"
__doc__ = u"Создаёт размеры по концам прямых кабельных лотков. Фитинги сохраняют непрерывность цепочки, но не дают опорных граней."

doc = revit.doc
uidoc = revit.uidoc
view = doc.ActiveView

MM_IN_FOOT = 304.8
ANGLE_TOLERANCE = 30.0
COS_30 = 0.8660254037844386
# Для привязок одного линейного размера грани должны быть практически строго
# параллельны. Порог 30° применяется только к классификации направлений.
FACE_PARALLEL_COS = 0.9998
COORDINATE_TOLERANCE = 1.0 / MM_IN_FOOT
STATION_TOLERANCE = 10.0 / MM_IN_FOOT
TURN_CONNECTOR_TOLERANCE = 300.0 / MM_IN_FOOT
# Some fabricated tray families place their exposed end face far from the
# MEP connector and omit Connector.AllRefs.  This value is used only by the
# controlled geometric fallback that locates the nearest fitting.
FITTING_FALLBACK_TOLERANCE = 2500.0 / MM_IN_FOOT
CONNECTOR_REFERENCE_TOLERANCE = 20.0 / MM_IN_FOOT
RUN_ALIGNMENT_TOLERANCE = 20.0 / MM_IN_FOOT
SLOPE_TOLERANCE = 1.0 / MM_IN_FOOT
STABLE_SURFACE_SCAN_LIMIT = 64
BASE_OFFSET = 500.0 / MM_IN_FOOT
LINE_EXTENSION = 150.0 / MM_IN_FOOT
DIMENSION_SHELF_STEP = 350.0 / MM_IN_FOOT
DIMENSION_SHELF_ATTEMPTS = 10
LAYOUT_TOLERANCE = 5.0 / MM_IN_FOOT
turn_reference_trace = []
fitting_lookup_trace = []
context_fittings_cache = None
ALLOWED_CATEGORIES = (
    int(BuiltInCategory.OST_CableTray),
    int(BuiltInCategory.OST_CableTrayFitting),
)
CALIBRATION_FILE_NAME = "fitting_reference_calibrations.json"


class TraySelectionFilter(ISelectionFilter):
    def AllowElement(self, element):
        return _is_allowed(element)

    def AllowReference(self, reference, point):
        return False


class DimensionTypeOption(object):
    def __init__(self, dimension_type):
        self.dimension_type = dimension_type
        parameter = dimension_type.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM)
        value = parameter.AsString() if parameter else None
        self.name = value or u"Тип размера {}".format(dimension_type.Id.IntegerValue)

    def __str__(self):
        return self.name


def _element_id(element):
    return element.Id.IntegerValue


def _extension_root():
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _calibration_path():
    return os.path.join(_extension_root(), CALIBRATION_FILE_NAME)


def _calibration_key(fitting):
    try:
        document_key = doc.PathName or doc.Title
        return u"{}|{}".format(document_key, fitting.GetTypeId().IntegerValue)
    except Exception:
        return None


def _load_calibrations():
    try:
        with codecs.open(_calibration_path(), "r", "utf-8") as stream:
            return json.loads(stream.read())
    except Exception:
        return {}


def _calibrated_fitting_reference(fitting, direction):
    """Build a known-good fitting Reference captured from a manual dimension."""
    key = _calibration_key(fitting)
    entry = _load_calibrations().get(key) if key else None
    if not entry:
        return None
    try:
        stable = u"{}:0:INSTANCE:{}".format(fitting.UniqueId, entry["suffix"])
        reference = Reference.ParseFromStableRepresentation(doc, stable)
    except Exception:
        return None
    try:
        bounding_box = fitting.get_BoundingBox(view) or fitting.get_BoundingBox(None)
        points = [XYZ(x, y, z)
                  for x in (bounding_box.Min.X, bounding_box.Max.X)
                  for y in (bounding_box.Min.Y, bounding_box.Max.Y)
                  for z in (bounding_box.Min.Z, bounding_box.Max.Z)]
        point = max(points, key=lambda item: item.DotProduct(direction))
    except Exception:
        point = _connector_points(fitting)[0] if _connector_points(fitting) else XYZ.Zero
    return {
        "coordinate": point.DotProduct(direction),
        "reference": reference,
        "point": point,
        "start": point,
        "end": point,
        "element": fitting,
    }


def _is_allowed(element):
    try:
        return int(element.Category.Id.IntegerValue) in ALLOWED_CATEGORIES
    except Exception:
        return False


def _is_straight_route(element):
    """Only ordinary straight tray segments take part in plan dimensions."""
    try:
        return isinstance(element.Location.Curve, Line)
    except Exception:
        return False


def _is_fitting(element):
    try:
        return int(element.Category.Id.IntegerValue) == int(BuiltInCategory.OST_CableTrayFitting)
    except Exception:
        return False


def _active_view_is_supported():
    # Высотные изменения трассы проверяются по разрезу, но размеры всегда
    # создаются только на активном плане в его проекции.
    return view.ViewType in (ViewType.FloorPlan, ViewType.EngineeringPlan)


def _selected_or_picked_elements():
    selected = [doc.GetElement(item_id) for item_id in uidoc.Selection.GetElementIds()]
    selected = [element for element in selected if element]
    eligible = [element for element in selected if _is_allowed(element)]

    if eligible:
        return eligible

    if selected:
        forms.alert(
            u"В текущем выделении нет прямых кабельных лотков.",
            title=u"Размеры лотков", exitscript=True)

    try:
        references = uidoc.Selection.PickObjects(
            ObjectType.Element,
            TraySelectionFilter(),
            u"Выберите кабельные лотки и фитинги. Размеры ставятся по прямым лоткам; фитинги сохраняют непрерывность цепочки. Завершите выбор кнопкой «Готово»."
        )
    except Exception:
        forms.alert(u"Выбор отменён.", title=u"Размеры лотков", exitscript=True)

    elements = []
    seen = set()
    for reference in references:
        element = doc.GetElement(reference.ElementId)
        if element and _is_allowed(element) and _element_id(element) not in seen:
            elements.append(element)
            seen.add(_element_id(element))
    return elements


def _connectors(element):
    manager = None
    try:
        manager = element.ConnectorManager
    except Exception:
        pass
    if manager is None:
        try:
            manager = element.MEPModel.ConnectorManager
        except Exception:
            pass
    if manager is None:
        return []
    try:
        return [connector for connector in manager.Connectors]
    except Exception:
        return []


def _connected_groups(elements):
    by_id = dict((_element_id(element), element) for element in elements)
    neighbours = dict((element_id, set()) for element_id in by_id)

    for element in elements:
        element_id = _element_id(element)
        for connector in _connectors(element):
            try:
                related = connector.AllRefs
            except Exception:
                continue
            for related_connector in related:
                try:
                    related_id = _element_id(related_connector.Owner)
                except Exception:
                    continue
                if related_id in by_id and related_id != element_id:
                    neighbours[element_id].add(related_id)
                    neighbours[related_id].add(element_id)

    groups = []
    remaining = set(by_id)
    while remaining:
        start = remaining.pop()
        stack = [start]
        group_ids = []
        while stack:
            current = stack.pop()
            group_ids.append(current)
            for neighbour in neighbours[current]:
                if neighbour in remaining:
                    remaining.remove(neighbour)
                    stack.append(neighbour)
        groups.append([by_id[element_id] for element_id in group_ids])
    return groups


def _project_to_view_plane(vector):
    projected = vector.Subtract(view.ViewDirection.Multiply(vector.DotProduct(view.ViewDirection)))
    if projected.GetLength() < 0.000001:
        return None
    return projected.Normalize()


def _canonical_axis(vector):
    vector = _project_to_view_plane(vector)
    if vector is None:
        return None
    right = vector.DotProduct(view.RightDirection)
    up = vector.DotProduct(view.UpDirection)
    if right < -0.000001 or (abs(right) <= 0.000001 and up < 0.0):
        return vector.Multiply(-1.0)
    return vector


def _same_axis(first, second):
    return abs(first.DotProduct(second)) >= COS_30


def _append_axis(target, axis):
    axis = _canonical_axis(axis)
    if axis is None:
        return
    if not any(_same_axis(axis, existing) for existing in target):
        target.append(axis)


def _location_axes(element):
    axes = []
    try:
        curve = element.Location.Curve
        _append_axis(axes, curve.GetEndPoint(1).Subtract(curve.GetEndPoint(0)))
    except Exception:
        pass
    return axes



def _connector_axes(element):
    axes = []
    for connector in _connectors(element):
        try:
            _append_axis(axes, connector.CoordinateSystem.BasisZ)
        except Exception:
            pass
    return axes


def _element_axes(element):
    axes = _location_axes(element)
    # Для лотков LocationCurve надёжнее осей коннекторов. Коннекторы нужны
    # как запасной источник направления только для самостоятельных фитингов.
    return axes if axes else _connector_axes(element)


def _group_axes(group):
    axes = []
    for element in group:
        for axis in _location_axes(element):
            _append_axis(axes, axis)
    if axes:
        return axes

    for element in group:
        for axis in _connector_axes(element):
            _append_axis(axes, axis)
    return axes


def _connected_owner_ids(element):
    owners = set()
    for connector in _connectors(element):
        try:
            related = connector.AllRefs
        except Exception:
            continue
        for related_connector in related:
            try:
                owners.add(_element_id(related_connector.Owner))
            except Exception:
                pass
    return owners


def _run_coordinate(element, axis):
    curve = element.Location.Curve
    midpoint = curve.GetEndPoint(0).Add(curve.GetEndPoint(1)).Multiply(0.5)
    perpendicular = view.ViewDirection.CrossProduct(axis).Normalize()
    return midpoint.DotProduct(perpendicular)


def _runs_for_axis(group, axis):
    """Разделяет параллельные ветви на независимые размерные цепочки."""
    # При перепаде отметки трасса остаётся прямой на плане. Все выбранные
    # элементы этого направления образуют одну цепочку: разделение по
    # положению в плане привело бы к размерам каждого подъёма по отдельности.
    trays = [element for element in group if not _is_fitting(element) and _participates(element, axis)]
    if not trays:
        return []

    tray_by_id = dict((_element_id(tray), tray) for tray in trays)
    neighbours = dict((tray_id, set()) for tray_id in tray_by_id)
    fittings = [element for element in group if _is_fitting(element)]

    # A fitting connects two trays into one run only when both have this same
    # plan direction. A change of direction is therefore a run boundary.
    for fitting in fittings:
        connected = [owner_id for owner_id in _connected_owner_ids(fitting)
                     if owner_id in tray_by_id]
        for tray_id in connected:
            neighbours[tray_id].update(
                other_id for other_id in connected if other_id != tray_id)

    runs = []
    remaining = set(tray_by_id)
    while remaining:
        start = remaining.pop()
        component_ids = set([start])
        stack = [start]
        while stack:
            current = stack.pop()
            for neighbour in neighbours[current]:
                if neighbour in remaining:
                    remaining.remove(neighbour)
                    component_ids.add(neighbour)
                    stack.append(neighbour)

        run = [tray_by_id[tray_id] for tray_id in component_ids]
        for fitting in fittings:
            if component_ids.intersection(_connected_owner_ids(fitting)):
                run.append(fitting)
        runs.append(run)

    # Выбранный отдельно фитинг тоже остаётся самостоятельной цепочкой.
    return runs


def _axis_kind(axis):
    if abs(axis.DotProduct(view.RightDirection)) >= COS_30:
        return u"горизонтальная"
    if abs(axis.DotProduct(view.UpDirection)) >= COS_30:
        return u"вертикальная"
    return u"диагональная"


def _solids(geometry, transform=None):
    """Возвращает Solid и преобразование его координат в координаты проекта.

    Для GeometryInstance берём именно GetSymbolGeometry(). GetInstanceGeometry()
    возвращает копию, чьи Reference нельзя использовать в размерах.
    """
    if geometry is None:
        return
    if transform is None:
        transform = Transform.Identity
    for geometry_object in geometry:
        if isinstance(geometry_object, Solid):
            if geometry_object.Volume > 0.000001:
                yield geometry_object, transform
        elif isinstance(geometry_object, GeometryInstance):
            try:
                instance_transform = transform.Multiply(geometry_object.Transform)
                symbol_geometry = geometry_object.GetSymbolGeometry()
            except Exception:
                continue
            for solid, solid_transform in _solids(symbol_geometry, instance_transform):
                yield solid, solid_transform


def _center_endpoint_candidates(element):
    """Stable point references at the two ends of a tray centre line."""
    candidates = []
    try:
        curve = element.Location.Curve
    except Exception:
        return candidates
    for index in (0, 1):
        try:
            reference = curve.GetEndPointReference(index)
            point = curve.GetEndPoint(index)
        except Exception:
            reference = None
            point = None
        if reference is not None and point is not None:
            candidates.append({
                "reference": reference,
                "point": point,
                "element": element,
            })
    return candidates


def _stable_tray_endpoint_reference(element, connector_point):
    """Stable LINEAR endpoint reference on a cable tray LocationCurve.

    Cable trays can accept a dimension to ``<instance>:0:LINEAR/<end>`` even
    when LocationCurve.GetEndPointReference() returns None.  The manual
    dimension diagnostic confirms this exact reference form for the family.
    """
    try:
        curve = element.Location.Curve
        endpoints = [curve.GetEndPoint(0), curve.GetEndPoint(1)]
        index = min(range(2),
                    key=lambda item: endpoints[item].DistanceTo(connector_point))
        stable = u"{}:0:LINEAR/{}".format(element.UniqueId, index)
        reference = Reference.ParseFromStableRepresentation(doc, stable)
        if reference is None:
            turn_reference_trace.append(
                u"Диагональный лоток {} | стабильная ссылка {} вернула пустой Reference."
                .format(_element_id(element), stable))
            return None
        return {
            "coordinate": 0.0,
            "reference": reference,
            "point": endpoints[index],
            "start": endpoints[index],
            "end": endpoints[index],
            "element": element,
        }
    except Exception as error:
        try:
            turn_reference_trace.append(
                u"Диагональный лоток {} | не удалось разобрать ссылку конца оси: {}"
                .format(_element_id(element), unicode(error)))
        except Exception:
            pass
        return None


def _geometry_reference_lines(geometry, transform=None):
    """Dimensionable symbol lines/edges and their project transforms.

    Cable-tray and fitting references are commonly stored on Solid.Edges,
    rather than as standalone Line geometry.  Retain the edge Reference while
    using its curve solely to locate it in project coordinates.
    """
    if geometry is None:
        return
    if transform is None:
        transform = Transform.Identity
    for geometry_object in geometry:
        if isinstance(geometry_object, Line):
            if geometry_object.Reference is not None:
                yield geometry_object, transform, geometry_object.Reference
        elif isinstance(geometry_object, Solid):
            for edge in geometry_object.Edges:
                try:
                    curve = edge.AsCurve()
                    reference = edge.Reference
                    if isinstance(curve, Line) and reference is not None:
                        yield curve, transform, reference
                except Exception:
                    continue
        elif isinstance(geometry_object, GeometryInstance):
            try:
                instance_transform = transform.Multiply(geometry_object.Transform)
                symbol_geometry = geometry_object.GetSymbolGeometry()
            except Exception:
                continue
            for line, line_transform, reference in _geometry_reference_lines(
                    symbol_geometry, instance_transform):
                yield line, line_transform, reference


def _center_line_candidates(element, axis):
    """Dimensionable centre-line references such as LINEAR/0."""
    candidates = []
    seen = set()
    geometry = _geometry_for_references(element)
    for line, line_transform, reference in _geometry_reference_lines(geometry):
        try:
            direction = line.GetEndPoint(1).Subtract(line.GetEndPoint(0))
            if reference is None or direction.GetLength() < 0.000001:
                continue
            if axis is not None:
                plan_direction = _project_to_view_plane(line_transform.OfVector(direction))
                if (plan_direction is None or
                        abs(plan_direction.DotProduct(axis)) < FACE_PARALLEL_COS):
                    continue
            point = line_transform.OfPoint(
                line.GetEndPoint(0).Add(line.GetEndPoint(1)).Multiply(0.5))
            stable = reference.ConvertToStableRepresentation(doc)
            if stable in seen:
                continue
            seen.add(stable)
            candidates.append({
                "coordinate": point.DotProduct(axis) if axis is not None else 0.0,
                "reference": reference,
                "point": point,
                "element": element,
            })
        except Exception:
            continue
    return candidates


def _stable_connector_line_candidates(element):
    """Native instance LINEAR references together with their real project lines.

    A raw line reference from symbol geometry cannot be used directly in a
    dimension.  Revit needs the corresponding INSTANCE reference.  Instead of
    guessing its geometry index, derive it from the raw stable representation
    and retain the transformed symbol line for connector matching.
    """
    candidates = []
    seen = set()
    try:
        instance_unique_id = element.UniqueId
        type_element = doc.GetElement(element.GetTypeId())
        type_unique_id = type_element.UniqueId if type_element else None
    except Exception:
        return candidates
    if not type_unique_id:
        return candidates

    geometry = _geometry_for_references(element)
    for line, line_transform, raw_reference in _geometry_reference_lines(geometry):
        try:
            raw_stable = raw_reference.ConvertToStableRepresentation(doc)
            marker_index = raw_stable.find(type_unique_id)
            if marker_index < 0:
                continue
            suffix = raw_stable[marker_index + len(type_unique_id):]
            if u":LINEAR/" not in suffix:
                continue
            stable = u"{}:0:INSTANCE:{}{}".format(
                instance_unique_id, type_unique_id, suffix)
            if stable in seen:
                continue
            reference = Reference.ParseFromStableRepresentation(doc, stable)
            resolved = element.GetGeometryObjectFromReference(reference)
            if isinstance(resolved, Edge):
                resolved = resolved.AsCurve()
            if not isinstance(resolved, Line):
                continue
            start = line_transform.OfPoint(line.GetEndPoint(0))
            end = line_transform.OfPoint(line.GetEndPoint(1))
            seen.add(stable)
            candidates.append({
                "coordinate": 0.0,
                "reference": reference,
                "point": start.Add(end).Multiply(0.5),
                "start": start,
                "end": end,
                "element": element,
            })
        except Exception:
            continue
    # Several fitting families do not expose a usable raw symbol reference,
    # even though their instance LINEAR reference is valid for a dimension.
    # Scan those native instance references directly as a fallback.  This is
    # the same reference form reported by the read-only manual-dimension
    # diagnostic (for example, INSTANCE:...:LINEAR/0).
    direct_candidates = _direct_instance_linear_candidates(element)
    known = set()
    for candidate in candidates:
        try:
            known.add(candidate["reference"].ConvertToStableRepresentation(doc))
        except Exception:
            pass
    for candidate in direct_candidates:
        try:
            stable = candidate["reference"].ConvertToStableRepresentation(doc)
        except Exception:
            stable = None
        if stable is None or stable not in known:
            candidates.append(candidate)
            if stable is not None:
                known.add(stable)
    return candidates


def _instance_type_infos(element):
    """Return outer and nested symbol UIDs with their project transforms."""
    infos = []
    seen = set()

    def _append(type_element, transform):
        try:
            unique_id = type_element.UniqueId if type_element else None
        except Exception:
            unique_id = None
        if unique_id and unique_id not in seen:
            infos.append((unique_id, transform))
            seen.add(unique_id)

    def _walk(geometry, transform):
        if geometry is None:
            return
        for geometry_object in geometry:
            if not isinstance(geometry_object, GeometryInstance):
                continue
            try:
                instance_transform = transform.Multiply(geometry_object.Transform)
                symbol = geometry_object.Symbol
                _append(doc.GetElement(symbol.Id), instance_transform)
                _walk(geometry_object.GetSymbolGeometry(), instance_transform)
            except Exception:
                pass

    try:
        _append(doc.GetElement(element.GetTypeId()), Transform.Identity)
    except Exception:
        pass
    try:
        # Stable INSTANCE references on many MEP fitting families carry the
        # Family UID rather than the FamilySymbol (type) UID.
        _append(element.Symbol.Family, Transform.Identity)
    except Exception:
        pass
    try:
        _walk(_geometry_for_references(element), Transform.Identity)
    except Exception:
        pass
    return infos


def _direct_instance_linear_candidates(element):
    """Find dimensionable instance LINEAR references without raw-edge metadata.

    The API can resolve these references on the fitting instance even when
    GetSymbolGeometry() supplies no matching stable reference.  Coordinates
    may be local symbol coordinates, so retain both raw and transformed lines
    and choose the one nearest to the fitting connectors.
    """
    candidates = []
    try:
        instance_unique_id = element.UniqueId
    except Exception:
        return candidates
    type_infos = _instance_type_infos(element)
    if not type_infos:
        return candidates
    connector_points = _connector_points(element)
    seen = set()
    for type_unique_id, transform in type_infos:
        for index in range(STABLE_SURFACE_SCAN_LIMIT):
            try:
                # The manual-dimension diagnostic proves that this family uses
                # INSTANCE:<nested-type-uid>:LINEAR/<index>.
                stable = u"{}:0:INSTANCE:{}:LINEAR/{}".format(
                    instance_unique_id, type_unique_id, index)
                reference = Reference.ParseFromStableRepresentation(doc, stable)
                resolved = element.GetGeometryObjectFromReference(reference)
                if isinstance(resolved, Edge):
                    resolved = resolved.AsCurve()
                if not isinstance(resolved, Line):
                    continue
                raw_start = resolved.GetEndPoint(0)
                raw_end = resolved.GetEndPoint(1)
                variants = [(raw_start, raw_end)]
                try:
                    variants.append((transform.OfPoint(raw_start),
                                     transform.OfPoint(raw_end)))
                except Exception:
                    pass
                if connector_points:
                    start, end = min(
                        variants,
                        key=lambda pair: min(
                            pair[0].Add(pair[1]).Multiply(0.5).DistanceTo(point)
                            for point in connector_points))
                else:
                    start, end = variants[0]
                resolved_stable = reference.ConvertToStableRepresentation(doc)
                if resolved_stable in seen:
                    continue
                seen.add(resolved_stable)
                candidates.append({
                    "coordinate": 0.0,
                    "reference": reference,
                    "point": start.Add(end).Multiply(0.5),
                    "start": start,
                    "end": end,
                    "element": element,
                })
            except Exception:
                continue
    return candidates


def _distance_to_line_segment(point, start, end):
    direction = end.Subtract(start)
    length_squared = direction.DotProduct(direction)
    if length_squared < 0.0000000001:
        return point.DistanceTo(start)
    position = point.Subtract(start).DotProduct(direction) / length_squared
    position = max(0.0, min(1.0, position))
    return point.DistanceTo(start.Add(direction.Multiply(position)))


def _connector_line_reference(element, connector_point):
    """Return the native LINEAR reference located at this exact connector."""
    candidates = _stable_connector_line_candidates(element)
    if not candidates:
        return None
    candidate = min(
        candidates,
        key=lambda item: _distance_to_line_segment(
            connector_point, item["start"], item["end"]))
    if (_distance_to_line_segment(connector_point, candidate["start"],
                                 candidate["end"]) >
            CONNECTOR_REFERENCE_TOLERANCE):
        return None
    candidate["point"] = connector_point
    return candidate


def _outward_plan_direction(tray, endpoint):
    """Direction from the tray interior towards this particular end."""
    try:
        curve = tray.Location.Curve
        start = curve.GetEndPoint(0)
        end = curve.GetEndPoint(1)
        other = end if endpoint.DistanceTo(start) < endpoint.DistanceTo(end) else start
        return _project_to_view_plane(endpoint.Subtract(other))
    except Exception:
        return None


def _is_diagonal_plan_direction(direction):
    return (direction is not None and
            abs(direction.DotProduct(view.RightDirection)) < COS_30 and
            abs(direction.DotProduct(view.UpDirection)) < COS_30)


def _fitting_turn_edge_reference(fitting, tray, endpoint, next_tray):
    """Native fitting edge at the external side of a non-orthogonal turn.

    The required point is the fitting's far edge in the direction of the
    outgoing tray, not the incoming tray connector.  A vertical 3D edge may
    project to that point in plan and is still a valid LINEAR reference.
    """
    candidates = _stable_connector_line_candidates(fitting)
    if not candidates:
        return None

    source_connector = _nearest_connector_point(tray, endpoint)
    if next_tray is not None:
        next_connector = _nearest_connector_point(next_tray, source_connector)
        direction = _project_to_view_plane(
            next_connector.Subtract(source_connector))
    else:
        fitting_points = _connector_points(fitting)
        if len(fitting_points) < 2:
            return None
        incoming = min(fitting_points,
                       key=lambda point: point.DistanceTo(endpoint))
        outgoing = max(fitting_points,
                       key=lambda point: point.DistanceTo(incoming))
        direction = _project_to_view_plane(outgoing.Subtract(incoming))
        if not _is_diagonal_plan_direction(direction):
            return None
    if direction is None:
        direction = _outward_plan_direction(tray, endpoint)
    if direction is None:
        return None

    calibrated = _calibrated_fitting_reference(fitting, direction)
    if calibrated is not None:
        return calibrated

    # The manual reference is a vertical (Z-direction) fitting edge.  In a
    # plan it projects to the exact outer turn point, and is therefore the
    # preferred reference over any sloped fitting edge.
    projected_point_edges = []
    for candidate in candidates:
        edge_direction = candidate["end"].Subtract(candidate["start"])
        if _project_to_view_plane(edge_direction) is None:
            projected_point_edges.append(candidate)
    if projected_point_edges:
        candidate = max(
            projected_point_edges,
            key=lambda item: item["point"].DotProduct(direction))
        candidate["point"] = candidate["point"]
        return candidate

    edge_points = []
    for candidate in candidates:
        edge_points.extend([candidate["start"], candidate["end"]])
    if not edge_points:
        return None
    outer_point = max(edge_points, key=lambda point: point.DotProduct(direction))
    candidate = min(
        candidates,
        key=lambda item: _distance_to_line_segment(
            outer_point, item["start"], item["end"]))
    if (_distance_to_line_segment(outer_point, candidate["start"],
                                  candidate["end"]) >
            CONNECTOR_REFERENCE_TOLERANCE):
        return None
    candidate["point"] = outer_point
    return candidate


def _turn_reference_trace_text(source, fitting, target, candidate, source_kind):
    """Read-only trace of the reference chosen at a non-orthogonal turn."""
    try:
        stable = candidate["reference"].ConvertToStableRepresentation(doc)
    except Exception:
        stable = u"не удалось получить"
    turn_reference_trace.append(
        u"Лоток {} → {} {} → {} | точка {} | {}".format(
            _element_id(source),
            u"фитинг {}".format(_element_id(fitting)) if fitting else u"без фитинга",
            u"диагональный лоток {}".format(_element_id(target)),
            source_kind,
            _point_text(candidate["point"]),
            stable))


def _face_candidates(element, axis):
    """Original reference discovery for ordinary planar tray routes."""
    options = Options()
    options.ComputeReferences = True
    options.IncludeNonVisibleObjects = False
    options.View = view
    candidates = []
    try:
        geometry = element.get_Geometry(options)
    except Exception:
        return candidates

    for solid, solid_transform in _solids(geometry):
        for face in solid.Faces:
            if not isinstance(face, PlanarFace) or face.Reference is None:
                continue
            normal = _project_to_view_plane(solid_transform.OfVector(face.FaceNormal))
            if normal is None or abs(normal.DotProduct(axis)) < FACE_PARALLEL_COS:
                continue
            point = solid_transform.OfPoint(face.Origin)
            candidates.append({
                "coordinate": point.DotProduct(axis),
                "reference": face.Reference,
                "point": point,
                "element": element,
            })
    return candidates


def _instance_face_candidates(element, axis):
    """Physical instance surfaces used only by elevation-change routes."""
    candidates = []
    geometry = _geometry_for_references(element)
    if geometry is None:
        return candidates

    for solid in _instance_edge_solids(geometry):
        for face in solid.Faces:
            if not isinstance(face, PlanarFace) or face.Reference is None:
                continue
            # A reference returned from instance geometry can resolve to a
            # different physical surface. Ask Revit for the actual target
            # before it is allowed into a linear dimension chain.
            try:
                resolved_face = element.GetGeometryObjectFromReference(face.Reference)
            except Exception:
                resolved_face = None
            if not isinstance(resolved_face, PlanarFace):
                continue
            normal = resolved_face.FaceNormal.Normalize()
            if abs(normal.DotProduct(axis)) < FACE_PARALLEL_COS:
                continue
            point = resolved_face.Origin
            candidates.append({
                "coordinate": point.DotProduct(axis),
                "reference": face.Reference,
                "point": point,
                "element": element,
            })
    return candidates


def _stable_surface_candidates(element, axis):
    """Physical surface references resolved from Revit stable references.

    GeometryInstance can expose copied face references that resolve to a
    different surface. Revit's own manual picker returns stable SURFACE
    references, so the elevation-route branch queries those directly.
    """
    candidates = []
    seen = set()
    try:
        unique_id = element.UniqueId
    except Exception:
        return candidates

    for index in range(STABLE_SURFACE_SCAN_LIMIT):
        try:
            stable = u"{}:{}:SURFACE".format(unique_id, index)
            reference = Reference.ParseFromStableRepresentation(doc, stable)
            face = element.GetGeometryObjectFromReference(reference)
            if not isinstance(face, PlanarFace):
                continue
            normal = face.FaceNormal.Normalize()
            if abs(normal.DotProduct(axis)) < FACE_PARALLEL_COS:
                continue
            coordinate = face.Origin.DotProduct(axis)
            key = (round(coordinate, 8), stable)
            if key in seen:
                continue
            seen.add(key)
            candidates.append({
                "coordinate": coordinate,
                "reference": reference,
                "point": face.Origin,
                "element": element,
            })
        except Exception:
            continue
    return candidates


def _witness_direction(axis):
    """Направление выносных линий размера в плоскости активного плана."""
    return view.ViewDirection.CrossProduct(axis).Normalize()


def _instance_edge_solids(geometry):
    """Solid в координатах экземпляра для поиска видимых физических рёбер.

    Для граней используется геометрия символа, а для рёбер — геометрия
    конкретного экземпляра. Именно эти рёбра Revit предлагает при ручном
    выборе размера на плане.
    """
    if geometry is None:
        return
    for geometry_object in geometry:
        if isinstance(geometry_object, Solid):
            if geometry_object.Volume > 0.000001:
                yield geometry_object
        elif isinstance(geometry_object, GeometryInstance):
            try:
                instance_geometry = geometry_object.GetInstanceGeometry()
            except Exception:
                continue
            for solid in _instance_edge_solids(instance_geometry):
                yield solid


def _geometry_for_references(element):
    options = Options()
    options.ComputeReferences = True
    options.IncludeNonVisibleObjects = False
    options.View = view
    try:
        return element.get_Geometry(options)
    except Exception:
        return None


def _edge_candidates(element, axis):
    """Физические поперечные рёбра элемента для размеров на плане.

    У наклонного по высоте лотка торцевые грани не параллельны торцевым
    граням соседних элементов в 3D. Поперечные рёбра при неизменном
    направлении трассы на плане остаются параллельными и пригодны для
    одной линейной размерной цепочки.
    """
    candidates = []
    geometry = _geometry_for_references(element)
    if geometry is None:
        return candidates

    witness = _witness_direction(axis)
    for solid in _instance_edge_solids(geometry):
        for edge in solid.Edges:
            try:
                curve = edge.AsCurve()
                # Прямая физическая кромка годится как опора линейного
                # размера; дуги и скругления не используем.
                if not isinstance(curve, Line):
                    continue
                reference = edge.Reference
                if reference is None:
                    try:
                        reference = curve.Reference
                    except Exception:
                        reference = None
                if reference is None:
                    continue
                start = curve.GetEndPoint(0)
                end = curve.GetEndPoint(1)
                direction = end.Subtract(start)
                if direction.GetLength() < 0.000001:
                    continue
                direction = direction.Normalize()
                if abs(direction.DotProduct(witness)) < FACE_PARALLEL_COS:
                    continue
                point = start.Add(end).Multiply(0.5)
                candidates.append({
                    "coordinate": point.DotProduct(axis),
                    "reference": reference,
                    "point": point,
                    "element": element,
                })
            except Exception:
                continue
    return candidates


def _reference_diagnostics(elements, axis):
    """Compact read-only diagnostics for unavailable plan references."""
    witness = _witness_direction(axis)
    reports = []
    for element in elements[:5]:
        edge_total = 0
        edge_references = 0
        cross_edges = 0
        for solid in _instance_edge_solids(_geometry_for_references(element)):
            for edge in solid.Edges:
                edge_total += 1
                try:
                    curve = edge.AsCurve()
                    reference = edge.Reference or curve.Reference
                    if reference is not None:
                        edge_references += 1
                    if isinstance(curve, Line):
                        direction = curve.GetEndPoint(1).Subtract(curve.GetEndPoint(0))
                        if (direction.GetLength() > 0.000001 and
                                abs(direction.Normalize().DotProduct(witness)) >= FACE_PARALLEL_COS):
                            cross_edges += 1
                except Exception:
                    pass
        location_reference = False
        try:
            location_reference = element.Location.Curve.Reference is not None
        except Exception:
            pass
        reports.append(u"{}: edges={}, refs={}, cross={}, location_ref={}"
                       .format(_element_id(element), edge_total, edge_references,
                               cross_edges, location_reference))
    return u"; ".join(reports)


def _has_height_change(element):
    """Есть ли у лотка составляющая вдоль направления взгляда плана."""
    if _is_fitting(element):
        return False
    try:
        curve = element.Location.Curve
        direction = curve.GetEndPoint(1).Subtract(curve.GetEndPoint(0))
        return abs(direction.DotProduct(view.ViewDirection)) > SLOPE_TOLERANCE
    except Exception:
        return False


def _chain_uses_edge_references(elements):
    # Все ссылки одной цепочки должны быть однотипными: при наличии хотя бы
    # одного наклонного лотка используем поперечные рёбра и для прямых.
    return any(_has_height_change(element) for element in elements)


def _station_coordinates(element, axis):
    """Координаты торцов элемента. Они задают только внешние точки цепочки."""
    coordinates = []
    if not _is_fitting(element):
        try:
            curve = element.Location.Curve
            coordinates = [
                curve.GetEndPoint(0).DotProduct(axis),
                curve.GetEndPoint(1).DotProduct(axis),
            ]
        except Exception:
            pass
    else:
        for connector in _connectors(element):
            try:
                connector_axis = _canonical_axis(connector.CoordinateSystem.BasisZ)
                if connector_axis and abs(connector_axis.DotProduct(axis)) >= FACE_PARALLEL_COS:
                    coordinates.append(connector.Origin.DotProduct(axis))
            except Exception:
                pass
    coordinates = sorted(coordinates)
    unique = []
    for coordinate in coordinates:
        if not unique or abs(coordinate - unique[-1]) > COORDINATE_TOLERANCE:
            unique.append(coordinate)
    return unique


def _participates(element, axis):
    # Фитинг может иметь физические грани в нескольких направлениях (отвод,
    # тройник). Он участвует только в цепочке, направление которой уже задано
    # выбранными лотками.
    if _is_fitting(element):
        return True
    return any(_same_axis(axis, element_axis) for element_axis in _element_axes(element))


def _fitting_at_tray_end(tray, endpoint, selected_ids):
    """Selected fitting connected to a specific end of a selected tray."""
    # Prefer the MEP topology over geometric proximity.  A physical tray face
    # can be far from its connector, so the connector nearest to that face is
    # not necessarily the one joined to the fitting.  Inspect every tray
    # connector, then select the linked fitting closest to this physical end.
    connected_fittings = []
    seen_fittings = set()
    for connector in _connectors(tray):
        try:
            related_connectors = connector.AllRefs
        except Exception:
            continue
        for related_connector in related_connectors:
            try:
                owner = related_connector.Owner
                owner_id = _element_id(owner)
                if _is_fitting(owner) and owner_id not in seen_fittings:
                    connected_fittings.append(owner)
                    seen_fittings.add(owner_id)
            except Exception:
                pass
    if connected_fittings:
        return min(
            connected_fittings,
            key=lambda fitting: min(
                endpoint.DistanceTo(point) for point in _connector_points(fitting)))

    nearest_distance = None
    end_connectors = []
    for connector in _connectors(tray):
        try:
            distance = connector.Origin.DistanceTo(endpoint)
        except Exception:
            continue
        if nearest_distance is None or distance < nearest_distance - COORDINATE_TOLERANCE:
            nearest_distance = distance
            end_connectors = [connector]
        elif abs(distance - nearest_distance) <= COORDINATE_TOLERANCE:
            end_connectors.append(connector)
    # Physical tray faces and MEP connectors do not have a fixed offset.
    # On some families the visible end is more than a metre away from its
    # connector.  The nearest connector still identifies the correct route
    # end, and its AllRefs relationship is authoritative, so never discard it
    # merely because the construction length exceeds a geometric tolerance.
    if nearest_distance is not None:
        for connector in end_connectors:
            try:
                related_connectors = connector.AllRefs
            except Exception:
                continue
            for related_connector in related_connectors:
                try:
                    owner = related_connector.Owner
                    # A fitting is contextual geometry of the selected tray.
                    # It may be selected, but must not have to be selected
                    # merely for its external turn edge to serve as a reference.
                    if _is_fitting(owner):
                        return owner
                except Exception:
                    pass

    # Some imported/assembled tray families are visually adjacent but do not
    # expose Connector.AllRefs relationships.  Locate only the nearest fitting
    # around this tray end as a controlled geometric fallback.
    global context_fittings_cache
    if context_fittings_cache is None:
        try:
            context_fittings_cache = list(
                FilteredElementCollector(doc)
                .OfCategory(BuiltInCategory.OST_CableTrayFitting)
                .WhereElementIsNotElementType())
        except Exception:
            context_fittings_cache = []
    nearest_fitting = None
    nearest_fitting_distance = None
    for fitting in context_fittings_cache:
        try:
            connector_distance = min(endpoint.DistanceTo(point)
                                     for point in _connector_points(fitting))
        except Exception:
            connector_distance = FITTING_FALLBACK_TOLERANCE + 1.0
        try:
            bounding_box = fitting.get_BoundingBox(view)
            if bounding_box is None:
                bounding_box = fitting.get_BoundingBox(None)
            if bounding_box is None:
                bounding_distance = FITTING_FALLBACK_TOLERANCE + 1.0
            else:
                delta_x = max(bounding_box.Min.X - endpoint.X, 0.0,
                              endpoint.X - bounding_box.Max.X)
                delta_y = max(bounding_box.Min.Y - endpoint.Y, 0.0,
                              endpoint.Y - bounding_box.Max.Y)
                bounding_distance = (delta_x * delta_x + delta_y * delta_y) ** 0.5
        except Exception:
            bounding_distance = FITTING_FALLBACK_TOLERANCE + 1.0
        distance = min(connector_distance, bounding_distance)
        if distance > FITTING_FALLBACK_TOLERANCE:
            continue
        if (nearest_fitting_distance is None or
                distance < nearest_fitting_distance - COORDINATE_TOLERANCE):
            nearest_fitting = fitting
            nearest_fitting_distance = distance
    if nearest_fitting is None:
        fitting_lookup_trace.append(
            u"Лоток {} | конец {} | фитинг в радиусе {:.0f} мм не найден; найдено фитингов в документе: {}."
            .format(_element_id(tray), _point_text(endpoint),
                    FITTING_FALLBACK_TOLERANCE * MM_IN_FOOT,
                    len(context_fittings_cache)))
    else:
        fitting_lookup_trace.append(
            u"Лоток {} | конец {} | ближайший фитинг {} | расстояние {:.0f} мм."
            .format(_element_id(tray), _point_text(endpoint),
                    _element_id(nearest_fitting),
                    nearest_fitting_distance * MM_IN_FOOT))
    return nearest_fitting


def _extreme_candidate(candidates, outward_sign):
    if not candidates:
        return None
    ordered = sorted(candidates, key=lambda item: item["coordinate"])
    return ordered[-1] if outward_sign > 0 else ordered[0]


def _elevation_route_references(elements, axis):
    """Dimension stations for a route with elevation changes on a plan.

    A tray between two fittings is dimensioned by the outer physical faces of
    those fittings, not by its own inclined end faces. At an exposed route end
    the tray's outer face is used as a fallback.
    """
    selected_ids = set(_element_id(element) for element in elements)
    candidates = []
    missing_references = []
    trays = [element for element in elements
             if not _is_fitting(element) and _participates(element, axis)]

    for tray in trays:
        try:
            curve = tray.Location.Curve
            endpoints = [curve.GetEndPoint(0), curve.GetEndPoint(1)]
        except Exception:
            missing_references.append(tray)
            continue

        for index, endpoint in enumerate(endpoints):
            other_endpoint = endpoints[1 - index]
            outward_sign = -1 if endpoint.DotProduct(axis) < other_endpoint.DotProduct(axis) else 1
            fitting = _fitting_at_tray_end(tray, endpoint, selected_ids)
            source = fitting if fitting else tray
            candidate = _extreme_candidate(_stable_surface_candidates(source, axis), outward_sign)
            if candidate is None:
                missing_references.append(source)
            else:
                candidates.append(candidate)

    ordered = sorted(candidates, key=lambda item: item["coordinate"])
    unique = []
    for candidate in ordered:
        if not unique or abs(candidate["coordinate"] - unique[-1]["coordinate"]) > COORDINATE_TOLERANCE:
            unique.append(candidate)
    return unique, missing_references


def _cross_route_elements(elements, axis, selected_ids):
    """Straight trays that meet this run through one of its fittings.

    Their side faces are the two limits of a turn or branch in the current
    dimension direction.
    """
    cross_routes = []
    seen = set()
    for fitting in elements:
        if not _is_fitting(fitting):
            continue
        for owner_id in _connected_owner_ids(fitting):
            if owner_id not in selected_ids or owner_id in seen:
                continue
            try:
                tray = doc.GetElement(ElementId(owner_id))
            except Exception:
                tray = None
            if (tray is None or _is_fitting(tray) or
                    _has_height_change(tray) or _participates(tray, axis)):
                continue
            seen.add(owner_id)
            cross_routes.append(tray)
    return cross_routes


def _connector_points(element):
    points = []
    for connector in _connectors(element):
        try:
            points.append(connector.Origin)
        except Exception:
            pass
    if points:
        return points
    try:
        curve = element.Location.Curve
        return [curve.GetEndPoint(0), curve.GetEndPoint(1)]
    except Exception:
        return []


def _cross_routes_at_end(tray, endpoint, axis, selected_ids):
    """Perpendicular straight trays connected to this exact tray end."""
    fitting = _fitting_at_tray_end(tray, endpoint, selected_ids)
    routes = []
    seen = set()
    if fitting is not None:
        owner_ids = _connected_owner_ids(fitting)
        if not any(owner_id in selected_ids for owner_id in owner_ids):
            # The fitting is geometrically adjacent but has no AllRefs link.
            # Search only among the already selected route elements.
            owner_ids = selected_ids
    else:
        owner_ids = selected_ids
    source_connector = _nearest_connector_point(tray, endpoint)
    route_anchor_points = (_connector_points(fitting)
                           if fitting is not None else [source_connector])
    nearest_distance = None
    for owner_id in owner_ids:
        if (owner_id not in selected_ids or owner_id == _element_id(tray) or
                owner_id in seen):
            continue
        try:
            other = doc.GetElement(ElementId(owner_id))
            other_endpoints = _connector_points(other)
        except Exception:
            other = None
            other_endpoints = []
        if (other is None or _is_fitting(other) or
                _has_height_change(other) or _participates(other, axis)):
            continue
        distance = min([anchor_point.DistanceTo(other_endpoint)
                        for anchor_point in route_anchor_points
                        for other_endpoint in other_endpoints] or [None])
        if distance is None:
            continue
        if distance > TURN_CONNECTOR_TOLERANCE:
            continue
        if nearest_distance is None or distance < nearest_distance - COORDINATE_TOLERANCE:
            nearest_distance = distance
            routes = [other]
            seen.add(owner_id)
        elif abs(distance - nearest_distance) <= COORDINATE_TOLERANCE:
            seen.add(owner_id)
            routes.append(other)
    return routes


def _nearest_connector_point(tray, point):
    connector_points = _connector_points(tray)
    if not connector_points:
        return point
    return min(connector_points, key=lambda candidate: candidate.DistanceTo(point))


def _is_diagonal_plan_element(element):
    for element_axis in _location_axes(element):
        if (abs(element_axis.DotProduct(view.RightDirection)) < COS_30 and
                abs(element_axis.DotProduct(view.UpDirection)) < COS_30):
            return True
    return False


def _direct_section_references(elements, axis):
    """Use end faces of direct sections only; turns are hard boundaries."""
    candidates = []
    missing_references = []
    layout_elements = []
    for element in elements:
        # Preserve the existing rule for routes that change elevation: they
        # are not combined with ordinary plan dimensions.
        if (not _is_straight_route(element) or _has_height_change(element) or
                not _participates(element, axis)):
            continue
        element_candidates = _face_candidates(element, axis)
        if not element_candidates:
            element_candidates = _stable_surface_candidates(element, axis)
        if not element_candidates:
            missing_references.append(element)
            continue
        for candidate in element_candidates:
            candidate["coordinate"] = candidate["point"].DotProduct(axis)
        ordered = sorted(element_candidates,
                         key=lambda item: item["coordinate"])
        first = ordered[0]
        last = ordered[-1]
        candidates.append(first)
        if abs(last["coordinate"] - first["coordinate"]) > COORDINATE_TOLERANCE:
            candidates.append(last)
        layout_elements.append(element)

    ordered = sorted(candidates, key=lambda item: item["coordinate"])
    unique = []
    for candidate in ordered:
        if (not unique or
                abs(candidate["coordinate"] - unique[-1]["coordinate"]) > COORDINATE_TOLERANCE):
            unique.append(candidate)
    return unique, missing_references, layout_elements


def _references_for_axis(elements, axis, selected_ids):
    # Fittings and adjacent routes must never contribute dimension stations.
    return _direct_section_references(elements, axis)
    """Возвращает только внешние физические грани каждого элемента.

    У лотков и фитингов может быть много параллельных граней: стенки,
    внутренние полки и технологические поверхности. Размер между всеми ими
    создаёт ложные значения 0, 20, 40, 90 и т.п. Для одной размерной сетки
    нужны лишь две крайние грани элемента в её направлении.
    """
    candidates = []
    missing_references = []
    layout_elements = list(elements)
    for element in elements:
        if _is_fitting(element) or _has_height_change(element):
            continue
        if not _participates(element, axis):
            continue
        is_diagonal = _is_diagonal_plan_element(element)
        element_candidates = _face_candidates(element, axis)
        if not element_candidates:
            element_candidates = _stable_surface_candidates(element, axis)
        for candidate in element_candidates:
            candidate["coordinate"] = candidate["point"].DotProduct(axis)
        if not element_candidates:
            missing_references.append(element)
            continue

        ordered_element_candidates = sorted(
            element_candidates, key=lambda item: item["coordinate"])
        first = ordered_element_candidates[0]
        last = ordered_element_candidates[-1]
        # Use the same physical end faces that form the dimension itself.
        # Location.Curve endpoints can be displaced by a tray's construction
        # length and do not reliably coincide with a connected fitting.
        endpoints = [first["point"]]
        if last["point"].DistanceTo(first["point"]) > COORDINATE_TOLERANCE:
            endpoints.append(last["point"])
        candidates.append(first)
        if abs(last["coordinate"] - first["coordinate"]) > COORDINATE_TOLERANCE:
            candidates.append(last)
        for endpoint in endpoints:
            endpoint_fitting = _fitting_at_tray_end(
                element, endpoint, selected_ids)
            endpoint_fitting_reference = (_fitting_turn_edge_reference(
                endpoint_fitting, element, endpoint, None)
                if endpoint_fitting is not None else None)
            if endpoint_fitting_reference is not None:
                endpoint_fitting_reference["coordinate"] = (
                    endpoint_fitting_reference["point"].DotProduct(axis))
                candidates.append(endpoint_fitting_reference)
                try:
                    stable = endpoint_fitting_reference["reference"].ConvertToStableRepresentation(doc)
                except Exception:
                    stable = u"не удалось получить"
                turn_reference_trace.append(
                    u"Лоток {} → фитинг {} | внешнее ребро фитинга | точка {} | {}"
                    .format(_element_id(element), _element_id(endpoint_fitting),
                            _point_text(endpoint_fitting_reference["point"]), stable))
            for cross_route in _cross_routes_at_end(
                    element, endpoint, axis, selected_ids):
                if is_diagonal:
                    # The diagonal tray already contributes its two centre
                    # points, so no angled face from the adjoining run is
                    # valid for this dimension direction.
                    continue
                cross_is_diagonal = _is_diagonal_plan_element(cross_route)
                if cross_is_diagonal:
                    source_connector = _nearest_connector_point(element, endpoint)
                    # Revit exposes a stable endpoint reference on the
                    # diagonal tray's LocationCurve (the same LINEAR
                    # reference used by a manually placed dimension).  It is
                    # more reliable than the hidden fitting edge and removes
                    # any dependency on that fitting family publishing refs.
                    connector_reference = _stable_tray_endpoint_reference(
                        cross_route, source_connector)
                    cross_candidates = ([connector_reference]
                                        if connector_reference else [])
                    if connector_reference is not None:
                        _turn_reference_trace_text(
                            element, None, cross_route,
                            connector_reference, u"конец оси диагонального лотка")
                    else:
                        turn_reference_trace.append(
                            u"Лоток {} → диагональный лоток {}: Reference конца оси не найден."
                            .format(_element_id(element),
                                    _element_id(cross_route)))
                else:
                    cross_candidates = _face_candidates(cross_route, axis)
                for candidate in cross_candidates:
                    candidate["coordinate"] = candidate["point"].DotProduct(axis)
                if not cross_candidates:
                    # A diagonal transition is an optional intermediate
                    # station. Some tray/fitting families expose no stable
                    # edge reference for it. In that case preserve the
                    # valid straight-run grid instead of discarding it.
                    if not cross_is_diagonal:
                        missing_references.append(cross_route)
                    continue
                if cross_is_diagonal:
                    # A horizontal/vertical dimension meets an angled tray
                    # at its centre-line endpoint, never at a sloped face.
                    centre = min(cross_candidates,
                                 key=lambda item: item["point"].DistanceTo(endpoint))
                    candidates.append(centre)
                else:
                    endpoint_coordinate = endpoint.DotProduct(axis)
                    opposite = max(
                        cross_candidates,
                        key=lambda item: abs(item["coordinate"] - endpoint_coordinate))
                    candidates.append(opposite)

    ordered = sorted(candidates, key=lambda item: item["coordinate"])
    unique = []
    for candidate in ordered:
        if not unique or abs(candidate["coordinate"] - unique[-1]["coordinate"]) > COORDINATE_TOLERANCE:
            unique.append(candidate)
    return unique, missing_references, layout_elements


def _group_label(group):
    return u", ".join(unicode(_element_id(element)) for element in group)


def _build_dimension_plans(groups):
    plans = []
    issues = []
    for group in groups:
        group_plans = []
        group_issues = []
        group_ids = set(_element_id(element) for element in group)
        axes = _group_axes(group)
        if not axes:
            issues.append(u"Группа элементов [{}]: не удалось определить направление лотка или фитинга.".format(_group_label(group)))
            continue

        for axis in axes:
            for participants in _runs_for_axis(group, axis):
                candidates, missing_references, layout_elements = _references_for_axis(
                    participants, axis, group_ids)
                kind = _axis_kind(axis)
                if missing_references:
                    group_issues.append(
                        u"Группа элементов [{}], {} цепочка: у элементов [{}] нет доступных физических граней у торцов."
                        .format(
                            _group_label(participants),
                            kind,
                            _group_label(missing_references)))
                    group_issues.append(
                        u"Диагностика Reference: {}"
                        .format(_reference_diagnostics(missing_references, axis)))
                    continue
                if len(candidates) < 2:
                    group_issues.append(
                        u"Группа элементов [{}], {} цепочка: не найдены две физические внешние грани с доступными Reference."
                        .format(_group_label(participants), kind))
                    continue
                group_plans.append({
                    "axis": axis,
                    "kind": kind,
                    "candidates": candidates,
                    "elements": layout_elements,
                })
        # For a route with elevation changes, an incomplete result is not
        # useful: do not create isolated vertical dimensions without its
        # required horizontal chain.
        plans.extend(group_plans)
        issues.extend(group_issues)
    return plans, issues


def _dimension_types():
    options = []
    for dimension_type in FilteredElementCollector(doc).OfClass(DimensionType):
        try:
            if dimension_type.StyleType == DimensionStyleType.Linear:
                options.append(DimensionTypeOption(dimension_type))
        except Exception:
            pass
    return sorted(options, key=lambda option: unicode(option))


def _pick_dimension_type():
    options = _dimension_types()
    if not options:
        forms.alert(u"В проекте нет доступных линейных типов размеров.", title=u"Размеры лотков", exitscript=True)
    choice = forms.SelectFromList.show(
        options,
        title=u"Тип линейного размера",
        button_name=u"Создать размеры",
        multiselect=False)
    return choice.dimension_type if choice else None


def _project_dimension_type_key():
    try:
        unique_id = doc.ProjectInformation.UniqueId
    except Exception:
        unique_id = doc.Title
    return u"dimension_type_{}".format(unique_id.replace(u"-", u"_"))


def _stored_dimension_type():
    config = script.get_config(u"myrvttools_dimension_type")
    unique_id = getattr(config, _project_dimension_type_key(), None)
    if not unique_id:
        forms.alert(
            u"Сначала выберите тип размера кнопкой «Тип размера».",
            title=u"Размеры лотков", exitscript=True)
    try:
        dimension_type = doc.GetElement(unique_id)
        valid = (dimension_type is not None and
                 dimension_type.StyleType == DimensionStyleType.Linear)
    except Exception:
        valid = False
    if not valid:
        forms.alert(
            u"Сохранённый тип размера больше недоступен. Выберите его заново кнопкой «Тип размера».",
            title=u"Размеры лотков", exitscript=True)
    return dimension_type


def _offset_direction(axis, kind):
    if kind == u"горизонтальная":
        return view.UpDirection.Multiply(-1.0)
    if kind == u"вертикальная":
        return view.RightDirection.Multiply(-1.0)
    direction = view.ViewDirection.CrossProduct(axis).Normalize()
    if direction.DotProduct(view.UpDirection) > 0.0:
        direction = direction.Multiply(-1.0)
    return direction


def _point_on_view_plane(point):
    """Переносит точку из 3D-геометрии в плоскость аннотаций активного вида."""
    vector_to_point = point.Subtract(view.Origin)
    depth = vector_to_point.DotProduct(view.ViewDirection)
    return point.Subtract(view.ViewDirection.Multiply(depth))


def _bounding_box_points(element):
    try:
        bounding_box = element.get_BoundingBox(view) or element.get_BoundingBox(None)
    except Exception:
        return []
    if bounding_box is None:
        return []
    points = []
    for x in (bounding_box.Min.X, bounding_box.Max.X):
        for y in (bounding_box.Min.Y, bounding_box.Max.Y):
            for z in (bounding_box.Min.Z, bounding_box.Max.Z):
                points.append(XYZ(x, y, z))
    return points


def _outer_layout_coordinate(elements, direction, fallback):
    coordinates = []
    for element in elements:
        for point in _bounding_box_points(element):
            coordinates.append(_point_on_view_plane(point).DotProduct(direction))
    return max(coordinates) if coordinates else fallback


def _line_points_2d(line):
    start = line.GetEndPoint(0)
    end = line.GetEndPoint(1)
    return ((start.DotProduct(view.RightDirection), start.DotProduct(view.UpDirection)),
            (end.DotProduct(view.RightDirection), end.DotProduct(view.UpDirection)))


def _cross_2d(first, second):
    return first[0] * second[1] - first[1] * second[0]


def _subtract_2d(first, second):
    return (first[0] - second[0], first[1] - second[1])


def _segment_intersects(first, second):
    """Returns True when two annotation strokes cross or overlap."""
    first_start, first_end = _line_points_2d(first)
    second_start, second_end = _line_points_2d(second)
    first_vector = _subtract_2d(first_end, first_start)
    second_vector = _subtract_2d(second_end, second_start)
    origin_delta = _subtract_2d(second_start, first_start)
    denominator = _cross_2d(first_vector, second_vector)
    if abs(denominator) <= 0.000000001:
        if abs(_cross_2d(origin_delta, first_vector)) > 0.000000001:
            return False
        first_min_x = min(first_start[0], first_end[0])
        first_max_x = max(first_start[0], first_end[0])
        first_min_y = min(first_start[1], first_end[1])
        first_max_y = max(first_start[1], first_end[1])
        second_min_x = min(second_start[0], second_end[0])
        second_max_x = max(second_start[0], second_end[0])
        second_min_y = min(second_start[1], second_end[1])
        second_max_y = max(second_start[1], second_end[1])
        return (first_min_x <= second_max_x + LAYOUT_TOLERANCE and
                second_min_x <= first_max_x + LAYOUT_TOLERANCE and
                first_min_y <= second_max_y + LAYOUT_TOLERANCE and
                second_min_y <= first_max_y + LAYOUT_TOLERANCE)
    first_factor = _cross_2d(origin_delta, second_vector) / denominator
    second_factor = _cross_2d(origin_delta, first_vector) / denominator
    return (-0.000001 <= first_factor <= 1.000001 and
            -0.000001 <= second_factor <= 1.000001)


def _dimension_strokes(plan, dimension_line):
    """Dimension line plus its witness lines, represented as line segments."""
    strokes = [dimension_line]
    axis = plan["axis"]
    line_start = dimension_line.GetEndPoint(0)
    line_start_coordinate = line_start.DotProduct(axis)
    for candidate in plan["candidates"]:
        witness_start = _point_on_view_plane(candidate["point"])
        witness_end = line_start.Add(axis.Multiply(
            candidate["coordinate"] - line_start_coordinate))
        if witness_start.DistanceTo(witness_end) > LAYOUT_TOLERANCE:
            strokes.append(Line.CreateBound(witness_start, witness_end))
    return strokes


def _has_layout_conflict(strokes, occupied_strokes):
    for stroke in strokes:
        for occupied in occupied_strokes:
            if _segment_intersects(stroke, occupied):
                return True
    return False


def _layout_dimension(plan, occupied_strokes):
    """Finds a side and shelf that do not cross previously made dimensions."""
    preferred = _offset_direction(plan["axis"], plan["kind"])
    directions = [preferred, preferred.Multiply(-1.0)]
    last = None
    for shelf in range(DIMENSION_SHELF_ATTEMPTS):
        offset = BASE_OFFSET + shelf * DIMENSION_SHELF_STEP
        for direction in directions:
            dimension_line = _dimension_line(plan, offset, direction)
            strokes = _dimension_strokes(plan, dimension_line)
            last = (offset, direction, dimension_line, strokes)
            if not _has_layout_conflict(strokes, occupied_strokes):
                return last
    return last


def _dimension_line(plan, offset, offset_direction=None):
    axis = plan["axis"]
    candidates = plan["candidates"]
    minimum = candidates[0]["coordinate"] - LINE_EXTENSION
    maximum = candidates[-1]["coordinate"] + LINE_EXTENSION
    # Размер — аннотация вида. Лотки часто находятся выше уровня плана,
    # поэтому нельзя использовать Z-координату физической грани напрямую.
    anchor = _point_on_view_plane(candidates[0]["point"])
    anchor_coordinate = anchor.DotProduct(axis)
    base = anchor.Add(axis.Multiply(minimum - anchor_coordinate))
    end = anchor.Add(axis.Multiply(maximum - anchor_coordinate))
    shift_direction = offset_direction
    if shift_direction is None:
        shift_direction = _offset_direction(axis, plan["kind"])
    outer_coordinate = _outer_layout_coordinate(plan["elements"], shift_direction, anchor.DotProduct(shift_direction))
    distance_to_outer_edge = max(0.0, outer_coordinate - anchor.DotProduct(shift_direction))
    shift = shift_direction.Multiply(distance_to_outer_edge + offset)
    return Line.CreateBound(base.Add(shift), end.Add(shift))


def _create_dimension(plan, offset, dimension_type, offset_direction=None):
    references = ReferenceArray()
    for candidate in plan["candidates"]:
        references.Append(candidate["reference"])
    dimension_line = _dimension_line(plan, offset, offset_direction)
    dimension = doc.Create.NewDimension(view, dimension_line, references)
    if dimension is None:
        raise Exception(u"Revit не создал размер по выбранным граням.")
    dimension.ChangeTypeId(dimension_type.Id)
    return dimension, dimension_line


def _result_text(created, issues):
    success = u"Создано цепочек размеров: {}.".format(created)
    if not issues:
        return success, None
    return success, u"Не обработано:\n\n{}".format(u"\n\n".join(issues))


def _point_text(point):
    return u"({:.0f}; {:.0f}; {:.0f}) мм".format(
        point.X * MM_IN_FOOT,
        point.Y * MM_IN_FOOT,
        point.Z * MM_IN_FOOT)


def _dimension_debug_text(records):
    try:
        dimensions_category_hidden = view.GetCategoryHidden(ElementId(BuiltInCategory.OST_Dimensions))
    except Exception:
        dimensions_category_hidden = u"не определено"
    try:
        annotations_hidden = view.AreAnnotationCategoriesHidden
    except Exception:
        annotations_hidden = u"не определено"
    try:
        annotation_crop_active = view.AnnotationCropActive
    except Exception:
        annotation_crop_active = u"не определено"
    lines = [
        u"Временная диагностика созданных размеров:",
        u"Категория «Размеры» скрыта: {}".format(dimensions_category_hidden),
        u"Аннотации вида скрыты: {}".format(annotations_hidden),
        u"Граница аннотаций включена: {}".format(annotation_crop_active),
    ]
    for record in records:
        dimension = record["dimension"]
        dimension_line = record["line"]
        plan = record.get("plan")
        try:
            owner_view_id = dimension.OwnerViewId.IntegerValue
        except Exception:
            owner_view_id = u"не определён"
        try:
            is_hidden = dimension.IsHidden(view)
        except Exception:
            is_hidden = u"не определено"
        try:
            bounding_box = dimension.get_BoundingBox(view)
            if bounding_box:
                bounds = u"{} — {}".format(_point_text(bounding_box.Min), _point_text(bounding_box.Max))
            else:
                bounds = u"не получены"
        except Exception as error:
            bounds = u"ошибка: {}".format(unicode(error))
        lines.append(
            u"ID {} | вид {} | Revit считает скрытым: {}\nлиния: {} — {}\nграницы: {}".format(
                dimension.Id.IntegerValue,
                owner_view_id,
                is_hidden,
                _point_text(dimension_line.GetEndPoint(0)),
                _point_text(dimension_line.GetEndPoint(1)),
                bounds))
        try:
            segment_values = []
            segments = dimension.Segments
            try:
                segment_count = segments.Size
            except Exception:
                segment_count = 0
            lines.append(u"Число сегментов созданного размера: {}".format(
                segment_count))
            for segment_index in range(segment_count):
                segment = segments.get_Item(segment_index)
                try:
                    value_text = segment.ValueString
                except Exception:
                    value_text = None
                if not value_text:
                    try:
                        value_text = u"{:.0f} мм".format(segment.Value * MM_IN_FOOT)
                    except Exception:
                        value_text = u"не определено"
                segment_values.append(unicode(value_text))
            if segment_values:
                lines.append(u"Сегменты созданного размера: {}".format(
                    u" | ".join(segment_values)))
        except Exception as error:
            lines.append(u"Сегменты созданного размера не прочитаны: {}".format(
                unicode(error)))
        if plan is not None:
            station_lines = []
            for index, candidate in enumerate(plan["candidates"]):
                try:
                    stable = candidate["reference"].ConvertToStableRepresentation(doc)
                except Exception as error:
                    stable = u"ошибка: {}".format(unicode(error))
                station_lines.append(
                    u"{}: лоток/фитинг {} | точка {} | координата {:.0f} мм | {}".format(
                        index + 1,
                        _element_id(candidate["element"]),
                        _point_text(candidate["point"]),
                        candidate["coordinate"] * MM_IN_FOOT,
                        stable))
            lines.append(u"Станции этой цепочки:\n{}".format(
                u"\n".join(station_lines)))
    if turn_reference_trace:
        lines.append(u"Выбор Reference на косых поворотах:\n{}".format(
            u"\n".join(turn_reference_trace)))
    if fitting_lookup_trace:
        lines.append(u"Поиск фитингов у концов лотков:\n{}".format(
            u"\n".join(fitting_lookup_trace)))
    return u"\n\n".join(lines)


def _reference_debug_text(reference):
    try:
        element = doc.GetElement(reference.ElementId)
        element_id = _element_id(element)
    except Exception:
        element = None
        element_id = u"не определён"
    try:
        geometry_object = element.GetGeometryObjectFromReference(reference)
        geometry_name = geometry_object.GetType().Name if geometry_object else u"не получен"
        if isinstance(geometry_object, PlanarFace):
            normal = geometry_object.FaceNormal
            geometry_name += u" | normal=({:.3f}; {:.3f}; {:.3f})".format(
                normal.X, normal.Y, normal.Z)
    except Exception as error:
        geometry_name = u"ошибка: {}".format(unicode(error))
    try:
        stable = reference.ConvertToStableRepresentation(doc)
    except Exception as error:
        stable = u"ошибка: {}".format(unicode(error))
    return u"ID {} | {} | {}".format(element_id, geometry_name, stable)


def _fitting_reference_debug_lines(elements):
    """Read-only inventory of stable references defined by fitting families."""
    reference_types = (
        FamilyInstanceReferenceType.Left,
        FamilyInstanceReferenceType.Right,
        FamilyInstanceReferenceType.Front,
        FamilyInstanceReferenceType.Back,
        FamilyInstanceReferenceType.StrongReference,
        FamilyInstanceReferenceType.WeakReference,
    )
    seen = set()
    lines = []
    for element in elements:
        if not _is_fitting(element) or _element_id(element) in seen:
            continue
        seen.add(_element_id(element))
        details = []
        for reference_type in reference_types:
            try:
                references = element.GetReferences(reference_type)
                count = len(references) if references else 0
                if count:
                    details.append(u"{}={}".format(reference_type, count))
            except Exception:
                pass
        lines.append(u"ID {}: {}".format(
            _element_id(element), u", ".join(details) if details else u"нет"))
    return lines[:8]


def _weak_reference_details(elements):
    """Read-only details needed to identify the directional fitting planes."""
    lines = []
    seen = set()
    for element in elements:
        element_id = _element_id(element)
        if not _is_fitting(element) or element_id in seen:
            continue
        seen.add(element_id)
        try:
            references = element.GetReferences(FamilyInstanceReferenceType.WeakReference)
        except Exception:
            references = None
        if not references:
            continue
        for index, reference in enumerate(references):
            try:
                stable = reference.ConvertToStableRepresentation(doc)
            except Exception:
                stable = u"не получено"
            try:
                reference_type = unicode(element.GetReferenceType(reference))
            except Exception:
                reference_type = u"не определён"
            try:
                geometry = element.GetGeometryObjectFromReference(reference)
                geometry_name = geometry.GetType().Name if geometry else u"None"
            except Exception:
                geometry = None
                geometry_name = u"ошибка"
            if isinstance(geometry, PlanarFace):
                normal = geometry.FaceNormal
                detail = u"PlanarFace normal=({:.3f}; {:.3f}; {:.3f}), origin=({:.0f}; {:.0f}; {:.0f}) мм".format(
                    normal.X, normal.Y, normal.Z,
                    _mm(geometry.Origin.X), _mm(geometry.Origin.Y), _mm(geometry.Origin.Z))
            else:
                detail = geometry_name
            lines.append(u"ID {} | weak #{} | {} | {} | {}".format(
                element_id, index + 1, reference_type, detail, stable))
    return lines[:20]


def _elevation_plan_debug_text(plans, issues, selected_elements):
    lines = [u"Проверка Reference для трассы с перепадом отметки.",
             u"Размеры в этом запуске не создавались."]
    lines.append(u"Найденные планы:")
    for plan in plans:
        axis = plan["axis"]
        lines.append(u"{}: {} привязок, axis=({:.3f}; {:.3f}; {:.3f}), элементы [{}]".format(
            plan["kind"], len(plan["candidates"]), axis.X, axis.Y, axis.Z,
            _group_label(plan["elements"])))
        if unicode(plan["kind"]).lower() != u"горизонтальная":
            continue
        lines.append(u"\nГоризонтальная цепочка: {} привязок.".format(
            len(plan["candidates"])))
        for candidate in plan["candidates"]:
            lines.append(_reference_debug_text(candidate["reference"]))
    if issues:
        lines.append(u"\nНеобработано:\n{}".format(u"\n".join(issues)))
    fitting_lines = _fitting_reference_debug_lines(selected_elements)
    if fitting_lines:
        lines.append(u"\nReference-плоскости фитингов:\n{}".format(
            u"\n".join(fitting_lines)))
    weak_details = _weak_reference_details(selected_elements)
    if weak_details:
        lines.append(u"\nДетали WeakReference фитингов:\n{}".format(
            u"\n".join(weak_details)))
    return u"\n\n".join(lines)


def _manual_dimension_reference_text(dimension):
    """Read native references from a user-created dimension without edits."""
    dimension_direction_text = None
    try:
        dimension_curve = dimension.Curve
        dimension_direction = dimension_curve.GetEndPoint(1).Subtract(
            dimension_curve.GetEndPoint(0)).Normalize()
        dimension_direction_text = (
            u"Размерная линия: ({:.3f}; {:.3f}; {:.3f})"
            .format(dimension_direction.X, dimension_direction.Y,
                    dimension_direction.Z))
    except Exception:
        pass
    lines = [u"Диагностика выбранного ручного размера."]
    try:
        references = dimension.References
    except Exception as error:
        return u"Не удалось прочитать References: {}".format(unicode(error))
    count = 0
    lines.append(u"Режим анализа: размер не изменяется; новые размеры не создаются.")
    if dimension_direction_text:
        lines.append(dimension_direction_text)
    for reference in references:
        count += 1
        category_name = u"не определена"
        try:
            element = doc.GetElement(reference.ElementId)
            category_name = (element.Category.Name if element and element.Category
                             else u"без категории")
            element_id = _element_id(element) if element else u"не найден"
        except Exception:
            element_id = u"не найден"
        try:
            reference_type = unicode(reference.ElementReferenceType)
        except Exception:
            reference_type = u"не определён"
        try:
            stable = reference.ConvertToStableRepresentation(doc)
        except Exception as error:
            stable = u"ошибка: {}".format(unicode(error))
        try:
            geometry = element.GetGeometryObjectFromReference(reference) if element else None
            geometry_name = geometry.GetType().Name if geometry else u"None"
            geometry_name = u"category={} | {}".format(category_name, geometry_name)
            if isinstance(geometry, Edge):
                edge_curve = geometry.AsCurve()
                start = edge_curve.GetEndPoint(0)
                end = edge_curve.GetEndPoint(1)
                direction_vector = end.Subtract(start)
                geometry_name += u" | curve={}".format(edge_curve.GetType().Name)
                if direction_vector.GetLength() > 0.000001:
                    direction = direction_vector.Normalize()
                    geometry_name += u" | direction=({:.3f}; {:.3f}; {:.3f})".format(
                        direction.X, direction.Y, direction.Z)
                geometry_name += u" | ends={}-{}".format(
                    _point_text(start), _point_text(end))
            if isinstance(geometry, Line):
                start = geometry.GetEndPoint(0)
                end = geometry.GetEndPoint(1)
                direction = end.Subtract(start).Normalize()
                geometry_name += u" direction=({:.3f}; {:.3f}; {:.3f})".format(
                    direction.X, direction.Y, direction.Z)
                geometry_name += u" ends=({:.0f}; {:.0f})-({:.0f}; {:.0f}) мм".format(
                    start.X * MM_IN_FOOT, start.Y * MM_IN_FOOT,
                    end.X * MM_IN_FOOT, end.Y * MM_IN_FOOT)
        except Exception as error:
            geometry_name = u"ошибка: {}".format(unicode(error))
        stable_chunks = [stable[index:index + 24]
                         for index in range(0, len(stable), 24)]
        lines.append(u"\n#{} | ID {} | {} | {}\n{}".format(
            count, element_id, reference_type, geometry_name,
            u"\n".join(stable_chunks)))
        if element and _is_fitting(element):
            connector_points = _connector_points(element)
            if connector_points:
                lines.append(u"Коннекторы фитинга ID {}: {}".format(
                    element_id,
                    u", ".join(_point_text(point)
                               for point in connector_points)))
    if count == 0:
        lines.append(u"У размера нет доступных References.")
    return u"\n".join(lines)


if not _active_view_is_supported():
    forms.alert(
        u"Команда создаёт размеры только на планах этажей.",
        title=u"Размеры лотков", exitscript=True)

selected_elements = [doc.GetElement(item_id) for item_id in uidoc.Selection.GetElementIds()]
selected_elements = [element for element in selected_elements if element]
if len(selected_elements) == 1 and isinstance(selected_elements[0], Dimension):
    forms.alert(_manual_dimension_reference_text(selected_elements[0]),
                title=u"Размеры лотков — Reference ручного размера", exitscript=True)

elements = _selected_or_picked_elements()
if not elements:
    forms.alert(
        u"Не выбраны прямые кабельные лотки.",
        title=u"Размеры лотков", exitscript=True)

plans, issues = _build_dimension_plans(_connected_groups(elements))
if False and any(_has_height_change(element) for element in elements):
    forms.alert(_elevation_plan_debug_text(plans, issues, elements),
                title=u"Размеры лотков — диагностика Reference", exitscript=True)

if not plans:
    forms.alert(
        u"Не удалось сформировать цепочки размеров.\n\n{}".format(u"\n\n".join(issues)),
        title=u"Размеры лотков", exitscript=True)

dimension_type = _stored_dimension_type()
if dimension_type is None:
    forms.alert(u"Создание размеров отменено.", title=u"Размеры лотков", exitscript=True)

created = 0
created_records = []
occupied_strokes = []
with revit.Transaction(u"Размеры кабельных лотков"):
    for plan in plans:
        try:
            offset, direction, unused_line, strokes = _layout_dimension(
                plan, occupied_strokes)
            dimension, dimension_line = _create_dimension(
                plan, offset, dimension_type, direction)
            created_records.append({"dimension": dimension, "line": dimension_line,
                                    "plan": plan})
            occupied_strokes.extend(_dimension_strokes(plan, dimension_line))
            created += 1
        except Exception as error:
            issues.append(
                u"Группа элементов [{}], {} цепочка: {}"
                .format(_group_label(plan["elements"]), plan["kind"], unicode(error)))
    doc.Regenerate()

success_text, issues_text = _result_text(created, issues)
if issues_text:
    forms.alert(issues_text, title=u"Размеры лотков — результаты")
