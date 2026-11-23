# -*- coding: utf-8 -*-
"""Place a selected Excel cable tray schedule on an open section."""
import os
import sys

from pyrevit import forms, script

BUNDLE = os.path.dirname(os.path.abspath(__file__))
if BUNDLE not in sys.path:
    sys.path.insert(0, BUNDLE)

from myrvttools_tray_section.dialog import TraySectionDialog, mm
from myrvttools_tray_section import revit_host


def _result(section, layout, unchecked_centres):
    lines = [u'Лист «{}»'.format(section.name),
             u'Кабелей размещено: {}'.format(len(layout.cables)),
             u'Занятый габарит: {} × {} мм. Лоток: {} × {} мм.'.format(
                 mm(layout.width), mm(layout.height), mm(section.width), mm(section.height))]
    if layout.overflow_width > 0.001:
        lines.append(u'Выход за внутреннюю ширину: {} мм.'.format(mm(layout.overflow_width)))
    if layout.overflow_height > 0.001:
        lines.append(u'Выход над стенками: {} мм.'.format(mm(layout.overflow_height)))
    if section.required_width is not None:
        lines.append(u'Требуемая ширина по Excel: {} мм.'.format(mm(section.required_width)))
    if unchecked_centres:
        lines.append(u'У {} экз. Revit не предоставил геометрию для сверки центра. '
                     u'Проверьте привязку на разрезе.'.format(unchecked_centres))
    return u'\n'.join(lines)


def run():
    uiapp = __revit__
    uidoc = uiapp.ActiveUIDocument
    title = revit_host.TITLE
    if uidoc is None or uidoc.Document.IsFamilyDocument:
        forms.alert(u'Откройте разрез в проекте Revit.', title=title)
        return
    doc, view = uidoc.Document, uidoc.ActiveView
    if not revit_host.is_section(view):
        forms.alert(u'Откройте сам вид разреза и запустите команду снова.', title=title)
        return

    try:
        dialog = TraySectionDialog(view.Name, uiapp.MainWindowHandle,
                                   lambda: revit_host.find_symbol(doc),
                                   revit_host.collect_style_types(doc),
                                   script.get_config('eomlab_tray_section'), script.save_config)
        selected = dialog.show()
        if selected is None:
            return
        section, layout, resources, legend = selected
        origin = revit_host.pick_origin(uidoc, view)
        if origin is None:
            return
        unchecked_centres = revit_host.place(doc, view, origin, section, layout, resources, legend)
    except Exception as error:
        script.get_logger().debug('Cable section could not be built', exc_info=True)
        forms.alert(u'Не удалось построить сечение. {}'.format(error), title=title)
        return
    forms.alert(_result(section, layout, unchecked_centres), title=title)


run()
