# -*- coding: utf-8 -*-
"""
Марки оборудования (IndependentTag) на активном виде, разложенные ровно
и без пересечений — кнопка «Марки оборудования» (Tools.panel/TagEquipment).

Revit сам ставит марку прямо над/сбоку элемента, не глядя на соседей:
рядом стоящее оборудование даёт марки друг на друге. Здесь Revit-часть:
собрать элементы, поставить недостающие марки, измерить их габарит,
отдать всё в чистую раскладку ``tag_layout.layout`` и записать
результат — положение головы марки, конец и излом выноски.

Координаты: всё переводится в 2D-систему вида (u — ``RightDirection``,
v — ``UpDirection``, начало — ``view.Origin``), там же раскладывается,
и обратно в XYZ с сохранением «глубины» (координаты вдоль
``ViewDirection``) самой марки — поэтому работает и на планах, и на
разрезах/фасадах.

Выноска начинается от УГО, а не от семейства. УГО — это то, что
элемент реально рисует на ЭТОМ виде. Его габарит — «прямоугольник
элемента» для раскладки (на него не кладутся марки), центр — точка, куда
смотрит выноска, а конец выноски ставится на край этого габарита. Точка
вставки/габарит семейства целиком не годятся: УГО часто смещено от
точки вставки (к стене, к потолку) и меньше/больше 3D-тела. Два вида УГО:

  - символьные линии — отдельные кривые в ``get_Geometry`` с
    ``Options.View = view`` (не рёбра тела), габарит — по ним;
  - вложенная аннотация (типовая аннотация в семействе — так сделаны УГО
    большинства приборов). В геометрию она НЕ попадает (проверено на
    модели: ни одной кривой), а ``get_BoundingBox(view)`` даёт её габарит
    вместе с 3D-телом — у модулей тело заметно шире УГО. Отделяем по
    масштабу (:func:`probe_annotation_rects`): аннотация растёт с
    масштабом вида, тело — нет. Во временной копии вида (подтранзакция,
    откатывается) меряем габарит при двух крупных масштабах и продолжаем
    стороны до масштаба вида (``tag_layout.extrapolate_rect``).

Если у элемента на виде нет ни того, ни другого (УГО нет), марка ставится
к самому семейству, как это делает Revit: прямоугольник элемента —
габарит семейства на виде (``get_BoundingBox(view)``, при его
отсутствии — видимое тело), выноска смотрит в точку вставки
(:func:`family_anchor`), а конец выноски привязан к элементу
(``LeaderEndCondition.Attached``) — Revit сам ставит его на семейство и
ведёт за ним при перемещении.

Двухэтажные марки. Семейство марки считается двухэтажным, если хотя
бы у одного его типа в имени есть «/» (например «Марка/Длина»). Типы
такого семейства С «/» используют оба этажа, БЕЗ «/» — только верхний:
нижний этаж у них пустой, но Revit всё равно включает его в габарит
марки, и стопка получалась разреженной. Для таких марок в раскладку
уходит только верхняя половина габарита (:func:`upper_floor_only`) —
на пустой нижний этаж может заходить соседняя марка/выноска. Семейства
без «/» в именах типов не трогаются: настоящую одноэтажную марку резать
пополам нельзя.

У элементов с УГО конец выноски свободный (``LeaderEndCondition.Free``)
— только так он встаёт точно на край УГО, а излом — ровно по вертикали/горизонтали.
Чтобы конец всё равно ездил за прибором, его положение относительно
прибора запоминается в марке, а updater ``tag_leader_follow`` переносит
его при сдвиге/повороте прибора (Revit привязать выноску к вложенному
УГО не умеет — подробности там).

Совместимость с версиями Revit: API выносок марок поменялся в 2022
(много ссылок на одну марку — ``GetTaggedReferences``/``SetLeaderEnd``/
``SetLeaderElbow``), старые свойства ``LeaderEnd``/``LeaderElbow``/
``TaggedLocalElementId`` удалены в 2023. Пробуем новый путь, при
AttributeError — старый.
"""

from Autodesk.Revit.DB import (
    BuiltInCategory, CategoryType, Element, ElementId, Family,
    FamilyInstance, FamilySymbol, FilteredElementCollector, IndependentTag,
    LeaderEndCondition, Reference, TagMode, TagOrientation, TextNote, ViewType,
    XYZ, Options, GeometryInstance, Curve, Solid, PolyLine, Point,
    LocationPoint, SubTransaction, ViewDuplicateOption
)
from Autodesk.Revit.UI.Selection import ISelectionFilter

import System

from eomtools import tag_layout, tag_leader_follow
from eomtools.equipment_tags_core import merge_existing_entries
from eomtools.equipment_tags_links import (
    LinkedEquipment, equipment_key, equipment_reference, tagged_equipment,
    visible_equipment
)

MM_TO_FT = 1.0 / 304.8

# Виды, на которых вообще можно ставить марки на элементы модели
# (3D — только заблокированный, не связываемся).
TAGGABLE_VIEW_TYPES = (
    ViewType.FloorPlan, ViewType.CeilingPlan, ViewType.EngineeringPlan,
    ViewType.AreaPlan, ViewType.Section, ViewType.Elevation, ViewType.Detail,
)


# ------------------------------------------------------------
# МЕЛОЧИ
# ------------------------------------------------------------

def id_int(eid):
    try:
        return eid.Value  # Revit 2024+
    except AttributeError:
        return eid.IntegerValue


def _safe_name(el):
    try:
        return Element.Name.GetValue(el)
    except Exception:
        return u""


def is_taggable_view(view):
    if view is None:
        return False
    try:
        if view.IsTemplate:
            return False
    except Exception:
        pass
    return view.ViewType in TAGGABLE_VIEW_TYPES


def is_equipment(el):
    """Экземпляр семейства модели (любая категория оборудования/устройств/
    обобщённых моделей) — то, на что ставим марку."""
    if isinstance(el, LinkedEquipment):
        el = el.element
    if not isinstance(el, FamilyInstance):
        return False
    cat = el.Category
    if cat is None:
        return False
    try:
        return cat.CategoryType == CategoryType.Model
    except Exception:
        return False


class EquipmentFilter(ISelectionFilter):
    """Выбор рамкой в режиме «Поставить марки»: только оборудование."""

    def AllowElement(self, elem):
        return is_equipment(elem)

    def AllowReference(self, reference, position):
        return True


class TagFilter(ISelectionFilter):
    """Выбор рамкой в режиме «Разложить марки»: только марки."""

    def AllowElement(self, elem):
        return isinstance(elem, IndependentTag)

    def AllowReference(self, reference, position):
        return True


def tagged_local_ids(tag):
    """Id элементов текущего документа, на которые смотрит марка."""
    try:
        return [id_int(i) for i in tag.GetTaggedLocalElementIds()]  # 2022+
    except AttributeError:
        pass
    try:
        eid = tag.TaggedLocalElementId
        if eid is not None and eid != ElementId.InvalidElementId:
            return [id_int(eid)]
    except Exception:
        pass
    return []


def resolve_elements(doc, picked):
    """Выбранное → уникальный список оборудования (марки → их элементы)."""
    result = []
    seen = set()
    for el in picked:
        if isinstance(el, IndependentTag):
            targets = tagged_equipment(doc, el)
        else:
            targets = [el]
        for t in targets:
            if t is None or not is_equipment(t):
                continue
            k = equipment_key(t)
            if k in seen:
                continue
            seen.add(k)
            result.append(t)
    return result


def tags_by_element(doc, view):
    """{id элемента: [марки на этом виде, которые на него смотрят]}."""
    result = {}
    for tag in FilteredElementCollector(doc, view.Id).OfClass(IndependentTag):
        for el in tagged_equipment(doc, tag):
            if not is_equipment(el):
                continue
            result.setdefault(equipment_key(el), []).append(tag)
    return result


def resolve_tag_pairs(doc, view, picked):
    """
    Режим «Разложить марки»: выбранное → [(элемент, марка)]. Выбранная
    марка — она сама (если смотрит на одно оборудование); выбранное
    оборудование — все его марки на этом виде. Новые марки не создаются.
    """
    by_element = None
    result = []
    seen = set()

    def add(el, tag):
        if el is None or not is_equipment(el):
            return
        k = id_int(tag.Id)
        if k not in seen:
            seen.add(k)
            result.append((el, tag))

    for item in picked:
        if isinstance(item, IndependentTag):
            targets = tagged_equipment(doc, item)
            if len(targets) == 1:  # марка сразу на несколько элементов — не трогаем
                add(targets[0], item)
        elif is_equipment(item):
            if by_element is None:
                by_element = tags_by_element(doc, view)
            for tag in by_element.get(equipment_key(item), []):
                add(item, tag)
    return result


def include_existing_tags(doc, view, entries):
    """Все одиночные марки оборудования на активном виде участвуют вместе
    с новыми. Закреплённые и многоссылочные остаются препятствиями."""
    tags = list(FilteredElementCollector(doc, view.Id).OfClass(IndependentTag))
    pairs = resolve_tag_pairs(doc, view, tags)
    return merge_existing_entries(entries, pairs, id_int)


def family_group(el):
    """(категория, семейство) — строка таблицы «что и какой маркой»."""
    fam = u""
    try:
        fam = el.Symbol.FamilyName
    except Exception:
        pass
    return (el.Category.Name, fam)


# ------------------------------------------------------------
# ТИПЫ МАРОК ПО КАТЕГОРИИ
# ------------------------------------------------------------

def _builtin_category(cat):
    try:
        return cat.BuiltInCategory  # 2023+
    except AttributeError:
        pass
    try:
        return System.Enum.ToObject(BuiltInCategory, id_int(cat.Id))
    except Exception:
        return None


def tag_category_ids(cat):
    """Категории марок для категории элемента: «своя» (OST_XxxTags,
    с учётом единственного числа — OST_LightingFixtures → OST_LightingFixtureTags)
    и мультикатегорийная."""
    ids = []
    bic = _builtin_category(cat)
    if bic is not None:
        name = str(bic)
        names = [name + "Tags"]
        if name.endswith("s"):
            names.append(name[:-1] + "Tags")
        for n in names:
            tag_bic = getattr(BuiltInCategory, n, None)
            if tag_bic is not None:
                ids.append(id_int(ElementId(tag_bic)))
                break
    ids.append(id_int(ElementId(BuiltInCategory.OST_MultiCategoryTags)))
    return ids


def tag_types_for_category(doc, cat):
    """Все загруженные типоразмеры марок, которыми можно пометить элемент
    категории cat (через Family → GetFamilySymbolIds, чтобы не терять
    невставленные типы — см. CLAUDE.md). Своя категория — первой."""
    wanted = tag_category_ids(cat)
    by_cat = dict((c, []) for c in wanted)
    for fam in FilteredElementCollector(doc).OfClass(Family):
        fcat = fam.FamilyCategory
        if fcat is None:
            continue
        key = id_int(fcat.Id)
        if key not in by_cat:
            continue
        for sid in fam.GetFamilySymbolIds():
            sym = doc.GetElement(sid)
            if isinstance(sym, FamilySymbol):
                by_cat[key].append(sym)
    result = []
    for c in wanted:
        result.extend(sorted(by_cat[c], key=tag_type_label))
    return result


def tag_type_label(symbol):
    fam_name = u""
    try:
        fam_name = symbol.FamilyName
    except Exception:
        pass
    return u"{}: {}".format(fam_name, _safe_name(symbol))


def default_tag_type_id(doc, cat):
    """Типоразмер марки «по умолчанию» для категории (как у Revit при
    «Марка по категории»), либо None."""
    for c in tag_category_ids(cat)[:1]:
        try:
            tid = doc.GetDefaultFamilyTypeId(ElementId(c))
            if tid is not None and tid != ElementId.InvalidElementId:
                return tid
        except Exception:
            pass
    return None


# ------------------------------------------------------------
# 2D-СИСТЕМА ВИДА
# ------------------------------------------------------------

class ViewFrame(object):
    def __init__(self, view):
        self.o = view.Origin
        self.r = view.RightDirection
        self.u = view.UpDirection
        self.d = view.ViewDirection

    def uv(self, p):
        v = p - self.o
        return (v.DotProduct(self.r), v.DotProduct(self.u))

    def depth(self, p):
        return (p - self.o).DotProduct(self.d)

    def xyz(self, uv, depth):
        return (self.o + self.r.Multiply(uv[0]) + self.u.Multiply(uv[1]) +
                self.d.Multiply(depth))

    def rect(self, bbox):
        if bbox is None:
            return None
        mn, mx = bbox.Min, bbox.Max
        us, vs = [], []
        for x in (mn.X, mx.X):
            for y in (mn.Y, mx.Y):
                for z in (mn.Z, mx.Z):
                    u, v = self.uv(bbox.Transform.OfPoint(XYZ(x, y, z)))
                    us.append(u)
                    vs.append(v)
        return (min(us), min(vs), max(us), max(vs))

    def rect_of_points(self, points):
        if not points:
            return None
        us, vs = [], []
        for p in points:
            u, v = self.uv(p)
            us.append(u)
            vs.append(v)
        return (min(us), min(vs), max(us), max(vs))


# ------------------------------------------------------------
# УГО НА ВИДЕ
# ------------------------------------------------------------

def _walk_geometry(geom, curve_pts, body_pts):
    """Точки видимой на виде геометрии: отдельные кривые (УГО, символьные
    линии) — в curve_pts, тела/точки — в body_pts."""
    if geom is None:
        return
    for g in geom:
        try:
            if isinstance(g, GeometryInstance):
                _walk_geometry(g.GetInstanceGeometry(), curve_pts, body_pts)
            elif isinstance(g, Curve):
                curve_pts.extend(g.Tessellate())
            elif isinstance(g, PolyLine):
                curve_pts.extend(g.GetCoordinates())
            elif isinstance(g, Solid):
                for edge in g.Edges:
                    body_pts.extend(edge.Tessellate())
            elif isinstance(g, Point):
                body_pts.append(g.Coord)
        except Exception:
            continue


def symbol_rect(el, view, frame, scaled=None):
    """Габарит УГО элемента на виде (u0, v0, u1, v1) или None."""
    return symbol_info(el, view, frame, scaled)[0]


# Масштабы замера аннотаций: много крупнее вида, чтобы стороны габарита
# задавала аннотация, а не тело; не крупнее предела Revit (1:24000)
_PROBE_MIN_SCALE = 1000
_PROBE_MAX_SCALE = 24000


def _probe_scales(scale):
    k1 = min(max(_PROBE_MIN_SCALE, 10 * int(scale)), _PROBE_MAX_SCALE // 2)
    return k1, 2 * k1


def _probe_view(doc, view):
    """Вид, на котором можно менять масштаб внутри подтранзакции: копия
    вида без шаблона (шаблон обычно держит масштаб), либо сам вид, если
    у него нет шаблона, а копию сделать нельзя. None — никак."""
    try:
        if view.CanViewBeDuplicated(ViewDuplicateOption.Duplicate):
            dup = doc.GetElement(view.Duplicate(ViewDuplicateOption.Duplicate))
            dup.ViewTemplateId = ElementId.InvalidElementId
            return dup
    except Exception:
        pass
    try:
        if view.ViewTemplateId == ElementId.InvalidElementId:
            return view
    except Exception:
        pass
    return None


def probe_annotation_rects(doc, view, frame, elements):
    """
    {id элемента: габарит вложенной аннотации (УГО) на view} — только для
    элементов, у которых она есть. Вызывать внутри транзакции: копия вида
    и смена масштаба делаются в подтранзакции и откатываются, в модели
    ничего не остаётся. Почему так — в докстринге модуля.
    """
    elements = [el for el in elements if not isinstance(el, LinkedEquipment)]
    if not elements:
        return {}
    k0 = int(view.Scale or 1)
    k1, k2 = _probe_scales(k0)
    measured = {}
    st = SubTransaction(doc)
    st.Start()
    try:
        target = _probe_view(doc, view)
        if target is not None:
            for k in (k1, k2):
                target.Scale = k
                doc.Regenerate()
                for el in elements:
                    try:
                        r = frame.rect(el.get_BoundingBox(target))
                    except Exception:
                        r = None
                    measured.setdefault(equipment_key(el), []).append(r)
    except Exception:
        measured = {}
    finally:
        st.RollBack()

    result = {}
    for el in elements:
        rs = measured.get(equipment_key(el))
        if not rs or len(rs) != 2:
            continue
        rect = tag_layout.extrapolate_rect(rs[0], k1, rs[1], k2, k0)
        if rect is None:
            continue
        # аннотация не может выйти за то, что элемент занимает на виде
        shown = frame.rect(el.get_BoundingBox(view))
        if shown is not None:
            rect = tag_layout.rect_intersection(rect, tag_layout.rect_inflate(shown, 1e-3))
        if rect is not None:
            result[equipment_key(el)] = rect
    return result


def symbol_info(el, view, frame, scaled=None):
    """
    (габарит, есть ли УГО). УГО — отдельные кривые элемента на виде (их
    габарит), иначе вложенная аннотация — её габарит из scaled
    (:func:`probe_annotation_rects`). Ни того, ни другого — УГО нет:
    габарит семейства на виде (``get_BoundingBox(view)``, при его
    отсутствии — видимое тело) и False — такую марку ставим к самому
    семейству (см. :func:`run`).
    """
    if isinstance(el, LinkedEquipment):
        return frame.rect(el.get_BoundingBox(view)), False
    curve_pts, body_pts = [], []
    try:
        opts = Options()
        opts.View = view
        opts.ComputeReferences = False
        opts.IncludeNonVisibleObjects = False
        _walk_geometry(el.get_Geometry(opts), curve_pts, body_pts)
    except Exception:
        pass
    rect = frame.rect_of_points(curve_pts)
    if rect is not None:
        return rect, True
    if scaled:
        rect = scaled.get(equipment_key(el))
        if rect is not None:
            return rect, True
    rect = frame.rect(el.get_BoundingBox(view))
    if rect is None:
        rect = frame.rect_of_points(body_pts)
    return rect, False


def family_anchor(el, frame, rect):
    """Точка вставки семейства на виде (если она в его габарите), иначе
    центр габарита — куда смотрит выноска марки «к семейству»."""
    try:
        loc = el.Location
        if isinstance(loc, LocationPoint):
            u, v = frame.uv(loc.Point)
            if rect[0] <= u <= rect[2] and rect[1] <= v <= rect[3]:
                return (u, v)
    except Exception:
        pass
    return tag_layout.rect_center(rect)


# ------------------------------------------------------------
# ДВУХЭТАЖНЫЕ МАРКИ
# ------------------------------------------------------------

TWO_FLOOR_MARK = u"/"


def upper_floor_only(doc, tag, cache):
    """
    True, если у марки заполнен только верхний этаж: её тип без «/» в
    имени, но в том же семействе есть тип с «/» (значит, семейство
    двухэтажное). cache — {id семейства: есть ли в нём тип с «/»}.
    """
    sym = doc.GetElement(tag.GetTypeId())
    if sym is None or TWO_FLOOR_MARK in _safe_name(sym):
        return False
    try:
        fam = sym.Family
    except Exception:
        return False
    if fam is None:
        return False
    key = id_int(fam.Id)
    if key not in cache:
        cache[key] = any(
            TWO_FLOOR_MARK in _safe_name(doc.GetElement(i))
            for i in fam.GetFamilySymbolIds()
        )
    return cache[key]


# ------------------------------------------------------------
# ВЫНОСКИ
# ------------------------------------------------------------

def _set_leader(tag, ref, end, elbow, attached=False):
    """Выноска с изломом. attached — конец привязан к семейству (Revit
    сам ставит его на элемент и ведёт за ним), end тогда не задаётся."""
    tag.HasLeader = True
    tag.LeaderEndCondition = (LeaderEndCondition.Attached if attached
                              else LeaderEndCondition.Free)
    try:
        if not attached:
            tag.SetLeaderEnd(ref, end)  # 2022+
        tag.SetLeaderElbow(ref, elbow)
    except AttributeError:
        if not attached:
            tag.LeaderEnd = end
        tag.LeaderElbow = elbow


def _tag_reference(tag, el):
    try:
        refs = list(tag.GetTaggedReferences())  # 2022+
        for r in refs:
            if isinstance(el, LinkedEquipment):
                if r.ElementId == el.link.Id and r.LinkedElementId == el.Id:
                    return r
            elif r.ElementId == el.Id:
                return r
        if refs:
            return refs[0]
    except AttributeError:
        pass
    return equipment_reference(el)


def _create_tag(doc, view, el, type_id, point):
    ref = equipment_reference(el)
    if type_id is not None:
        sym = doc.GetElement(type_id)
        if sym is not None and not sym.IsActive:
            sym.Activate()
        return IndependentTag.Create(
            doc, type_id, view.Id, ref, False, TagOrientation.Horizontal, point
        )
    return IndependentTag.Create(
        doc, view.Id, ref, False, TagMode.TM_ADDBY_CATEGORY,
        TagOrientation.Horizontal, point
    )


# ------------------------------------------------------------
# ПРЕПЯТСТВИЯ
# ------------------------------------------------------------

def _other_equipment(doc, view, own_ids, category_ids):
    """Прочее оборудование тех же категорий на виде."""
    result = []
    for el in FilteredElementCollector(doc, view.Id).OfClass(FamilyInstance):
        if equipment_key(el) in own_ids or el.Category is None:
            continue
        if id_int(el.Category.Id) in category_ids:
            result.append(el)
    return result


def _obstacle_rects(doc, view, frame, own_ids, others, scaled):
    """Прочее оборудование (others) + чужие марки и тексты — на них
    марки стараются не ставить."""
    rects = []
    for el in others:
        r = symbol_rect(el, view, frame, scaled)
        if r is not None:
            rects.append(r)
    for cls in (IndependentTag, TextNote):
        for el in FilteredElementCollector(doc, view.Id).OfClass(cls):
            if equipment_key(el) in own_ids:
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
    Ставит и раскладывает марки на view. entries — [(элемент, марка или
    None, ElementId типа марки или None)]: марка None — создать новую (тип
    None — «по категории», как Revit). Двигаются ровно эти марки, прочие
    марки на виде — препятствия. У элемента может быть несколько марок,
    поэтому ключ раскладки — id марки. Вызывать внутри транзакции.
    Возвращает словарь статистики.
    """
    stats = {"created": 0, "moved": 0, "no_bbox": 0, "failed": 0,
             "overlaps": 0, "crossings": 0, "upper_floor": 0, "no_symbol": 0,
             "following": 0}

    scale = float(view.Scale or 1)
    k = MM_TO_FT * scale
    offset = settings["offset_mm"] * k
    gap = settings["gap_mm"] * k
    shelf = settings["shelf_mm"] * k
    cluster_dist = settings["cluster_mm"] * k

    frame = ViewFrame(view)

    # 0. УГО-аннотации — один замер на все элементы, свои и соседние
    elements = []
    own_ids = set()
    for el, _tag, _type in entries:
        if equipment_key(el) not in own_ids:
            own_ids.add(equipment_key(el))
            elements.append(el)
    category_ids = set(id_int(el.Category.Id) for el in elements)
    others = _other_equipment(doc, view, own_ids, category_ids)
    # Семейства связи, которые пользователь оставил без марки, тоже препятствия.
    links = dict((id_int(el.link.Id), el.link) for el in elements
                 if isinstance(el, LinkedEquipment))
    if int(doc.Application.VersionNumber) >= 2024:
        for link in links.values():
            others.extend(el for el in visible_equipment(doc, view, link)
                          if equipment_key(el) not in own_ids
                          and id_int(el.Category.Id) in category_ids)
    scaled = probe_annotation_rects(doc, view, frame, elements + others)

    # 1. УГО элементов: {id: (габарит, точка выноски, марка к семейству)}
    symbols = {}
    for el in elements:
        rect, has_symbol = symbol_info(el, view, frame, scaled)
        if rect is None:
            stats["no_bbox"] += 1
        elif has_symbol:
            symbols[equipment_key(el)] = (rect, tag_layout.rect_center(rect), False)
        else:
            symbols[equipment_key(el)] = (rect, family_anchor(el, frame, rect), True)
            stats["no_symbol"] += 1

    # недостающие марки
    work = []  # (el, tag, rect, anchor, attached)
    for el, tag, type_id in entries:
        sym = symbols.get(equipment_key(el))
        if sym is None:
            continue
        rect, anchor, attached = sym
        if tag is None:
            try:
                bb = el.get_BoundingBox(view)
                depth = frame.depth(bb.Min) if bb is not None else 0.0
                tag = _create_tag(doc, view, el, type_id, frame.xyz(anchor, depth))
                stats["created"] += 1
            except Exception:
                stats["failed"] += 1
                continue
        work.append((el, tag, rect, anchor, attached))

    if not work:
        return stats

    # 2. габарит марок без выноски
    for _el, tag, _r, _a, _at in work:
        try:
            tag.HasLeader = False
        except Exception:
            pass
    doc.Regenerate()

    items = []
    info = {}
    two_floor_cache = {}
    for el, tag, rect, anchor, attached in work:
        trect = frame.rect(tag.get_BoundingBox(view))
        if trect is None:
            stats["failed"] += 1
            continue
        # Revit подводит выноску к середине ПОЛНОЙ высоты марки
        attach_v = (trect[1] + trect[3]) * 0.5
        if upper_floor_only(doc, tag, two_floor_cache):
            # раскладываем только верхний этаж (выноска — к его нижнему
            # краю, черте между этажами); смещение головы ниже считается
            # от центра этой половины, так что запись позиции не меняется
            trect = (trect[0], attach_v, trect[2], trect[3])
            stats["upper_floor"] += 1
        head = tag.TagHeadPosition
        hu, hv = frame.uv(head)
        cu, cv = tag_layout.rect_center(trect)
        key = id_int(tag.Id)
        info[key] = (el, tag, (hu - cu, hv - cv), frame.depth(head), attached)
        items.append(tag_layout.TagItem(
            key, anchor, rect,
            (tag_layout.rect_w(trect), tag_layout.rect_h(trect)),
            attach_dv=attach_v - cv
        ))

    # 3. раскладка
    own_ids.update(info.keys())
    obstacles = _obstacle_rects(doc, view, frame, own_ids, others, scaled)

    placements = tag_layout.layout(items, obstacles, offset=offset, gap=gap,
                                   shelf=shelf, cluster_dist=cluster_dist)
    stats["overlaps"], stats["crossings"] = tag_layout.count_conflicts(placements, items)

    # 4. запись
    for p in placements:
        el, tag, (ou, ov), depth, attached = info[p.key]
        cu, cv = tag_layout.rect_center(p.tag_rect)
        try:
            tag.TagHeadPosition = frame.xyz((cu + ou, cv + ov), depth)
            _set_leader(tag, _tag_reference(tag, el),
                        frame.xyz(p.end, depth), frame.xyz(p.elbow, depth),
                        attached=attached)
            stats["moved"] += 1
        except Exception:
            stats["failed"] += 1
            continue
        # конец выноски у УГО свободный — следить за прибором будет updater
        # (tag_leader_follow); присоединённый Revit ведёт сам
        try:
            if attached:
                tag_leader_follow.forget(tag)
            else:
                tag_leader_follow.remember(tag, el, frame.xyz(p.end, depth))
                stats["following"] += 1
        except Exception:
            pass

    return stats

