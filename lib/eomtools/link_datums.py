# -*- coding: utf-8 -*-
u"""
Уровни и оси модели и связи (КФП) — сбор из Revit для кнопки
`Tools.panel/CompareLinkDatums` («Оси и уровни КФП»). Сравнение — в
link_datums_core (без Revit API).

Координаты связи переводятся в систему модели через GetTotalTransform()
экземпляра связи, поэтому сравнивается то, что видно в модели: связь,
вставленная со сдвигом, даст расхождение по всем осям/уровням.

Отметки — от базовой точки проекта модели (как «Отметка» уровня при
«Базе отсчёта отметок» = базовая точка проекта), в мм.
"""

from Autodesk.Revit.DB import (
    Arc, BasePoint, Element, FilteredElementCollector, Grid, Level, Line,
    RevitLinkInstance, XYZ,
)

from eomtools import link_datums_core

FT_TO_MM = 304.8


def _safe_name(el):
    if el is None:
        return u""
    try:
        return Element.Name.GetValue(el) or u""
    except Exception:
        try:
            return el.Name or u""
        except Exception:
            return u""


def loaded_links(doc):
    u"""Загруженные экземпляры связей Revit."""
    return [link for link in FilteredElementCollector(doc).OfClass(RevitLinkInstance)
            if link.GetLinkDocument() is not None]


def link_label(link):
    u"""«Имя.rvt : позиция» — имя экземпляра связи (как в диспетчере проекта)."""
    return _safe_name(link)


def matching_links(links, keyword):
    u"""Связи, в имени которых (экземпляра или файла) есть keyword."""
    result = []
    for link in links:
        names = [link_label(link)]
        try:
            names.append(link.GetLinkDocument().Title or u"")
        except Exception:
            pass
        if any(link_datums_core.link_matches(n, keyword) for n in names):
            result.append(link)
    return result


def _base_z(doc):
    u"""Отметка базовой точки проекта во внутренних координатах, футы."""
    try:
        return BasePoint.GetProjectBasePoint(doc).Position.Z
    except Exception:
        return 0.0


def _xy_mm(point):
    return (point.X * FT_TO_MM, point.Y * FT_TO_MM)


def _id_int(eid):
    try:
        return eid.Value  # Revit 2024+
    except Exception:
        return eid.IntegerValue


def collect_levels(source_doc, transform, base_z):
    u"""[{"name", "elevation" (мм от базы модели), "id"}] уровней документа."""
    result = []
    for level in FilteredElementCollector(source_doc).OfClass(Level):
        z = level.ProjectElevation
        if transform is not None:
            z = transform.OfPoint(XYZ(0, 0, z)).Z
        result.append({
            "name": _safe_name(level),
            "elevation": (z - base_z) * FT_TO_MM,
            "id": level.Id,
            "id_int": _id_int(level.Id),
        })
    return result


def _grid_shape(curve, transform):
    if transform is not None:
        curve = curve.CreateTransformed(transform)
    if isinstance(curve, Line):
        return "line", (_xy_mm(curve.GetEndPoint(0)), _xy_mm(curve.GetEndPoint(1)))
    if isinstance(curve, Arc):
        return "arc", (_xy_mm(curve.Center), curve.Radius * FT_TO_MM)
    return None, None


def collect_grids(source_doc, transform):
    u"""[{"name", "id", "line"|"arc"}] осей документа (в плане, мм)."""
    result = []
    for grid in FilteredElementCollector(source_doc).OfClass(Grid):
        item = {"name": _safe_name(grid), "id": grid.Id, "id_int": _id_int(grid.Id)}
        try:
            kind, shape = _grid_shape(grid.Curve, transform)
        except Exception:
            kind, shape = None, None
        if kind:
            item[kind] = shape
        result.append(item)
    return result


def compare(doc, link, tolerance_mm):
    u"""(строки уровней, строки осей) — модель против связи link."""
    link_doc = link.GetLinkDocument()
    transform = link.GetTotalTransform()
    base_z = _base_z(doc)
    levels = link_datums_core.compare_levels(
        collect_levels(doc, None, base_z), collect_levels(link_doc, transform, base_z),
        tolerance_mm)
    grids = link_datums_core.compare_grids(
        collect_grids(doc, None), collect_grids(link_doc, transform), tolerance_mm)
    return levels, grids
