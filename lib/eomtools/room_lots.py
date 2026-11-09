# -*- coding: utf-8 -*-
"""
Анализ помещений связанной модели по лотам — для кнопки
ToolsRooms.panel/RoomLots («Двухуровневые лоты»).

Собирает Room из подключённых связей (только из связей — помещения самой
модели не берутся), для каждого читает имя лота и номер секции (имена
параметров — из настроек room_lots_settings, они проектные и в коде не
зашиты), раскладывает по уровням и сортирует по имени лота → номеру
секции → номеру помещения.

Двухуровневый (многоуровневый) лот — это лот, у которого помещения с
одним и тем же значением имени лота стоят на двух и более РАЗНЫХ уровнях.
Лоты сравниваются в пределах одной связи (одинаковое имя лота в двух
разных связях — это разные лоты).
"""

from Autodesk.Revit.DB import (
    BuiltInCategory, BuiltInParameter, FilteredElementCollector,
    RevitLinkInstance,
)
from eomtools.i18n import tr

from eomtools.params import get_param_any
from eomtools.room_finder import natural_key


class LinkSource(object):
    """Одна загруженная связанная модель (несколько экземпляров одной и той
    же связи схлопываются в один источник — помещения в них одинаковые)."""

    def __init__(self, link_doc, name):
        self.doc = link_doc
        self.name = name

    def __str__(self):
        return self.name


class LotRoom(object):
    __slots__ = ("link_name", "room_id", "number", "name", "level_name",
                 "level_elev", "lot", "section")

    def __init__(self, link_name, room_id, number, name, level_name,
                 level_elev, lot, section):
        self.link_name = link_name
        self.room_id = room_id
        self.number = number
        self.name = name
        self.level_name = level_name
        self.level_elev = level_elev
        self.lot = lot
        self.section = section


class Lot(object):
    """Все помещения одного имени лота в одной связи."""

    def __init__(self, link_name, lot):
        self.link_name = link_name
        self.lot = lot
        self.rooms = []

    def levels(self):
        """[(имя уровня, отметка)] — уникальные, снизу вверх."""
        seen = {}
        for r in self.rooms:
            seen.setdefault(r.level_name, r.level_elev)
        return sorted(seen.items(), key=lambda kv: (kv[1], kv[0]))

    def sections(self):
        return sorted(set(r.section for r in self.rooms if r.section),
                      key=natural_key)

    def is_multilevel(self):
        return len(self.levels()) > 1


def _link_doc_key(link_doc):
    try:
        path = link_doc.PathName
    except Exception:
        path = None
    return path if path else u"id:{}".format(id(link_doc))


def _link_name(link_doc, link):
    try:
        title = link_doc.Title
        if title:
            return title
    except Exception:
        pass
    try:
        return link.Name
    except Exception:
        return tr(u"связь")


def get_link_sources(doc):
    """Загруженные связи (без повторов одной и той же модели), у которых
    есть хотя бы одно помещение."""
    sources = []
    seen = set()
    for link in FilteredElementCollector(doc).OfClass(RevitLinkInstance):
        try:
            link_doc = link.GetLinkDocument()
        except Exception:
            link_doc = None
        if link_doc is None:
            continue
        key = _link_doc_key(link_doc)
        if key in seen:
            continue
        seen.add(key)
        try:
            has_rooms = FilteredElementCollector(link_doc) \
                .OfCategory(BuiltInCategory.OST_Rooms) \
                .WhereElementIsNotElementType() \
                .GetElementCount() > 0
        except Exception:
            has_rooms = False
        if has_rooms:
            sources.append(LinkSource(link_doc, _link_name(link_doc, link)))
    sources.sort(key=lambda s: s.name.lower())
    return sources


def _builtin_string(room, bip):
    try:
        p = room.get_Parameter(bip)
        if p and p.HasValue:
            return (p.AsString() or u"").strip()
    except Exception:
        pass
    return u""


def _room_placed(room):
    try:
        return room.Area > 0 and room.Location is not None
    except Exception:
        return False


def _room_level(room):
    try:
        lvl = room.Level
        if lvl is not None:
            return lvl.Name, lvl.Elevation
    except Exception:
        pass
    return tr(u"(без уровня)"), 0.0


def _value(room, param_name):
    if not param_name:
        return u""
    v = get_param_any(room, param_name)
    return v.strip() if v else u""


def collect_rooms(sources, lot_param_name, section_param_name):
    """
    LotRoom для всех размещённых помещений выбранных связей. Возвращает
    (rooms, skipped_unplaced) — неразмещённые/неокружённые помещения
    (площадь 0) пропускаются и только считаются.
    """
    rooms = []
    skipped = 0
    for src in sources:
        try:
            elements = FilteredElementCollector(src.doc) \
                .OfCategory(BuiltInCategory.OST_Rooms) \
                .WhereElementIsNotElementType() \
                .ToElements()
        except Exception:
            continue
        for room in elements:
            if not _room_placed(room):
                skipped += 1
                continue
            level_name, level_elev = _room_level(room)
            rooms.append(LotRoom(
                link_name=src.name,
                room_id=room.Id.IntegerValue,
                number=_builtin_string(room, BuiltInParameter.ROOM_NUMBER),
                name=_builtin_string(room, BuiltInParameter.ROOM_NAME),
                level_name=level_name,
                level_elev=level_elev,
                lot=_value(room, lot_param_name),
                section=_value(room, section_param_name),
            ))
    return rooms, skipped


def room_sort_key(r):
    """Имя лота → номер секции → номер помещения (числа — как числа).
    Помещения без имени лота — в конце."""
    return (not r.lot, natural_key(r.lot), natural_key(r.section),
            natural_key(r.number))


def group_by_level(rooms):
    """[(имя уровня, отметка, [LotRoom отсортированы])] — уровни снизу вверх.
    Уровни разных связей с одинаковым именем сливаются в один."""
    by_level = {}
    for r in rooms:
        entry = by_level.setdefault(r.level_name, [r.level_elev, []])
        entry[1].append(r)
    result = []
    for level_name, (elev, level_rooms) in by_level.items():
        level_rooms.sort(key=room_sort_key)
        result.append((level_name, elev, level_rooms))
    result.sort(key=lambda t: (t[1], t[0]))
    return result


def group_by_lot(rooms):
    """Lot для каждого непустого имени лота (в пределах связи),
    отсортированы по имени лота → первой секции. Помещения без имени
    лота в лоты не попадают."""
    lots = {}
    for r in rooms:
        if not r.lot:
            continue
        key = (r.link_name, r.lot)
        lot = lots.get(key)
        if lot is None:
            lot = lots[key] = Lot(r.link_name, r.lot)
        lot.rooms.append(r)
    result = list(lots.values())
    for lot in result:
        lot.rooms.sort(key=lambda r: (r.level_elev,) + room_sort_key(r))
    result.sort(key=lambda l: (
        natural_key(l.lot),
        natural_key((l.sections() or [u""])[0]),
        l.link_name.lower(),
    ))
    return result


def multilevel_lots(rooms):
    """Только лоты, помещения которых стоят на двух и более уровнях."""
    return [lot for lot in group_by_lot(rooms) if lot.is_multilevel()]
