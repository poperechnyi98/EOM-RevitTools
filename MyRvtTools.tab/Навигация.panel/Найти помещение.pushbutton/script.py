# -*- coding: utf-8 -*-
from Autodesk.Revit.DB import BuiltInCategory, BuiltInParameter, FilteredElementCollector, RevitLinkInstance, Transform, XYZ
from pyrevit import forms, revit


__title__ = u"Найти\nпомещение"
__doc__ = u"Ищет помещения в текущей модели и подключённых связях."

doc = revit.doc
uidoc = revit.uidoc
view = doc.ActiveView


class RoomOption(object):
    def __init__(self, room, source_name, bbox):
        self.room = room
        self.source_name = source_name
        self.bbox = bbox
        self.number = _value(room, BuiltInParameter.ROOM_NUMBER)
        self.name = _value(room, BuiltInParameter.ROOM_NAME)

    def __str__(self):
        suffix = u" [{}]".format(self.source_name) if self.source_name else u""
        return u"{} — {}{}".format(self.number or u"?", self.name or u"Без имени", suffix)


def _value(room, parameter):
    try:
        value = room.get_Parameter(parameter).AsString()
        return value.strip() if value else u""
    except Exception:
        return u""


def _bbox_in_host(room, transform):
    bbox = room.get_BoundingBox(None)
    if bbox is None:
        return None
    points = []
    for x in (bbox.Min.X, bbox.Max.X):
        for y in (bbox.Min.Y, bbox.Max.Y):
            for z in (bbox.Min.Z, bbox.Max.Z):
                points.append(transform.OfPoint(XYZ(x, y, z)))
    return (XYZ(min(point.X for point in points), min(point.Y for point in points), min(point.Z for point in points)),
            XYZ(max(point.X for point in points), max(point.Y for point in points), max(point.Z for point in points)))


def _collect_rooms(source_doc, transform, source_name):
    result = []
    rooms = FilteredElementCollector(source_doc).OfCategory(BuiltInCategory.OST_Rooms).WhereElementIsNotElementType()
    for room in rooms:
        try:
            if room.Area <= 0 or room.Location is None:
                continue
            bbox = _bbox_in_host(room, transform)
            if bbox:
                result.append(RoomOption(room, source_name, bbox))
        except Exception:
            pass
    return result


def _zoom(bbox):
    active_ui_view = None
    for ui_view in uidoc.GetOpenUIViews():
        if ui_view.ViewId == view.Id:
            active_ui_view = ui_view
            break
    if active_ui_view is None:
        return False
    minimum, maximum = bbox
    pad = 5.0
    active_ui_view.ZoomAndCenterRectangle(
        XYZ(minimum.X - pad, minimum.Y - pad, minimum.Z),
        XYZ(maximum.X + pad, maximum.Y + pad, maximum.Z))
    uidoc.RefreshActiveView()
    return True


options = _collect_rooms(doc, Transform.Identity, u"")
for link in FilteredElementCollector(doc).OfClass(RevitLinkInstance):
    try:
        linked_doc = link.GetLinkDocument()
        if linked_doc:
            options.extend(_collect_rooms(linked_doc, link.GetTotalTransform(), linked_doc.Title))
    except Exception:
        pass

if not options:
    forms.alert(u"Помещения не найдены. Проверьте, что модель или связь с помещениями загружена.",
                title=u"Найти помещение", exitscript=True)

options.sort(key=lambda item: ((item.number or u"").lower(), (item.name or u"").lower()))
picked = forms.SelectFromList.show(options, title=u"Найти помещение", button_name=u"Подсветить", multiselect=False)
if picked and not _zoom(picked.bbox):
    forms.alert(u"Откройте план, разрез или 3D-вид модели и повторите команду.", title=u"Найти помещение")
