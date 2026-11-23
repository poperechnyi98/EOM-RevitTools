# -*- coding: utf-8 -*-
"""Revit access; all writes occur only after the preview is accepted."""
import os

from eomlab_coord import link_views, planning

try:
    unicode
except NameError:
    unicode = str

DEFAULT_WORKSET = u'1_Шахты'


def _db():
    from Autodesk.Revit import DB
    return DB


def id_value(value):
    return value.Value if hasattr(value, 'Value') else value.IntegerValue


def document_label(doc):
    return u'{} — {}'.format(doc.Title, doc.PathName or u'не сохранён')


def document_problem(doc):
    if doc.IsLinked:
        return u'Документ загружен как связь, а не самостоятельная модель'
    if doc.IsFamilyDocument:
        return u'Это семейство, а не проект'
    if doc.IsReadOnly:
        return u'Документ доступен только для чтения'
    if doc.IsModifiable:
        return u'В документе уже открыта транзакция'
    return None


def views(doc):
    db = _db()
    return sorted([view for view in db.FilteredElementCollector(doc).OfClass(db.View)
                   if not view.IsTemplate and view.AreGraphicsOverridesAllowed()],
                  key=lambda view: link_views.safe_name(view).lower())


def model_key(link):
    """Use the source path, never instance ids copied between RVT files."""
    linked = link.GetLinkDocument()
    path = linked.PathName if linked is not None else u''
    if not path:
        try:
            typ = link.Document.GetElement(link.GetTypeId())
            path = _db().ModelPathUtils.ConvertModelPathToUserVisiblePath(
                typ.GetExternalFileReference().GetAbsolutePath())
        except Exception:
            pass
    if path:
        return os.path.normcase(os.path.normpath(path))
    # Do not merge unrelated, unsaved models merely because titles coincide.
    if linked is not None:
        return u'loaded:{}:{}'.format(linked.GetHashCode(), linked.Title)
    return u'unloaded:{}:{}'.format(link.Document.GetHashCode(), link.GetTypeId())


def collect(doc, hosts, allow_template=False):
    result = []
    for host in hosts:
        problem = link_views.availability(doc, host, allow_template)
        for source in link_views.collect_rows(doc, host, allow_template):
            link = source['link']
            row = dict(source)
            type_element = doc.GetElement(link.GetTypeId())
            row.update(doc=doc, host=host, model_key=model_key(link),
                       model_name=link_views.safe_name(type_element) if type_element is not None
                       else link_views.safe_name(link),
                       problem=source['reason'], target=None,
                       current_view=source.get('current_view'),
                       manual=False, color_mode='inherit', color=None)
            result.append(row)
    return result


def color_scope(row, workset_name):
    """Conservatively reject other potentially visible model geometry.

    Annotation categories are allowed. This verifies model contents only; the
    visual effect of link-instance overrides on annotation graphics needs Revit QA.
    """
    db = _db()
    linked = row['link'].GetLinkDocument()
    view = row.get('target')
    if linked is None or view is None:
        raise ValueError(u'Для окраски требуется загруженная связь и выбранный вид')
    if not workset_name.strip():
        raise ValueError(u'Укажите рабочий набор шахт')
    if not linked.IsWorkshared:
        raise ValueError(u'У модели связи нет рабочих наборов: отбор шахт проверить нельзя')
    generic_id = int(db.BuiltInCategory.OST_GenericModel)
    table = linked.GetWorksetTable()
    count = 0
    other = []
    elements = db.FilteredElementCollector(linked, view.Id).WhereElementIsNotElementType()
    for element in elements:
        category = element.Category
        if category is None or category.CategoryType != db.CategoryType.Model:
            continue
        if element.IsHidden(view):
            continue
        workset = table.GetWorkset(element.WorksetId)
        ws_name = workset.Name if workset is not None else u''
        if id_value(category.Id) == generic_id and ws_name == workset_name:
            count += 1
        elif len(other) < 5:
            other.append(u'{} [Id {}], РБ «{}»'.format(
                link_views.safe_name(element), element.Id, ws_name))
    if other:
        raise ValueError(u'Вид связи содержит другую модельную геометрию. '
                         u'Цвет всей связи затронет её тоже:\n' + u'\n'.join(other))
    if not count:
        raise ValueError(u'На виде не найдены «Обобщенные модели» из РБ «{}»'.format(workset_name))
    return count


def solid_fill(doc):
    db = _db()
    for element in db.FilteredElementCollector(doc).OfClass(db.FillPatternElement):
        pattern = element.GetFillPattern()
        if pattern.IsSolidFill and pattern.Target == db.FillPatternTarget.Drafting:
            return element.Id
    raise ValueError(u'В основной модели отсутствует сплошная штриховка типа «Условная»')


def set_color(row, fill_id):
    db = _db()
    color = db.Color(*planning.rgb(row['apply_color']))
    host, link = row['host'], row['link']
    settings = db.OverrideGraphicSettings(host.GetElementOverrides(link.Id))
    settings.SetProjectionLineColor(color)
    settings.SetCutLineColor(color)
    settings.SetSurfaceForegroundPatternId(fill_id)
    settings.SetSurfaceForegroundPatternColor(color)
    settings.SetSurfaceForegroundPatternVisible(True)
    settings.SetCutForegroundPatternId(fill_id)
    settings.SetCutForegroundPatternColor(color)
    settings.SetCutForegroundPatternVisible(True)
    host.SetElementOverrides(link.Id, settings)


def _target_view(doc, view, allow_template=False):
    """Return the active view; view templates are never edited."""
    return view


def validate(row, workset_name, allow_template=False):
    problem = document_problem(row['doc']) or row['problem']
    if problem:
        raise ValueError(problem)
    target, link, host = row.get('target'), row['link'], row['host']
    if target is None:
        raise ValueError(u'Не выбран вид связи')
    if host.Document != row['doc'] or link.Document != row['doc']:
        raise ValueError(u'Вид или связь принадлежат другому RVT')
    linked = link.GetLinkDocument()
    if (linked is None or target.Document != linked or target.IsTemplate
            or target.ViewType != host.ViewType):
        raise ValueError(u'Выбранный вид не подходит для этой связи')
    problem = link_views.availability(row['doc'], host, allow_template)
    if problem:
        raise ValueError(problem)
    if row.get('apply_color') is not None:
        planning.rgb(row['apply_color'])
        return u'Готово; шахт: {}'.format(color_scope(row, workset_name))
    return u'Готово; без изменения цвета'


def apply_document(doc, rows, workset_name, allow_template=False, clear_rows=None):
    clear_rows = list(clear_rows or [])
    for row in rows:
        validate(row, workset_name, allow_template)
    # Kept for compatibility with older profiles; the current dialog strips
    # apply_color so the optional whole-link color path is inactive in the UI.
    colored = [row for row in rows if row.get('apply_color') is not None]
    fill_id = solid_fill(doc) if colored else None
    assignments = []
    for row in rows:
        owner = _target_view(doc, row['host'], allow_template)
        item = next((pair for pair in assignments if pair[0] == owner), None)
        if item is None:
            item = (owner, [])
            assignments.append(item)
        item[1].append((row['link'], row['target']))

    removals = []
    for row in clear_rows:
        if row['host'].Document != doc or row['link'].Document != doc:
            raise ValueError(u'��� ��� ��� �ਭ������� ��㣮�� RVT')
        problem = document_problem(doc) or row.get('problem')
        if problem:
            raise ValueError(problem)
        owner = _target_view(doc, row['host'], allow_template)
        item = next((pair for pair in removals if pair[0] == owner), None)
        if item is None:
            item = (owner, [])
            removals.append(item)
        item[1].append(row['link'])

    def graphics():
        for row in colored:
            target_row = row
            owner = _target_view(doc, row['host'], allow_template)
            if owner is not row['host']:
                target_row = dict(row)
                target_row['host'] = owner
            set_color(target_row, fill_id)

    return link_views.apply_assignments(doc, assignments, before_commit=graphics,
                                        allow_template=allow_template,
                                        removals=removals)
