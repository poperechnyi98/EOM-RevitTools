# -*- coding: utf-8 -*-
"""Search rooms in the host model and its loaded links."""
import os
import sys

from pyrevit import forms, script

BUNDLE = os.path.dirname(os.path.abspath(__file__))
if BUNDLE not in sys.path:
    sys.path.insert(0, BUNDLE)

from eomtools_room_search import dialog, rooms


def run():
    uiapp = __revit__
    uidoc = uiapp.ActiveUIDocument
    if uidoc is None:
        forms.alert(u'Откройте проект Revit.', title=dialog.TITLE)
        return
    if uidoc.Document.IsFamilyDocument:
        forms.alert(u'Команда работает в проекте Revit.', title=dialog.TITLE)
        return

    try:
        sources = rooms.collect_sources(uidoc.Document)
        if not sources:
            forms.alert(u'В модели и загруженных связях нет размещённых помещений.',
                        title=dialog.TITLE)
            return
        config = script.get_config('eomlab_find_rooms')
        window = dialog.RoomSearch(sources, config, script.save_config,
                                   uidoc.Document.Title, uiapp.MainWindowHandle)
        result = window.show()
        if result is not None:
            rooms.show_in_view(uidoc, result[0], result[1])
    except Exception as exc:
        forms.alert(u'{}'.format(exc), title=dialog.TITLE)


run()
