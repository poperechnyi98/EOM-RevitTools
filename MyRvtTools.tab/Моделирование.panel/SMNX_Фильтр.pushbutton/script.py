# -*- coding: utf-8 -*-
"""Заполнить параметр SMNX_Фильтр у всех элементов модели, у которых он есть.

Нужен для фильтров видов и вкладки «Цвета» View Manager: у наших элементов
SMNX_Фильтр заполнен (например, «ЭОМ»), у элементов смежников — пуст.

Перед записью показывает, сколько элементов изменится, и спрашивает подтверждение.
Одна транзакция — отменяется Ctrl+Z. Элементы, занятые другими пользователями,
пропускаются.
"""
from pyrevit import forms, revit, script

from Autodesk.Revit.DB import (CheckoutStatus, FailureProcessingResult, FailureSeverity,
                               FilteredElementCollector, IFailuresPreprocessor, StorageType,
                               Transaction, TransactionStatus, WorksharingUtils)

PARAM = u'SMNX_Фильтр'
doc = revit.doc
out = script.get_output()

if doc.IsFamilyDocument:
    forms.alert(u'Откройте модель проекта, а не семейство.', exitscript=True)
if doc.IsReadOnly:
    forms.alert(u'Модель открыта только для чтения.', exitscript=True)

value = forms.ask_for_string(default=u'ЭОМ', title=PARAM,
                             prompt=u'Значение параметра {0} для элементов модели:'.format(PARAM))
if value is None:
    script.exit()
value = value.strip()
if not value:
    forms.alert(u'Значение пустое — нечего записывать.', exitscript=True)

ONLY_EMPTY = u'Заполнить только пустые'
OVERWRITE = u'Перезаписать у всех'
mode = forms.CommandSwitchWindow.show(
    [ONLY_EMPTY, OVERWRITE],
    message=u'«{0}» → {1}. Что делать с элементами, где параметр уже заполнен?'.format(value, PARAM))
if not mode:
    script.exit()

# ---------- что будем менять ----------
todo, same, filled_other, busy, read_only = [], 0, 0, 0, 0
for el in FilteredElementCollector(doc).WhereElementIsNotElementType():
    p = el.LookupParameter(PARAM)
    if p is None or p.StorageType != StorageType.String:
        continue
    if p.IsReadOnly:
        read_only += 1
        continue
    current = p.AsString() or u''
    if current == value:
        same += 1
        continue
    if current and mode == ONLY_EMPTY:
        filled_other += 1
        continue
    if doc.IsWorkshared and \
            WorksharingUtils.GetCheckoutStatus(doc, el.Id) == CheckoutStatus.OwnedByOtherUser:
        busy += 1
        continue
    todo.append(p)

summary = [u'Будет записано «{0}»: {1} эл.'.format(value, len(todo)),
           u'Уже «{0}»: {1}'.format(value, same)]
if filled_other:
    summary.append(u'Заполнено другим значением (не трогаю): {0}'.format(filled_other))
if busy:
    summary.append(u'Заняты другими пользователями (пропуск): {0}'.format(busy))
if read_only:
    summary.append(u'Параметр только для чтения (пропуск): {0}'.format(read_only))

if not todo:
    forms.alert(u'\n'.join(summary) + u'\n\nМенять нечего.', title=PARAM, exitscript=True)
if not forms.alert(u'\n'.join(summary) + u'\n\nЗаписать?', title=PARAM, yes=True, no=True):
    script.exit()


import uuid


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

msg = u'Записано: {0}.'.format(written)
if failed:
    msg += u' Не удалось записать: {0}.'.format(failed)
forms.alert(msg + u'\n\n' + u'\n'.join(summary[1:]), title=PARAM)
