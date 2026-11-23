# -*- coding: utf-8 -*-
"""Launch the single active-model coordination-views dialog."""
from pyrevit import forms

from eomlab_coord import backend, dialog, link_views


def run(uiapp):
    uidoc = uiapp.ActiveUIDocument
    if uidoc is None:
        forms.alert(u'Откройте проект Revit и повторите запуск.', title=u'MyRvtTools — Координационные виды')
        return
    doc = uidoc.Document
    view = uidoc.ActiveView
    problem = backend.document_problem(doc)
    if problem:
        forms.alert(problem, title=u'MyRvtTools — Координационные виды')
        return
    if view is None:
        forms.alert(u'Не удалось определить активный вид.', title=u'MyRvtTools — Координационные виды')
        return
    # Link overrides belong to this active view only. View templates are not
    # edited by this command.
    view_problem = link_views.availability(doc, view, allow_template=False)
    if view_problem:
        forms.alert(view_problem, title=u'MyRvtTools - Coordination Views')
        return
    try:
        rows = backend.collect(doc, [view], allow_template=False)
    except Exception as exc:
        forms.alert(u'Не удалось прочитать связи и виды:\n{}'.format(exc),
                    title=u'MyRvtTools — Координационные виды')
        return
    if not rows:
        forms.alert(u'В активной модели нет загруженных Revit-связей.',
                    title=u'MyRvtTools — Координационные виды')
        return
    selection = dialog.show(rows, doc, view, getattr(uiapp, 'MainWindowHandle', None))
    if selection.get('rows') is None:
        return
    try:
        count = backend.apply_document(doc, selection['rows'], selection['workset'],
                                        False,
                                        selection.get('clear_rows') or [])
    except Exception as exc:
        forms.alert(u'Настройки не применены:\n\n{}'.format(exc),
                    title=u'MyRvtTools — Координационные виды')
        return
    forms.alert(u'Настроено связей: {}\n\nЦелевой вид: {}'.format(count, link_views.safe_name(view)),
                title=u'MyRvtTools — Координационные виды')
