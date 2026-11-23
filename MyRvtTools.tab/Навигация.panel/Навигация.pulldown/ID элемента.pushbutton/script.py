# -*- coding: utf-8 -*-
from pyrevit import forms, revit, script
from Autodesk.Revit.DB import ElementId, RevitLinkInstance


__title__ = u"ID\nэлемента"
__doc__ = u"Показывает и копирует ID выделенных элементов."

doc = revit.doc
uidoc = revit.uidoc


def _description(element):
    if element is None:
        return u""
    parts = []
    try:
        if element.Category:
            parts.append(element.Category.Name)
    except Exception:
        pass
    try:
        if element.Name:
            parts.append(element.Name)
    except Exception:
        pass
    return u" — ".join(parts)


def _resolve(reference):
    linked_id = reference.LinkedElementId
    if linked_id and linked_id != ElementId.InvalidElementId:
        link_instance = doc.GetElement(reference.ElementId)
        linked_doc = None
        if isinstance(link_instance, RevitLinkInstance):
            try:
                linked_doc = link_instance.GetLinkDocument()
            except Exception:
                pass
        element = linked_doc.GetElement(linked_id) if linked_doc else None
        link_name = linked_doc.Title if linked_doc else u"Связанная модель"
        return linked_id.IntegerValue, element, link_name

    element = doc.GetElement(reference.ElementId)
    return reference.ElementId.IntegerValue, element, None


try:
    references = list(uidoc.Selection.GetReferences())
except Exception:
    references = []

if not references:
    forms.alert(u"Выделите один или несколько элементов и запустите кнопку снова.",
                title=u"ID элемента", exitscript=True)

rows = []
for reference in references:
    element_id, element, link_name = _resolve(reference)
    description = _description(element)
    row = unicode(element_id)
    extra = u" — ".join([item for item in (description, link_name) if item])
    if extra:
        row += u" ({})".format(extra)
    rows.append(row)

script.clipboard_copy(u", ".join(row.split(u" (")[0] for row in rows))
forms.alert(u"\n".join(rows) + u"\n\nID скопированы в буфер обмена.",
            title=u"ID элементов ({})".format(len(rows)))
