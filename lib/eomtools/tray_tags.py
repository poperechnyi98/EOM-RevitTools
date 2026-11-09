# -*- coding: utf-8 -*-
"""
Марки кабельных лотков на активном виде, разложенные ровно и без
пересечений — кнопка «Марки лотков» (Tools.panel/TagTrays). То же, что
«Марки оборудования» (``equipment_tags.py``), и та же раскладка
(``tag_layout.layout``): кучки, стопки/колонки на одном отступе, полки
выносок; отличается только то, что такое «элемент» на виде.

Лоток — не семейство, а прямой участок (``CableTray``). Соединительные
детали (отводы, тройники — экземпляры семейств категории «Соединительные
детали кабельных лотков») сюда не попадают вовсе: ни выбор рамкой, ни
выделение их не берут. Лотки под наклоном (ось круче
``max_slope_deg`` к горизонтали, вертикальные тоже) в режиме «Поставить»
пропускаются.

Геометрия на виде (чистая часть — ``tray_tags_core``): ось лотка
проецируется на вид и обрезается рамкой подрезки, марка смотрит в
середину видимой части. У параллельных лотков рядом (одна трасса) точки
марок расходятся вдоль трассы лесенкой, а марки встают одним блоком —
стопкой над горизонтальной трассой, колонкой у вертикальной — иначе
выноски от середин легли бы друг на друга. «Прямоугольник элемента» — кусок полосы лотка у
этой середины; вся полоса (и других лотков) кусками — препятствия.
Полуширина полосы — проекция сечения (ширина × высота) на вид: на плане
это половина ширины, на разрезе вдоль лотка — половина высоты. Конец
выноски — свободный (``Free``), ровно на краю лотка напротив излома;
за лотком при его сдвиге он не ездит (updater ``tag_leader_follow``
следит только за семействами, а присоединённый конец Revit ставит на
лоток сам, не напротив марки) — после правки трассы кнопку запускают
ещё раз в режиме «Разложить».
"""

from Autodesk.Revit.DB import (
    BuiltInCategory, ElementId, FilteredElementCollector, IndependentTag,
    LocationCurve, TextNote, XYZ
)
from Autodesk.Revit.DB.Electrical import CableTray
from Autodesk.Revit.UI.Selection import ISelectionFilter

from eomtools import equipment_tags, tag_layout, tray_tags_core
from eomtools.equipment_tags import id_int, tagged_local_ids

MM_TO_FT = equipment_tags.MM_TO_FT

# «прямоугольник элемента» у середины лотка — не короче, мм на листе
_MIN_ANCHOR_MM = 2.0


def is_tray(el):
    """Прямой участок кабельного лотка (соединительные детали — нет)."""
    return isinstance(el, CableTray)


class TrayFilter(ISelectionFilter):
    def AllowElement(self, elem):
        return is_tray(elem)

    def AllowReference(self, reference, position):
        return True


class TrayTagFilter(ISelectionFilter):
    """Марки, смотрящие на лоток."""

    def AllowElement(self, elem):
        if not isinstance(elem, IndependentTag):
            return False
        ids = tagged_local_ids(elem)
        return len(ids) == 1 and is_tray(elem.Document.GetElement(ElementId(ids[0])))

    def AllowReference(self, reference, position):
        return True


def _xyz_tuple(p):
    return (p.X, p.Y, p.Z)


def end_points(tray):
    loc = tray.Location
    if not isinstance(loc, LocationCurve):
        return None
    c = loc.Curve
    return c.GetEndPoint(0), c.GetEndPoint(1)


def is_sloped(tray, max_deg):
    pts = end_points(tray)
    if pts is None:
        return False
    return tray_tags_core.is_sloped(_xyz_tuple(pts[0]), _xyz_tuple(pts[1]), max_deg)


def resolve_trays(doc, picked):
    """Выбранное → уникальные лотки (марки → их лотки)."""
    result, seen = [], set()
    for el in picked:
        if isinstance(el, IndependentTag):
            targets = [doc.GetElement(ElementId(i)) for i in tagged_local_ids(el)]
        else:
            targets = [el]
        for t in targets:
            if t is None or not is_tray(t) or id_int(t.Id) in seen:
                continue
            seen.add(id_int(t.Id))
            result.append(t)
    return result


def resolve_tag_pairs(doc, view, picked):
    """Режим «Разложить»: выбранное → [(лоток, марка)]; у выбранного
    лотка — все его марки на виде."""
    by_tray = None
    result, seen = [], set()
    for item in picked:
        pairs = []
        if isinstance(item, IndependentTag):
            ids = tagged_local_ids(item)
            if len(ids) == 1:
                pairs.append((doc.GetElement(ElementId(ids[0])), item))
        elif is_tray(item):
            if by_tray is None:
                by_tray = equipment_tags.tags_by_element(doc, view)
            pairs.extend((item, t) for t in by_tray.get(id_int(item.Id), []))
        for tray, tag in pairs:
            if tray is not None and is_tray(tray) and id_int(tag.Id) not in seen:
                seen.add(id_int(tag.Id))
                result.append((tray, tag))
    return result


# ------------------------------------------------------------
# ЛОТОК НА ВИДЕ
# ------------------------------------------------------------

def _crop_rect(view, frame):
    try:
        if view.CropBoxActive:
            return frame.rect(view.CropBox)
    except Exception:
        pass
    return None


def _half_width(tray, frame, p0, p1, a, b):
    """Полуширина полосы лотка на виде: сечение (ширина в плане, высота
    по вертикали) в направлении поперёк оси на виде."""
    d = p1 - p0
    horiz = XYZ(d.X, d.Y, 0)
    side = XYZ(-horiz.Y, horiz.X, 0).Normalize() if horiz.GetLength() > 1e-9 else XYZ.BasisX
    du, dv = b[0] - a[0], b[1] - a[1]
    ln = (du * du + dv * dv) ** 0.5
    if ln < 1e-6:  # лоток виден с торца
        return None
    across = frame.r.Multiply(-dv / ln) + frame.u.Multiply(du / ln)
    try:
        width, height = tray.Width, tray.Height
    except Exception:
        width = height = 0.0
    return 0.5 * (abs(across.DotProduct(side)) * width +
                  abs(across.DotProduct(XYZ.BasisZ)) * height)


def tray_strip(tray, view, frame):
    """
    Видимая часть лотка на виде: (a, b, half_w, depth) — концы оси (u, v)
    в рамке подрезки, полуширина полосы, глубина середины; None — лотка
    на виде нет. Если ось вида смотрит вдоль лотка (разрез поперёк),
    полоса вырождается: a == b, half_w — по габариту лотка на виде.
    """
    pts = end_points(tray)
    if pts is None:
        return None
    p0, p1 = pts
    a, b = frame.uv(p0), frame.uv(p1)
    t0, t1 = 0.0, 1.0
    crop = _crop_rect(view, frame)
    if crop is not None:
        clipped = tray_tags_core.clip_segment(a, b, crop)
        if clipped is None:
            return None
        t0, t1 = clipped
    mid = p0 + (p1 - p0).Multiply((t0 + t1) * 0.5)
    depth = frame.depth(mid)
    a, b = tray_tags_core.lerp(a, b, t0), tray_tags_core.lerp(a, b, t1)
    half_w = _half_width(tray, frame, p0, p1, a, b)
    if half_w is None:
        r = frame.rect(tray.get_BoundingBox(view))
        if r is None:
            return None
        c = tag_layout.rect_center(r)
        return c, c, max(tag_layout.rect_w(r), tag_layout.rect_h(r)) * 0.5, depth
    return a, b, half_w, depth


# ------------------------------------------------------------
# ПРЕПЯТСТВИЯ
# ------------------------------------------------------------

def _obstacle_rects(doc, view, frame, own_ids, strips):
    """Все лотки на виде кусками, соединительные детали, чужие марки и
    тексты. strips — {id лотка: полоса} уже посчитанных."""
    rects = []
    for tray in FilteredElementCollector(doc, view.Id).OfClass(CableTray):
        s = strips.get(id_int(tray.Id))
        if s is None:
            try:
                s = tray_strip(tray, view, frame)
            except Exception:
                s = None
        if s is not None:
            rects.extend(tray_tags_core.strip_pieces(s[0], s[1], s[2]))
    fittings = (FilteredElementCollector(doc, view.Id)
                .OfCategory(BuiltInCategory.OST_CableTrayFitting)
                .WhereElementIsNotElementType())
    for el in fittings:
        r = frame.rect(el.get_BoundingBox(view))
        if r is not None:
            rects.append(r)
    for cls in (IndependentTag, TextNote):
        for el in FilteredElementCollector(doc, view.Id).OfClass(cls):
            if id_int(el.Id) in own_ids:
                continue
            r = frame.rect(el.get_BoundingBox(view))
            if r is not None:
                rects.append(r)
    return rects


# ------------------------------------------------------------
# ОСНОВНОЙ ПРОХОД
# ------------------------------------------------------------

def run(doc, view, entries, settings):
    """
    Ставит и раскладывает марки лотков на view. entries — [(лоток, марка
    или None, ElementId типа марки)]: марка None — создать. Двигаются
    ровно эти марки, прочие на виде — препятствия. Вызывать внутри
    транзакции. Возвращает словарь статистики.
    """
    stats = {"created": 0, "moved": 0, "no_bbox": 0, "failed": 0,
             "overlaps": 0, "crossings": 0, "upper_floor": 0}

    k = MM_TO_FT * float(view.Scale or 1)
    offset = settings["offset_mm"] * k
    gap = settings["gap_mm"] * k
    shelf = settings["shelf_mm"] * k
    cluster_dist = settings["cluster_mm"] * k
    min_anchor = _MIN_ANCHOR_MM * k

    frame = equipment_tags.ViewFrame(view)

    strips = {}
    for tray, _tag, _type in entries:
        key = id_int(tray.Id)
        if key in strips:
            continue
        try:
            strips[key] = tray_strip(tray, view, frame)
        except Exception:
            strips[key] = None
        if strips[key] is None:
            stats["no_bbox"] += 1

    # где на лотке марка: середина, у параллельных лотков рядом — лесенкой
    at, group = tray_tags_core.stagger_anchors(
        [(key, s[0], s[1]) for key, s in strips.items() if s is not None],
        cluster_dist, offset)

    work = []  # (tray, tag)
    for tray, tag, type_id in entries:
        s = strips.get(id_int(tray.Id))
        if s is None:
            continue
        if tag is None:
            a, b, _hw, depth = s
            try:
                point = frame.xyz(tray_tags_core.lerp(a, b, at[id_int(tray.Id)]), depth)
                tag = equipment_tags._create_tag(doc, view, tray, type_id, point)
                stats["created"] += 1
            except Exception:
                stats["failed"] += 1
                continue
        work.append((tray, tag))

    if not work:
        return stats

    # габарит марок без выноски
    for _tray, tag in work:
        try:
            tag.HasLeader = False
        except Exception:
            pass
    doc.Regenerate()

    items = []
    info = {}
    two_floor_cache = {}
    for tray, tag in work:
        trect = frame.rect(tag.get_BoundingBox(view))
        if trect is None:
            stats["failed"] += 1
            continue
        attach_v = (trect[1] + trect[3]) * 0.5
        if equipment_tags.upper_floor_only(doc, tag, two_floor_cache):
            trect = (trect[0], attach_v, trect[2], trect[3])
            stats["upper_floor"] += 1
        tray_key = id_int(tray.Id)
        a, b, half_w, depth = strips[tray_key]
        t = at[tray_key]
        head = tag.TagHeadPosition
        hu, hv = frame.uv(head)
        cu, cv = tag_layout.rect_center(trect)
        key = id_int(tag.Id)
        info[key] = (tray, tag, (hu - cu, hv - cv), frame.depth(head))
        items.append(tag_layout.TagItem(
            key, tray_tags_core.lerp(a, b, t),
            tray_tags_core.anchor_rect(a, b, half_w, min_anchor, t),
            (tag_layout.rect_w(trect), tag_layout.rect_h(trect)),
            attach_dv=attach_v - cv, group=group[tray_key]
        ))

    own_ids = set(info.keys())
    obstacles = _obstacle_rects(doc, view, frame, own_ids, strips)

    # конец выноски ставим на край полосы сами (у косого лотка край
    # габарита куска — не край лотка)
    placements = tag_layout.layout(items, obstacles, offset=offset, gap=gap,
                                   shelf=shelf, cluster_dist=cluster_dist,
                                   start_at_edge=False)
    rect_by_key = dict((it.key, it.elem_rect) for it in items)
    for p in placements:
        tray = info[p.key][0]
        a, b, half_w, _depth = strips[id_int(tray.Id)]
        if a == b:  # лоток виден с торца — край его габарита
            p.end = tag_layout.exit_point(p.end, p.elbow, rect_by_key[p.key])
        else:
            p.end = tray_tags_core.strip_exit(p.end, p.elbow, a, b, half_w)
    stats["overlaps"], stats["crossings"] = tag_layout.count_conflicts(placements, items)

    for p in placements:
        tray, tag, (ou, ov), depth = info[p.key]
        cu, cv = tag_layout.rect_center(p.tag_rect)
        try:
            tag.TagHeadPosition = frame.xyz((cu + ou, cv + ov), depth)
            equipment_tags._set_leader(tag, equipment_tags._tag_reference(tag, tray),
                                       frame.xyz(p.end, depth), frame.xyz(p.elbow, depth))
            stats["moved"] += 1
        except Exception:
            stats["failed"] += 1

    return stats
