# -*- coding: utf-8 -*-
"""Заполнить параметр SMNX_Фильтр у всех элементов модели, у которых он есть.

Нужен для фильтров видов и вкладки «Цвета» View Manager: у наших элементов
SMNX_Фильтр заполнен (например, «ЭОМ»), у элементов смежников его нет.

Одно окно: значение, режим (только пустые / перезаписать) и живой подсчёт,
сколько элементов изменится. Одна транзакция — отменяется Ctrl+Z. Элементы,
занятые другими пользователями, пропускаются.
"""
import io
import os
import uuid

import clr
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')
from System.Windows.Interop import WindowInteropHelper
from System.Windows.Markup import XamlReader

from pyrevit import HOST_APP, forms, revit, script

from Autodesk.Revit.DB import (CheckoutStatus, FailureProcessingResult, FailureSeverity,
                               FilteredElementCollector, IFailuresPreprocessor, StorageType,
                               Transaction, TransactionStatus, WorksharingUtils)

PARAM = u'SMNX_Фильтр'
DEFAULT_VALUE = u'ЭОМ'
doc = revit.doc

if doc.IsFamilyDocument:
    forms.alert(u'Откройте модель проекта, а не семейство.', exitscript=True)
if doc.IsReadOnly:
    forms.alert(u'Модель открыта только для чтения.', exitscript=True)

# ---------- что есть в модели (один проход) ----------
writable, busy, read_only = [], 0, 0      # writable: [(параметр, текущее значение)]
for el in FilteredElementCollector(doc).WhereElementIsNotElementType():
    p = el.LookupParameter(PARAM)
    if p is None or p.StorageType != StorageType.String:
        continue
    if p.IsReadOnly:
        read_only += 1
        continue
    if doc.IsWorkshared and \
            WorksharingUtils.GetCheckoutStatus(doc, el.Id) == CheckoutStatus.OwnedByOtherUser:
        busy += 1
        continue
    writable.append((p, p.AsString() or u''))

if not writable and not busy:
    forms.alert(u'В модели нет элементов с параметром {0}.'.format(PARAM), exitscript=True)


def plan(value, overwrite):
    """(к записи [параметры], уже с этим значением, заполнено другим)"""
    todo, same, other = [], 0, 0
    for p, current in writable:
        if current == value:
            same += 1
        elif current and not overwrite:
            other += 1
        else:
            todo.append(p)
    return todo, same, other


# ---------- окно ----------
here = os.path.dirname(__file__)
with io.open(os.path.join(here, 'dialog.xaml'), 'r', encoding='utf-8') as f:
    win = XamlReader.Parse(f.read())
try:
    WindowInteropHelper(win).Owner = HOST_APP.uiapp.MainWindowHandle
except Exception:
    pass
value_box = win.FindName('value_box')
overwrite_radio = win.FindName('overwrite_radio')
only_empty_radio = win.FindName('only_empty_radio')
todo_label = win.FindName('todo_label')
details_label = win.FindName('details_label')
ok_button = win.FindName('ok_button')
state = {'accepted': False}


def update(*_):
    value = (value_box.Text or u'').strip()
    overwrite = bool(overwrite_radio.IsChecked)
    todo, same, other = plan(value, overwrite)
    ok_button.IsEnabled = bool(value) and bool(todo)
    if not value:
        todo_label.Text = u'Введите значение'
    else:
        todo_label.Text = u'Будет записано: {0}'.format(len(todo))
    lines = [u'Элементов с параметром: {0}'.format(len(writable) + busy + read_only),
             u'Уже «{0}»: {1}'.format(value, same)]
    if other:
        lines.append(u'Заполнено другим значением: {0} — {1}'.format(
            other, u'перезапишутся' if overwrite else u'не трогаю'))
    if busy:
        lines.append(u'Заняты другими пользователями: {0} — пропуск'.format(busy))
    if read_only:
        lines.append(u'Только для чтения: {0} — пропуск'.format(read_only))
    details_label.Text = u'\n'.join(lines)


def accept(sender, args):
    state['accepted'] = True
    win.DialogResult = True


value_box.Text = DEFAULT_VALUE
value_box.TextChanged += update
overwrite_radio.Checked += update
only_empty_radio.Checked += update
ok_button.Click += accept
update()
value_box.Focus()
value_box.SelectAll()
win.ShowDialog()

if not state['accepted']:
    script.exit()

value = value_box.Text.strip()
todo, same, other = plan(value, bool(overwrite_radio.IsChecked))


# ---------- запись ----------
class _SkipWarnings(IFailuresPreprocessor):
    """Предупреждения (например, про группы) не прерывают запись; ошибки — откат."""
    __namespace__ = 'EOM.SmnxFilter.N' + uuid.uuid4().hex

    def PreprocessFailures(self, accessor):
        for f in list(accessor.GetFailureMessages()):
            if f.GetSeverity() == FailureSeverity.Warning:
                accessor.DeleteWarning(f)
            else:
                return FailureProcessingResult.ProceedWithRollBack
        return FailureProcessingResult.Continue


tx = Transaction(doc, u'Заполнить {0} = «{1}»'.format(PARAM, value))
options = tx.GetFailureHandlingOptions()
options.SetFailuresPreprocessor(_SkipWarnings())
tx.SetFailureHandlingOptions(options)
tx.Start()
written, failed = 0, 0
try:
    for p in todo:
        try:
            if p.Set(value):
                written += 1
            else:
                failed += 1
        except Exception:
            failed += 1
    status = tx.Commit()
except Exception as exc:
    if tx.GetStatus() == TransactionStatus.Started:
        tx.RollBack()
    forms.alert(u'Ошибка, ничего не изменено:\n{0}'.format(exc), title=PARAM, exitscript=True)

if status != TransactionStatus.Committed:
    forms.alert(u'Revit отклонил изменение, ничего не изменено.', title=PARAM, exitscript=True)

msg = u'{0} = «{1}» записано: {2}.'.format(PARAM, value, written)
if failed:
    msg += u' Не удалось: {0}.'.format(failed)
forms.alert(msg, title=PARAM)
