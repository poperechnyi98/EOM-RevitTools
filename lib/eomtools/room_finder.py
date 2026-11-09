# -*- coding: utf-8 -*-
"""
Поиск и подсветка (зум активного вида) помещения по номеру/названию/типу —
для кнопки ToolsRooms.panel/FindRoom.

Список Room собирается из активного документа и из ВСЕХ подключённых
связей (та же идея, что room_info._collect_rooms / room_tags._iter_link_rooms),
один раз за сеанс: результат кэшируется в глобальном словаре модуля _CACHE,
а не только на время одного запуска скрипта. pyRevit по умолчанию
переиспользует движок IronPython/CPython между отдельными кликами по кнопке
в рамках одного сеанса Revit ("attached"-движок) — модули lib/ остаются в
sys.modules, поэтому глобальный словарь модуля переживает повторные запуски
кнопки, и повторный поиск в том же проекте не пересобирает список заново.
Кэш живёт на документ (ключ — doc.PathName, либо id(doc) для несохранённого
файла) и сбрасывается только вручную — пунктом «Обновить список» в самом
диалоге кнопки (дешёвого способа отследить «помещения могли измениться» без
собственного слежения за изменениями документа нет, а слушать
Application.DocumentChanged ради одной кнопки — overkill).
"""

import re
from eomtools.i18n import tr

from Autodesk.Revit.DB import (
    BuiltInCategory, BuiltInParameter, FilteredElementCollector,
    RevitLinkInstance, Transform, XYZ,
)

from eomtools.params import get_param_any
from eomtools.room_finder_core import same_level

_CACHE = {}

_NUM_RE = re.compile(r"(\d+)")


def _doc_key(doc):
    try:
        path = doc.PathName
    except Exception:
        path = None
    return path if path else u"id:{}".format(id(doc))


class RoomRecord(object):
    __slots__ = ("room", "number", "name", "level_name", "bbox", "is_host", "room_id")

    def __init__(self, room, number, name, level_name, bbox, is_host):
        self.room = room
        self.number = number
        self.name = name
        self.level_name = level_name
        self.bbox = bbox  # (XYZ min, XYZ max) в координатах хоста, либо None
        self.is_host = is_host  # помещение из самой модели, а не из связи
        try:
            self.room_id = room.Id.IntegerValue
        except Exception:
            self.room_id = None


def _room_number(room):
    try:
        p = room.get_Parameter(BuiltInParameter.ROOM_NUMBER)
        if p and p.HasValue:
            return (p.AsString() or u"").strip()
    except Exception:
        pass
    return u""


def _room_name(room):
    try:
        p = room.get_Parameter(BuiltInParameter.ROOM_NAME)
        if p and p.HasValue:
            return (p.AsString() or u"").strip()
    except Exception:
        pass
    return u""


def _room_level_name(room):
    try:
        lvl = room.Level
        if lvl is not None:
            return lvl.Name
    except Exception:
        pass
    return None


def _room_placed(room):
    try:
        return room.Area > 0 and room.Location is not None
    except Exception:
        return False


def _room_bbox_host(room, transform):
    """
    Габаритный контейнер помещения в координатах хоста. Через transform
    пропускаются все 8 углов бокса связи (не только центр/Min/Max) —
    у связи может быть поворот (истинный север и т.п.), а не только
    смещение, и охватывающий бокс тогда нужно пересчитать уже в хосте.
    """
    try:
        bbox = room.get_BoundingBox(None)
    except Exception:
        bbox = None
    if bbox is None:
        return None

    corners = [
        XYZ(x, y, z)
        for x in (bbox.Min.X, bbox.Max.X)
        for y in (bbox.Min.Y, bbox.Max.Y)
        for z in (bbox.Min.Z, bbox.Max.Z)
    ]

    try:
        pts = [transform.OfPoint(p) for p in corners]
    except Exception:
        pts = corners

    return (
        XYZ(min(p.X for p in pts), min(p.Y for p in pts), min(p.Z for p in pts)),
        XYZ(max(p.X for p in pts), max(p.Y for p in pts), max(p.Z for p in pts)),
    )


def _collect_from(source_doc, transform, is_host):
    records = []
    try:
        rooms = FilteredElementCollector(source_doc) \
            .OfCategory(BuiltInCategory.OST_Rooms) \
            .WhereElementIsNotElementType() \
            .ToElements()
    except Exception:
        return records

    for room in rooms:
        if not _room_placed(room):
            continue
        records.append(RoomRecord(
            room=room,
            number=_room_number(room),
            name=_room_name(room),
            level_name=_room_level_name(room),
            bbox=_room_bbox_host(room, transform),
            is_host=is_host,
        ))
    return records


def _collect_all(doc):
    records = _collect_from(doc, Transform.Identity, True)

    for link in FilteredElementCollector(doc).OfClass(RevitLinkInstance):
        try:
            linked_doc = link.GetLinkDocument()
        except Exception:
            continue
        if linked_doc is None:
            continue
        try:
            transform = link.GetTotalTransform()
        except Exception:
            continue
        records.extend(_collect_from(linked_doc, transform, False))

    return records


def get_records(doc, force_refresh=False):
    """Список RoomRecord — из кэша модуля, либо собирается заново, если
    force_refresh=True или для этого документа ещё ничего не кэшировано."""
    key = _doc_key(doc)
    if not force_refresh and key in _CACHE:
        return _CACHE[key]

    records = _collect_all(doc)
    _CACHE[key] = records
    return records


def _host_visible_room_ids(doc, view):
    """
    IntegerValue всех Room активного документа, которые Revit реально
    показывает на этом виде — учитывает уровень, View Range, обрезку,
    фазу и т.д. (тот же приём, что build_visible_ids в ZoomToElement).
    None, если вид не поддерживает такой сбор (лист, легенда и т.п.) —
    вызывающий код тогда просто не сужает список по этому признаку.
    """
    try:
        return set(
            eid.IntegerValue
            for eid in FilteredElementCollector(doc, view.Id)
                .OfCategory(BuiltInCategory.OST_Rooms)
                .WhereElementIsNotElementType()
                .ToElementIds()
        )
    except Exception:
        return None


def filter_for_view(records, doc, view):
    """
    Сужает список помещений до тех, что относятся к активному виду.

    Для помещений САМОГО документа — по факту видимости на этом виде
    (_host_visible_room_ids: уровень/View Range/обрезка/фаза — всё, что
    знает про это сам Revit). Для помещений из связей такой проверки нет
    (элементы связи не входят в FilteredElementCollector(doc, view.Id)),
    поэтому для них — по совпадению имени уровня с уровнем активного вида
    (room_finder_core.same_level: код корпуса «ПЧ» = «0» = «00»); грубее, но обычно ровно то, что нужно: «помещения этого этажа».

    Возвращает (filtered, narrowed). narrowed=False и filtered=records
    (без изменений), если сузить нечем (вид без уровня, например 3D/лист)
    или итоговый список оказался пустым — чтобы не оставить пользователя
    с пустым диалогом из-за, например, разного именования уровней в
    хосте и связи.
    """
    view_level = getattr(view, "GenLevel", None)
    view_level_name = view_level.Name if view_level is not None else None
    host_ids = _host_visible_room_ids(doc, view)

    if view_level_name is None and host_ids is None:
        return records, False

    filtered = []
    for r in records:
        if r.is_host and host_ids is not None:
            if r.room_id in host_ids:
                filtered.append(r)
            continue
        if view_level_name is not None and same_level(r.level_name, view_level_name):
            filtered.append(r)

    if not filtered:
        return records, False

    return filtered, True


def type_value(record, type_param_name):
    """Значение параметра-группировки (из настроек) на самом Room. "" —
    если имя параметра не задано в настройках, либо у этого помещения его
    нет/он пуст."""
    if not type_param_name:
        return u""
    value = get_param_any(record.room, type_param_name)
    return value.strip() if value else u""


def number_value(record, number_param_name):
    """
    «Номер помещения» для показа/поиска/сортировки — значение параметра из
    настроек (например, если проектный номер хранится не во встроенном
    ROOM_NUMBER, а в своём общем параметре). Падает обратно на встроенный
    номер (record.number), если имя параметра не задано в настройках или
    у конкретного помещения этот параметр пуст/отсутствует — так список не
    остаётся без номера при неполной настройке или разночтениях по части
    помещений.
    """
    if not number_param_name:
        return record.number
    value = get_param_any(record.room, number_param_name)
    return value.strip() if value else record.number


def natural_key(text):
    """Ключ сортировки, где числовые куски сравниваются как числа — чтобы
    номер «10» шёл после «9», а не перед ним, как при обычной строковой
    сортировке."""
    text = text or u""
    return [int(part) if part.isdigit() else part.lower()
            for part in _NUM_RE.split(text)]


def level_mismatch_hint(record, view, number_display=None):
    """
    None, если можно спокойно зумить (помещение на уровне активного вида,
    либо уровень одной из сторон определить не удалось — тогда лучше
    попытаться зумить, чем ложно ругаться). Иначе — текст подсказки, на
    какой план переключиться (тот же приём, что ZoomToElement.hint_where_to_look).

    number_display — что показать пользователю как «номер»; если не
    передан, берётся встроенный record.number (см. number_value —
    настроенный параметр номера может отличаться от встроенного).
    """
    active_level = getattr(view, "GenLevel", None)
    active_level_name = active_level.Name if active_level is not None else None

    if not active_level_name or not record.level_name:
        return None
    if same_level(record.level_name, active_level_name):
        return None

    if number_display is None:
        number_display = record.number

    return (
        tr(u"Помещение {} «{}» относится к уровню «{}», а на активном виде — "
        u"уровень «{}». Переключитесь на план уровня «{}» и выберите "
        u"помещение снова.").format(
            number_display or u"?", record.name or u"",
            record.level_name, active_level_name, record.level_name
        )
    )


def zoom_to_bbox(uidoc, view, bbox, pad_ratio=0.35, min_pad_ft=3.0):
    """
    Аспект-корректный зум активного вида на bbox (та же идея, что
    ZoomToElement.zoom_center_on_points: прямоугольник помещения обычно
    почти квадратный, и без подгонки под соотношение сторон окна вида
    ZoomAndCenterRectangle центрирует его заметно неточно). Возвращает
    True, если зум применён.
    """
    if bbox is None:
        return False

    min_pt, max_pt = bbox

    uiview = None
    for uv in uidoc.GetOpenUIViews():
        if uv.ViewId == view.Id:
            uiview = uv
            break
    if uiview is None:
        return False

    dx = max_pt.X - min_pt.X
    dy = max_pt.Y - min_pt.Y
    pad = max(max(dx, dy) * pad_ratio, min_pad_ft)

    cx = (min_pt.X + max_pt.X) / 2.0
    cy = (min_pt.Y + max_pt.Y) / 2.0
    half_x = dx / 2.0 + pad
    half_y = dy / 2.0 + pad
    z = (min_pt.Z + max_pt.Z) / 2.0

    try:
        rect = uiview.GetWindowRectangle()
        win_w = abs(rect.Right - rect.Left)
        win_h = abs(rect.Bottom - rect.Top)
        if win_w > 0 and win_h > 0:
            aspect = float(win_w) / float(win_h)
            if half_x / half_y < aspect:
                half_x = half_y * aspect
            else:
                half_y = half_x / aspect
    except Exception:
        pass

    try:
        uiview.ZoomAndCenterRectangle(
            XYZ(cx - half_x, cy - half_y, z),
            XYZ(cx + half_x, cy + half_y, z),
        )
    except Exception:
        return False

    try:
        uidoc.RefreshActiveView()
    except Exception:
        pass

    return True
