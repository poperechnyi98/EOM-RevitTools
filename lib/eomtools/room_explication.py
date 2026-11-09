# -*- coding: utf-8 -*-
"""Экспликация фрагмента — Revit API (`ToolsRooms.panel/FragmentExplication`,
`ToolsRooms.panel/UpdateExplication`).

Вид «ручная спецификация» (по умолчанию) — свободная таблица без параметров:

1. Помещения фрагмента — помещения модели и видимых связей, чья точка
   размещения попадает в рамку подрезки плана и в его секущий диапазон.
2. Спецификация помещений, у которой тело всегда пустое (фильтр по номеру
   помещения, под который ничего не подходит, заголовки граф скрыты).
   Её четыре поля — встроенные параметры помещения — задают только
   столбцы и их ширины; сама экспликация — текст в ячейках ШАПКИ
   спецификации (строка заголовков граф + по строке на помещение), где
   Revit даёт задать и текст, и высоту строк. Новых параметров в модели нет.
3. Какой план и какие источники (модель/связи) у экспликации — скрытая
   метка ExtensibleStorage на самой спецификации; по ней «Обновить»
   находит экспликации и пересобирает строки.

Вид таблицы «ключевая спецификация» (настройка kind) — room_explication_key;
вид уже созданной экспликации определяется по ней самой (kind_of).

`find_legacy`/`delete_legacy` — убрать то, что оставила первая версия
кнопки (ключевые спецификации без метки и, если не используются,
параметры `LL_Экспликация_*`).
"""

from Autodesk.Revit.DB import (
    BuiltInCategory, BuiltInParameter, ElementId, FilteredElementCollector,
    RevitLinkInstance, ScheduleFilter, ScheduleFilterType, SectionType,
    SharedParameterElement, ViewSchedule, ViewType,
)
from System import Guid, String

from eomtools import room_explication_core as core

# Запас по высоте при отборе помещений по секущему диапазону плана, футы.
Z_TOLERANCE_FT = 1.0

# Номер помещения, под который не подходит ни одно помещение — тело пустое.
EMPTY_FILTER_VALUE = u"LowLife: экспликация без строк тела"

# Встроенные параметры помещения — поля-«столбцы» спецификации (по порядку
# граф). Значения из них не выводятся (тело пустое), они задают ширины.
COLUMN_BIPS = (
    BuiltInParameter.ROOM_NUMBER,
    BuiltInParameter.ROOM_NAME,
    BuiltInParameter.ROOM_AREA,
    BuiltInParameter.ROOM_LEVEL_ID,
)

# Скрытая метка на спецификации (ExtensibleStorage). GUID не менять.
SCHEMA_GUID = Guid("ce570e42-1621-434a-a0f4-ddc2d2795d98")
SCHEMA_NAME = "LowLifeFragmentExplication"
SCHEMA_VENDOR = "LowLife"
_F_VIEW = "SourceViewUniqueId"
_F_SOURCES = "Sources"
_SOURCES_SEP = u"\n"

# Параметры первой версии кнопки (ключевая спецификация) — только для уборки.
LEGACY_PARAM_GUIDS = (
    "0d49f254-a0c7-4953-b450-116639cca2e2",
    "cf634822-e8ed-4901-8aab-3626a0804411",
    "13bf02c5-69f7-4fb1-9d5b-6d9208a2112c",
    "590902e1-804a-4a96-aec1-e0d7d60437ce",
)



# ---------------------------------------------------------------------------
# Вид и помещения
# ---------------------------------------------------------------------------

_PLAN_TYPES = set(getattr(ViewType, n) for n in
                  ("FloorPlan", "CeilingPlan", "EngineeringPlan", "AreaPlan")
                  if hasattr(ViewType, n))


def unsupported_reason(view):
    """None, если по виду можно строить экспликацию, иначе текст причины."""
    if view is None or view.IsTemplate:
        return u"Откройте план, по которому нужна экспликация."
    if view.ViewType not in _PLAN_TYPES:
        return (u"Экспликация фрагмента строится по плану (этажа, потолка, "
                u"несущих конструкций, зон). Активный вид: {}.".format(view.ViewType))
    if not view.CropBoxActive:
        return (u"У вида не включена подрезка — непонятно, какой это "
                u"фрагмент. Обрежьте вид (кнопка «Обрезать вид») и "
                u"запустите снова.")
    return None


def _crop_frame(view):
    """(преобразование модель -> вид, (xmin, ymin, xmax, ymax)) рамки подрезки."""
    box = view.CropBox
    rect = (min(box.Min.X, box.Max.X), min(box.Min.Y, box.Max.Y),
            max(box.Min.X, box.Max.X), max(box.Min.Y, box.Max.Y))
    return box.Transform.Inverse, rect


def _z_range(doc, view):
    """(низ, верх) секущего диапазона плана во внутренних координатах."""
    level = view.GenLevel
    base = level.ProjectElevation if level is not None else 0.0
    lo, hi = base, base
    try:
        from Autodesk.Revit.DB import PlanViewPlane
        vr = view.GetViewRange()
        for plane in (PlanViewPlane.BottomClipPlane, PlanViewPlane.CutPlane,
                      PlanViewPlane.TopClipPlane):
            lvl = doc.GetElement(vr.GetLevelId(plane))
            if lvl is None:
                continue
            z = lvl.ProjectElevation + vr.GetOffset(plane)
            if plane == PlanViewPlane.TopClipPlane:
                hi = max(hi, min(z, base + 3 * Z_TOLERANCE_FT))
            else:
                lo, hi = min(lo, z), max(hi, z)
    except Exception:
        pass
    return lo - Z_TOLERANCE_FT, hi + Z_TOLERANCE_FT


def _rooms(source_doc):
    return (FilteredElementCollector(source_doc)
            .OfCategory(BuiltInCategory.OST_Rooms)
            .WhereElementIsNotElementType())


def _text_param(el, bip=None, name=None):
    try:
        p = el.get_Parameter(bip) if bip is not None else el.LookupParameter(name)
    except Exception:
        p = None
    if p is None or not p.HasValue:
        return u""
    try:
        return p.AsString() or p.AsValueString() or u""
    except Exception:
        return u""


def _is_area_spec(param):
    try:
        from Autodesk.Revit.DB import SpecTypeId
        return param.Definition.GetDataType() == SpecTypeId.Area
    except Exception:
        try:
            from Autodesk.Revit.DB import ParameterType
            return param.Definition.ParameterType == ParameterType.Area
        except Exception:
            return False


def _area_value(room, name):
    """Площадь: встроенная (м²) или из параметра name — число площади
    переводится в м², другое число берётся как есть, текст — текстом."""
    if not name:
        return room.Area * core.SQ_FT_TO_SQ_M
    try:
        p = room.LookupParameter(name)
    except Exception:
        p = None
    if p is None or not p.HasValue:
        return u""
    from Autodesk.Revit.DB import StorageType
    if p.StorageType == StorageType.Double:
        value = p.AsDouble()
        return value * core.SQ_FT_TO_SQ_M if _is_area_spec(p) else value
    if p.StorageType == StorageType.Integer:
        return float(p.AsInteger())
    return p.AsString() or p.AsValueString() or u""


def _room_data(room, params):
    """params — {"number", "name", "area", "category": имя параметра}; пусто
    у номера/имени/площади — встроенные параметры помещения."""
    number = params.get("number")
    name = params.get("name")
    category = params.get("category")
    data = {
        "number": (_text_param(room, name=number) if number
                   else _text_param(room, BuiltInParameter.ROOM_NUMBER)),
        "name": (_text_param(room, name=name) if name
                 else _text_param(room, BuiltInParameter.ROOM_NAME)),
        "area_m2": _area_value(room, params.get("area")),
        "category": _text_param(room, name=category) if category else u"",
    }
    # Значения уровней сортировки/группировки (room_explication_core.build_table).
    data["keys"] = [_text_param(room, name=p) for p in params.get("levels") or []]
    return data


def _visible_room_ids(doc, view, link=None):
    """
    Id помещений, которые Revit показывает на этом плане (его уровень и
    секущий диапазон): для модели — FilteredElementCollector(doc, view.Id),
    для связи — (doc, view.Id, link.Id), есть с Revit 2024. None — Revit не
    ответил (старая версия, ошибка) или ответил пустым набором (например,
    категория «Помещения» скрыта на виде) — тогда проверка по высоте.
    """
    try:
        if link is None:
            collector = FilteredElementCollector(doc, view.Id)
        else:
            collector = FilteredElementCollector(doc, view.Id, link.Id)
        ids = set(str(el.Id) for el in collector.OfCategory(BuiltInCategory.OST_Rooms)
                  .WhereElementIsNotElementType())
    except Exception:
        return None
    return ids or None


class RoomStats(object):
    """Сколько помещений отсеялось на каждом шаге — для сообщения, если
    во фрагменте не нашлось ни одного."""

    def __init__(self, label):
        self.label = label
        self.total = 0      # всего помещений в модели/связи
        self.in_frame = 0   # точка размещения в рамке подрезки (в плане)
        self.on_level = 0   # из них — на этом этаже
        self.method = u""   # как определялся этаж

    def text(self):
        return u"{}: всего {}, в рамке (в плане) {}, из них на этом этаже {} ({})".format(
            self.label, self.total, self.in_frame, self.on_level, self.method)


def collect_fragment_rooms(doc, view, params=None):
    """
    params — имена параметров (room_explication_settings.room_params).
    Помещения фрагмента по источникам: {подпись источника: [dict помещения]}
    (dict — как ждёт room_explication_core.build_rows), число отброшенных
    неразмещённых/незамкнутых помещений (площадь 0) и [RoomStats].

    В рамке — точка размещения внутри рамки подрезки (только X/Y вида).
    На этом этаже — помещение показано на плане (_visible_room_ids); если
    Revit этого не сказал — точка в секущем диапазоне плана (_z_range).
    """
    params = params or {}
    to_view, rect = _crop_frame(view)
    lo, hi = _z_range(doc, view)

    sources = [(u"Эта модель", doc, None, None)]
    for link in FilteredElementCollector(doc).OfClass(RevitLinkInstance):
        try:
            link_doc = link.GetLinkDocument()
            if link_doc is None or link.IsHidden(view):
                continue
        except Exception:
            continue
        sources.append((u"Связь: {}".format(link_doc.Title), link_doc,
                        link.GetTotalTransform(), link))

    result = {}
    skipped = 0
    stats = []
    for label, source_doc, transform, link in sources:
        st = RoomStats(label)
        stats.append(st)
        visible = _visible_room_ids(doc, view, link)
        st.method = (u"по видимости на плане" if visible is not None
                     else u"по высоте секущего диапазона")
        for room in _rooms(source_doc):
            st.total += 1
            loc = room.Location
            pt = getattr(loc, "Point", None) if loc is not None else None
            if pt is None:
                continue
            if transform is not None:
                pt = transform.OfPoint(pt)
            local = to_view.OfPoint(pt)
            if not core.in_rect(local.X, local.Y, rect):
                continue
            st.in_frame += 1
            if visible is not None:
                if str(room.Id) not in visible:
                    continue
            elif not lo <= pt.Z <= hi:
                continue
            st.on_level += 1
            if room.Area <= 0:
                skipped += 1
                continue
            result.setdefault(label, []).append(_room_data(room, params))
    return result, skipped, stats


# ---------------------------------------------------------------------------
# Метка на спецификации
# ---------------------------------------------------------------------------

def _schema():
    from Autodesk.Revit.DB.ExtensibleStorage import AccessLevel, Schema, SchemaBuilder
    schema = Schema.Lookup(SCHEMA_GUID)
    if schema is not None:
        return schema
    sb = SchemaBuilder(SCHEMA_GUID)
    sb.SetSchemaName(SCHEMA_NAME)
    sb.SetVendorId(SCHEMA_VENDOR)
    sb.SetReadAccessLevel(AccessLevel.Public)
    sb.SetWriteAccessLevel(AccessLevel.Public)
    sb.AddSimpleField(_F_VIEW, String)
    sb.AddSimpleField(_F_SOURCES, String)
    return sb.Finish()


def read_mark(schedule):
    """(UniqueId плана, [источники] или [] = все) или None, если это не
    экспликация фрагмента."""
    from Autodesk.Revit.DB.ExtensibleStorage import Schema
    schema = Schema.Lookup(SCHEMA_GUID)
    if schema is None:
        return None
    try:
        ent = schedule.GetEntity(schema)
        if ent is None or not ent.IsValid():
            return None
        view_uid = ent.Get[String](_F_VIEW)
        sources = ent.Get[String](_F_SOURCES) or u""
    except Exception:
        return None
    if not view_uid:
        return None
    return view_uid, [s for s in sources.split(_SOURCES_SEP) if s]


def _write_mark(schedule, view, sources):
    from Autodesk.Revit.DB.ExtensibleStorage import Entity
    ent = Entity(_schema())
    ent.Set[String](_F_VIEW, view.UniqueId)
    ent.Set[String](_F_SOURCES, _SOURCES_SEP.join(sources or []))
    schedule.SetEntity(ent)


def list_explications(doc):
    """[(спецификация, план или None, [источники])] — все экспликации фрагмента."""
    result = []
    for v in FilteredElementCollector(doc).OfClass(ViewSchedule):
        try:
            if v.IsTemplate:
                continue
        except Exception:
            continue
        mark = read_mark(v)
        if mark is None:
            continue
        view_uid, sources = mark
        result.append((v, doc.GetElement(view_uid), sources))
    return result


def explications_of_view(doc, view):
    return [e for e in list_explications(doc)
            if e[1] is not None and e[1].Id == view.Id]


def pick_rooms(by_source, sources):
    """Помещения выбранных источников ([] — все; несуществующие пропускаются,
    а если не осталось ни одного — берутся все)."""
    chosen = [s for s in (sources or []) if s in by_source] or sorted(by_source.keys())
    rooms = []
    for s in chosen:
        rooms.extend(by_source[s])
    return rooms


# ---------------------------------------------------------------------------
# Спецификация-«бланк» и таблица в шапке
# ---------------------------------------------------------------------------

def element_name(el):
    try:
        from Autodesk.Revit.DB import Element
        return Element.Name.GetValue(el)
    except Exception:
        return el.Name


def _schedule_names(doc):
    names = set()
    for v in FilteredElementCollector(doc).OfClass(ViewSchedule):
        try:
            names.add(element_name(v))
        except Exception:
            pass
    return names


def _create_blank(doc, view_name):
    """Спецификация помещений с пустым телом и четырьмя полями-столбцами."""
    schedule = ViewSchedule.CreateSchedule(doc, ElementId(BuiltInCategory.OST_Rooms))
    schedule.Name = core.unique_name(core.schedule_name(view_name), _schedule_names(doc))
    d = schedule.Definition
    by_param = dict((str(sf.ParameterId), sf) for sf in d.GetSchedulableFields())
    fields = []
    for bip in COLUMN_BIPS:
        sf = by_param.get(str(ElementId(bip)))
        if sf is None:
            raise RuntimeError(u"В спецификации помещений нет поля {}.".format(bip))
        fields.append(d.AddField(sf))
    d.AddFilter(ScheduleFilter(fields[0].FieldId, ScheduleFilterType.Equal, EMPTY_FILTER_VALUE))
    d.ShowHeaders = False
    d.ShowTitle = True
    try:
        d.ShowGrandTotal = False
    except Exception:
        pass
    d.IsItemized = True
    return schedule


def _visible_fields(schedule):
    d = schedule.Definition
    fields = []
    for fid in d.GetFieldOrder():
        field = d.GetField(fid)
        if not field.IsHidden:
            fields.append(field)
    return fields


def _set_style(section, row, col, center, bold=False):
    """Выравнивание текста ячейки шапки (по центру/влево)."""
    try:
        from Autodesk.Revit.DB import (
            HorizontalAlignmentStyle, TableCellStyleOverrideOptions)
        style = section.GetTableCellStyle(row, col)
        opts = TableCellStyleOverrideOptions()
        opts.HorizontalAlignment = True
        if bold:
            opts.Bold = True
            style.IsFontBold = True
        style.SetCellStyleOverrideOptions(opts)
        style.FontHorizontalAlignment = (HorizontalAlignmentStyle.Center if center
                                         else HorizontalAlignmentStyle.Left)
        section.SetCellStyle(row, col, style)
    except Exception:
        pass


def _merged_span(section, row, col):
    """(лево, право) объединения, в которое входит ячейка, или None."""
    try:
        m = section.GetMergedCell(row, col)
        return m.Left, m.Right
    except Exception:
        return None


def _unmerge_row(section, row, col0, count):
    """Новая строка шапки могла унаследовать объединение ячеек от строки
    названия — разбиваем на отдельные ячейки (как «Разъединить» в Revit)."""
    from Autodesk.Revit.DB import TableMergedCell
    for j in range(count):
        c = col0 + j
        span = _merged_span(section, row, c)
        if span is None or span[0] == span[1]:
            continue
        try:
            section.SetMergedCell(row, span[0], TableMergedCell(row, span[0], row, span[0]))
        except Exception:
            pass


def _ensure_header_columns(section, widths_ft):
    """
    Шапка должна иметь столько же столбцов, сколько граф. Обычно Revit
    повторяет в шапке столбцы тела, но если их меньше — добавляем
    (InsertColumn). Ширины задаём и в шапке. Возвращает число столбцов.
    """
    need = len(widths_ft)
    for _ in range(need):
        if section.NumberOfColumns >= need:
            break
        inserted = False
        for idx in (section.LastColumnNumber + 1, section.LastColumnNumber):
            try:
                if section.CanInsertColumn(idx):
                    section.InsertColumn(idx)
                    inserted = True
                    break
            except Exception:
                continue
        if not inserted:
            break
    col0 = section.FirstColumnNumber
    for j, width in enumerate(widths_ft):
        if j >= section.NumberOfColumns:
            break
        try:
            section.SetColumnWidth(col0 + j, width)
        except Exception:
            pass
    return section.NumberOfColumns


def _header_report(section, row, count):
    """Что Revit отдал в шапке — для текста ошибки."""
    spans = []
    for j in range(count):
        c = section.FirstColumnNumber + j
        spans.append(u"{}:{}".format(c, _merged_span(section, row, c)))
    return (u"столбцов в шапке {} (первый {}, последний {}); объединения в строке {}: {}"
            .format(section.NumberOfColumns, section.FirstColumnNumber,
                    section.LastColumnNumber, row, u", ".join(spans)))


def fill_table(doc, schedule, title, columns, rows, heights):
    """
    Пересобирает таблицу в шапке: строка названия, строка заголовков граф
    (высота heights[0] мм), затем строки rows (heights[1] мм) — таблица из
    room_explication_core.build_table: ("group", (уровень, заголовок)) —
    объединённая на всю ширину строка, ("row", строка) — помещение. Ширины
    граф — у полей тела и у столбцов шапки. Вызывать в транзакции.
    """
    fields = _visible_fields(schedule)
    if len(fields) < len(columns):
        raise RuntimeError(u"У спецификации «{}» меньше {} видимых столбцов — "
                           u"удалите её и создайте экспликацию заново."
                           .format(element_name(schedule), len(columns)))
    widths_ft = [c[2] / core.MM_IN_FOOT for c in columns]
    for field, width in zip(fields, widths_ft):
        field.GridColumnWidth = width
    doc.Regenerate()

    header = schedule.GetTableData().GetSectionData(SectionType.Header)
    first = header.FirstRowNumber
    for r in range(header.LastRowNumber, first, -1):
        header.RemoveRow(r)
    _ensure_header_columns(header, widths_ft)
    try:
        from Autodesk.Revit.DB import TableMergedCell
        header.SetMergedCell(first, header.FirstColumnNumber, TableMergedCell(
            first, header.FirstColumnNumber, first, header.LastColumnNumber))
    except Exception:
        pass  # название — на всю ширину таблицы; не вышло — останется как есть
    header.SetCellText(first, header.FirstColumnNumber, title)

    header_mm, row_mm = heights
    col0 = header.FirstColumnNumber
    ncols = len(columns)
    lines = [("heading", [c[1] for c in columns])] + [
        (kind, item) for kind, item in rows]
    for i, (line_kind, values) in enumerate(lines):
        r = first + 1 + i
        header.InsertRow(r)
        header.SetRowHeight(r, (header_mm if i == 0 else row_mm) / core.MM_IN_FOOT)
        _unmerge_row(header, r, col0, ncols)
        if line_kind == "group":
            # Заголовок группы — одна ячейка на всю ширину таблицы.
            try:
                from Autodesk.Revit.DB import TableMergedCell
                header.SetMergedCell(r, col0, TableMergedCell(r, col0, r, col0 + ncols - 1))
            except Exception:
                pass
            header.SetCellText(r, col0, values[1])
            _set_style(header, r, col0, center=True, bold=True)
            continue
        for j, text in enumerate(values):
            try:
                header.SetCellText(r, col0 + j, text)
            except Exception as err:
                raise RuntimeError(u"Revit не дал записать ячейку шапки ({}, {}): {}\n{}"
                                   .format(r, col0 + j, err,
                                           _header_report(header, r, len(values))))
            # Наименование помещения — влево, остальное и заголовки — по центру.
            _set_style(header, r, col0 + j, center=(i == 0 or columns[j][0] != "name"))


KIND_HEADER = core.KIND_HEADER
KIND_KEY = core.KIND_KEY


def kind_of(schedule):
    """Вид уже созданной экспликации — по самой спецификации."""
    try:
        return KIND_KEY if schedule.Definition.IsKeySchedule else KIND_HEADER
    except Exception:
        return KIND_HEADER


def build_explication(doc, view, sources, title, columns, rows, heights,
                      schedule=None, kind=KIND_HEADER, app=None, header_orders=()):
    """
    Создаёт (schedule=None, вид kind) или пересобирает экспликацию плана
    view; у существующей вид не меняется (kind_of). sources — выбранные
    источники ([] = все), запоминаются в метке. app — Application (нужен
    ключевой спецификации для параметров). rows — таблица build_table;
    header_orders — «по убыванию» уровней с заголовком (для ключевой
    спецификации). Вызывать в транзакции. Возвращает (спецификация, [замечания]).
    """
    from eomtools import room_explication_key as rkey
    if schedule is None:
        if kind == KIND_KEY:
            name = core.unique_name(core.schedule_name(view.Name), _schedule_names(doc))
            schedule = rkey.create(doc, name)
        else:
            schedule = _create_blank(doc, view.Name)
            doc.Regenerate()
    if kind_of(schedule) == KIND_KEY:
        notes = rkey.fill(doc, app, schedule, title, columns, rows, heights,
                          header_orders)
    else:
        fill_table(doc, schedule, title, columns, rows, heights)
        notes = []
    _write_mark(schedule, view, sources)
    return schedule, notes


# ---------------------------------------------------------------------------
# Уборка за первой версией (ключевая спецификация + параметры)
# ---------------------------------------------------------------------------

def find_legacy(doc):
    """
    То, что осталось от первой версии кнопки: ключевые спецификации с
    параметрами LL_Экспликация_* БЕЗ метки экспликации (с меткой — это
    экспликации вида «ключевая спецификация», их не трогаем). Параметры
    предлагаются к удалению, только если ни одна помеченная экспликация их
    не использует. Возвращает (спецификации, параметры).
    """
    from eomtools import room_explication_key as rkey
    schedules = []
    in_use = False
    for v in FilteredElementCollector(doc).OfClass(ViewSchedule):
        try:
            if v.IsTemplate or not rkey.uses_params(v):
                continue
        except Exception:
            continue
        if read_mark(v) is None:
            schedules.append(v)
        else:
            in_use = True
    params = []
    if not in_use:
        for guid in LEGACY_PARAM_GUIDS:
            el = SharedParameterElement.Lookup(doc, Guid(guid))
            if el is not None:
                params.append(el)
    return schedules, params


def delete_legacy(doc, schedules, params):
    """Удаляет найденное find_legacy. Вызывать в транзакции."""
    from System.Collections.Generic import List
    ids = [el.Id for el in list(schedules) + list(params)]
    if ids:
        doc.Delete(List[ElementId](ids))
    return len(ids)


# ---------------------------------------------------------------------------
# Общий сценарий кнопок
# ---------------------------------------------------------------------------

def rebuild(doc, view, sources, settings, schedule=None, app=None):
    """
    Собирает помещения фрагмента view (источники sources, [] = все) и
    создаёт/пересобирает экспликацию по настройкам кнопки (новая — вида из
    настроек, существующая — своего вида). app — Application. Вызывать в
    транзакции. Возвращает (спецификация, число строк, пропущено с
    площадью 0, [замечания]).
    """
    from eomtools import room_explication_settings as rs
    params = rs.room_params(settings)
    by_source, skipped, _stats = collect_fragment_rooms(doc, view, params)
    levels = rs.sort_levels(settings)
    table = core.build_table(pick_rooms(by_source, sources),
                             settings.get("area_decimals", 2),
                             [(desc, header) for _name, desc, header in levels])
    schedule, notes = build_explication(
        doc, view, sources, rs.title(settings), rs.columns(settings), table,
        rs.heights(settings), schedule, rs.kind(settings), app,
        [desc for _name, desc, header in levels if header])
    return schedule, len(core.table_rows(table)), skipped, notes


def explications_to_update(doc, uidoc):
    """
    Какие экспликации обновлять, по контексту: выделенные на листе/в
    диспетчере → открытая спецификация → открытый план → открытый лист.
    Возвращает (список из list_explications, описание) или ([], None),
    если контекст ни на что не указывает (тогда — все в проекте).
    """
    from Autodesk.Revit.DB import ScheduleSheetInstance, ViewSheet
    all_items = list_explications(doc)
    by_id = dict((str(e[0].Id), e) for e in all_items)

    picked = []
    for eid in uidoc.Selection.GetElementIds():
        el = doc.GetElement(eid)
        if isinstance(el, ScheduleSheetInstance):
            eid = el.ScheduleId
        item = by_id.get(str(eid))
        if item is not None and item not in picked:
            picked.append(item)
    if picked:
        return picked, u"выделенные"

    view = doc.ActiveView
    item = by_id.get(str(view.Id))
    if item is not None:
        return [item], u"открытая спецификация"
    on_view = [e for e in all_items if e[1] is not None and e[1].Id == view.Id]
    if on_view:
        return on_view, u"экспликации открытого плана"
    if isinstance(view, ViewSheet):
        placed = set(str(i.ScheduleId) for i in FilteredElementCollector(doc, view.Id)
                     .OfClass(ScheduleSheetInstance))
        on_sheet = [e for e in all_items if str(e[0].Id) in placed]
        if on_sheet:
            return on_sheet, u"на открытом листе"
    return [], None
