# -*- coding: utf-8 -*-
"""Read room properties and navigate to the selected room in Revit."""
from collections import OrderedDict

from Autodesk.Revit import DB
from Autodesk.Revit.Exceptions import ArgumentException, InvalidOperationException
from System.Collections.Generic import List

NUMBER = u'builtin:{}'.format(int(DB.BuiltInParameter.ROOM_NUMBER))
NAME = u'builtin:{}'.format(int(DB.BuiltInParameter.ROOM_NAME))
DEFAULT_FIELDS = (NUMBER, NAME)
SEARCHABLE = (DB.StorageType.String, DB.StorageType.Integer,
              DB.StorageType.Double, DB.StorageType.ElementId)


def parameter_key(parameter, doc):
    element_id = parameter.Id
    if element_id.Value < 0:
        return u'builtin:{}'.format(element_id.Value)
    if parameter.IsShared:
        return u'shared:{}'.format(parameter.GUID)
    definition = doc.GetElement(element_id)
    return u'project:{}'.format(definition.UniqueId) if definition is not None else None


def parameter_text(parameter, doc):
    if parameter is None or not parameter.HasValue:
        return u''
    storage = parameter.StorageType
    if storage == DB.StorageType.String:
        value = parameter.AsString()
    elif storage == DB.StorageType.ElementId:
        element_id = parameter.AsElementId()
        element = doc.GetElement(element_id)
        value = DB.Element.Name.GetValue(element) if element is not None else u''
    else:
        value = parameter.AsValueString()
        if not value and storage == DB.StorageType.Integer:
            value = u'{}'.format(parameter.AsInteger())
    return (value or u'').strip()


class Room(object):
    def __init__(self, element):
        self.element = element
        doc = element.Document
        self.number = parameter_text(element.get_Parameter(DB.BuiltInParameter.ROOM_NUMBER), doc)
        self.name = parameter_text(element.get_Parameter(DB.BuiltInParameter.ROOM_NAME), doc)
        level = doc.GetElement(element.LevelId)
        self.level = DB.Element.Name.GetValue(level) if level is not None else u''
        self.values = {NUMBER: self.number, NAME: self.name}

    def search_values(self, keys, parameter_ids):
        missing = set(keys).difference(self.values)
        if missing:
            doc = self.element.Document
            for parameter in self.element.Parameters:
                key = parameter_ids.get(parameter.Id.Value)
                if key in missing:
                    self.values[key] = parameter_text(parameter, doc)
                    missing.remove(key)
                    if not missing:
                        break
            self.values.update((key, u'') for key in missing)
        return tuple(self.values[key] for key in keys)


class Source(object):
    def __init__(self, doc, links):
        self.title = doc.Title if links else u'Текущая модель · {}'.format(doc.Title)
        self.placements = [(link, u'{} · ID {}'.format(self.title, link.Id)
                            if len(links) > 1 else self.title)
                           for link in (links or [None])]
        elements = (DB.FilteredElementCollector(doc).OfCategory(DB.BuiltInCategory.OST_Rooms)
                    .WhereElementIsNotElementType())
        self.rooms = [Room(element) for element in elements
                      if element.Location is not None and element.Area > 0]
        self.rooms.sort(key=lambda room: (room.number.lower(), room.name.lower(),
                                          room.level.lower(), room.element.Id.Value))
        self.fields = {}
        self.parameter_ids = {}
        if self.rooms:
            # Project and shared parameters are bound to the Room category.
            for parameter in self.rooms[0].element.Parameters:
                if parameter.StorageType in SEARCHABLE and parameter.Definition is not None:
                    key = parameter_key(parameter, doc)
                    if key:
                        self.fields[key] = parameter.Definition.Name
                        self.parameter_ids[parameter.Id.Value] = key
            self.fields.update({NUMBER: u'Номер', NAME: u'Имя'})


def collect_sources(host):
    documents = OrderedDict([(host, [])])
    for link in DB.FilteredElementCollector(host).OfClass(DB.RevitLinkInstance):
        doc = link.GetLinkDocument()
        if doc is not None:
            documents.setdefault(doc, []).append(link)
    sources = [Source(doc, links) for doc, links in documents.items()]
    sources[1:] = sorted(sources[1:], key=lambda source: source.title.lower())
    return [source for source in sources if source.rooms]


def _zoom_rectangle(view, ui_view, bbox, transform):
    right, up, direction = view.RightDirection, view.UpDirection, view.ViewDirection
    points = [transform.OfPoint(bbox.Transform.OfPoint(DB.XYZ(x, y, z)))
              for x in (bbox.Min.X, bbox.Max.X)
              for y in (bbox.Min.Y, bbox.Max.Y)
              for z in (bbox.Min.Z, bbox.Max.Z)]
    xs = [right.DotProduct(point) for point in points]
    ys = [up.DotProduct(point) for point in points]
    depth = direction.DotProduct(points[0].Add(points[-1]).Multiply(0.5))
    center = (right.Multiply((min(xs) + max(xs)) / 2.0)
              .Add(up.Multiply((min(ys) + max(ys)) / 2.0))
              .Add(direction.Multiply(depth)))
    width, height = max(xs) - min(xs), max(ys) - min(ys)
    pad = max(max(width, height) * 0.35, 3.0)
    half_width, half_height = width / 2.0 + pad, height / 2.0 + pad
    rect = ui_view.GetWindowRectangle()
    pixel_width = abs(rect.Right - rect.Left)
    pixel_height = abs(rect.Bottom - rect.Top)
    if pixel_width and pixel_height:
        aspect = float(pixel_width) / pixel_height
        half_width = max(half_width, half_height * aspect)
        half_height = max(half_height, half_width / aspect)
    offset = right.Multiply(half_width).Add(up.Multiply(half_height))
    return center.Subtract(offset), center.Add(offset)


def show_in_view(uidoc, room, link):
    view = uidoc.ActiveView
    if (not isinstance(view, (DB.ViewPlan, DB.ViewSection, DB.View3D))
            or (isinstance(view, DB.View3D) and view.IsPerspective)):
        raise ValueError(u'Откройте план, разрез или ортогональный 3D-вид.')
    ui_view = next((item for item in uidoc.GetOpenUIViews() if item.ViewId == view.Id), None)
    bbox = room.element.get_BoundingBox(None)
    if ui_view is None or bbox is None:
        raise ValueError(u'Не удалось определить область помещения на активном виде.')

    transform = link.GetTotalTransform() if link is not None else DB.Transform.Identity
    lower, upper = _zoom_rectangle(view, ui_view, bbox, transform)
    ui_view.ZoomAndCenterRectangle(lower, upper)
    try:
        if link is None:
            ids = List[DB.ElementId]()
            ids.Add(room.element.Id)
            uidoc.Selection.SetElementIds(ids)
        else:
            refs = List[DB.Reference]()
            refs.Add(DB.Reference(room.element).CreateLinkReference(link))
            uidoc.Selection.SetReferences(refs)
    except (ArgumentException, InvalidOperationException):
        # The active view may reject highlighting a room that is not visible there.
        pass
    uidoc.RefreshActiveView()
