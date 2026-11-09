# -*- coding: utf-8 -*-
"""Стартовый скрипт EOM-RevitTools. pyRevit выполняет его при запуске Revit
и при каждой перезагрузке pyRevit (Reload, кнопка «Обновить»).

View Manager: закреплённую панель Revit разрешает зарегистрировать только
при запуске, поэтому регистрация здесь. При перезагрузке pyRevit панель уже
зарегистрирована — к ней просто подключается свежая версия интерфейса.
"""
try:
    from pyrevit import HOST_APP
    from eomtools import vm_host, vm_ui

    _host = vm_host.install(HOST_APP.uiapp)
    _host.bind(vm_ui.Controller(_host))
except Exception as _exc:
    try:
        from pyrevit import script
        script.get_logger().warning(u'View Manager: панель не подключена: {0}'.format(_exc))
    except Exception:
        pass
