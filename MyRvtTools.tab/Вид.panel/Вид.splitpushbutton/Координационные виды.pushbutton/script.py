# -*- coding: utf-8 -*-
"""Configure linked coordination views on the active Revit view."""
import os
import sys

from pyrevit import forms, script

__title__ = u'Коорд. виды\nна связях'

_bundle = os.path.dirname(os.path.abspath(__file__))
if _bundle not in sys.path:
    sys.path.insert(0, _bundle)

try:
    from eomlab_coord.runner import run
    run(__revit__)
except Exception as ex:
    script.get_logger().exception('MyRvtTools coordination command failed')
    forms.alert(u'Ошибка запуска координационных видов:\n{}\n\n'
                u'Проверьте активный проект и вид.'.format(ex),
                title=u'MyRvtTools — Координационные виды')
