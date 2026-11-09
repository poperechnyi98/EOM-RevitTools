# -*- coding: utf-8 -*-
"""Оборудование связей: видимость в виде хоста, ссылки и координаты.

API импортируется лениво, чтобы адаптер можно было проверить вне Revit.
Связанный документ только читается; марки создаются в документе хоста.
"""


def _db():
    import Autodesk.Revit.DB as DB
    return DB


def _id(eid):
    try:
        return eid.Value
    except AttributeError:
        return eid.IntegerValue


class LinkedEquipment(object):
    """Элемент связи с габаритом в координатах текущего документа."""

    def __init__(self, link, element):
        self.link = link
        self.element = element
        self.Id = element.Id
        self.Category = element.Category
        self.Symbol = element.Symbol

    def get_BoundingBox(self, view):
        # Вид хоста нельзя передавать элементу другого документа.
        box = self.element.get_BoundingBox(None)
        if box is None:
            return None
        DB = _db()
        transform = self.link.GetTotalTransform().Multiply(box.Transform)
        points = [transform.OfPoint(DB.XYZ(x, y, z))
                  for x in (box.Min.X, box.Max.X)
                  for y in (box.Min.Y, box.Max.Y)
                  for z in (box.Min.Z, box.Max.Z)]
        result = DB.BoundingBoxXYZ()
        result.Min = DB.XYZ(min(p.X for p in points), min(p.Y for p in points),
                            min(p.Z for p in points))
        result.Max = DB.XYZ(max(p.X for p in points), max(p.Y for p in points),
                            max(p.Z for p in points))
        return result


def equipment_key(element):
    if isinstance(element, LinkedEquipment):
        return (_id(element.link.Id), _id(element.Id))
    return _id(element.Id)


def equipment_reference(element):
    DB = _db()
    if isinstance(element, LinkedEquipment):
        return DB.Reference(element.element).CreateLinkReference(element.link)
    return DB.Reference(element)


def visible_links(doc, view):
    DB = _db()
    return [link for link in DB.FilteredElementCollector(doc, view.Id)
            .OfClass(DB.RevitLinkInstance)
            if link.GetLinkDocument() is not None and not link.IsHidden(view)]


def visible_equipment(doc, view, link):
    """Revit 2024+: штатная видимость связи именно на виде хоста.

    Не заменять сбором всего связанного документа: он включает скрытые
    элементы и оборудование вне текущего вида.
    """
    DB = _db()
    elements = DB.FilteredElementCollector(doc, view.Id, link.Id).OfClass(DB.FamilyInstance)
    return [LinkedEquipment(link, el) for el in elements
            if el.Category is not None and el.Category.CategoryType == DB.CategoryType.Model]


def tagged_equipment(doc, tag):
    """Цели марки, включая связь; ключ содержит Id экземпляра связи."""
    DB = _db()
    try:
        ids = tag.GetTaggedElementIds()  # Revit 2022+
    except AttributeError:
        ids = [tag.TaggedElementId]
    result = []
    for target in ids:
        if target.LinkInstanceId != DB.ElementId.InvalidElementId:
            link = doc.GetElement(target.LinkInstanceId)
            linked_doc = link.GetLinkDocument() if link is not None else None
            el = linked_doc.GetElement(target.LinkedElementId) if linked_doc is not None else None
            result.append(LinkedEquipment(link, el)
                          if isinstance(el, DB.FamilyInstance) else None)
        else:
            el = doc.GetElement(target.HostElementId)
            result.append(el)
    return result
