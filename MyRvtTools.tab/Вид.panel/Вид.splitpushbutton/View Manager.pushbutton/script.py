# -*- coding: utf-8 -*-
"""Показать/скрыть закреплённую панель View Manager.

Панель регистрируется и получает свежую версию кода в startup.py (запуск Revit
и каждая перезагрузка pyRevit). Здесь — только показ/скрытие.
"""
from pyrevit import HOST_APP, forms

from eomtools import vm_host

uiapp = HOST_APP.uiapp
try:
    host = vm_host.install(uiapp)
    if host.controller is None:
        from eomtools import vm_ui
        host.bind(vm_ui.Controller(host))
    if not vm_host.DockablePane.PaneExists(vm_host.pane_id()):
        raise RuntimeError(u'Панель View Manager зарегистрирована. Перезапустите Revit один раз, '
                           u'чтобы она появилась.')
    pane = uiapp.GetDockablePane(vm_host.pane_id())
    if pane.IsShown():
        pane.Hide()
    else:
        pane.Show()
except Exception as exc:
    forms.alert(u'{0}'.format(exc), title=u'View Manager')
