# -*- coding: utf-8 -*-
"""View Manager, вкладка «Шаблоны вида»: экспорт шаблонов в свою папку и импорт обратно.

Вызывать только из контекста Revit API (ExternalEvent).

Revit не умеет сохранять один шаблон вида в отдельный файл, поэтому каждый шаблон
кладётся в маленький файл-контейнер .rvt: пустой проект, в который скопирован шаблон
вместе со всем, от чего он зависит (фильтры, типы линий, штриховки и т. п.).
Имя файла = имя шаблона.

Импорт открывает такой файл в фоне, копирует из него шаблон(ы) вида в текущую модель
и закрывает файл без сохранения. Одно действие — отменяется Ctrl+Z.
При совпадении имени:
- 'replace' — виды со старым шаблоном переключаются на новый, старый удаляется,
  новый получает его имя;
- 'copy' — новый шаблон добавляется рядом с именем «<имя> (импорт)».
"""
try:
    unicode
except NameError:
    unicode = str

import os
import uuid

from Autodesk.Revit.DB import (
    CopyPasteOptions, DuplicateTypeAction, ElementId, ElementTransformUtils,
    FailureProcessingResult, FailureSeverity, FilteredElementCollector,
    IDuplicateTypeNamesHandler, IFailuresPreprocessor, SaveAsOptions, Transaction,
    TransactionStatus, Transform, UnitSystem, View, ViewFamilyType)
from System.Collections.Generic import List

from eomtools import vm_revit
from eomtools.vm_revit import ViewManagerError, _id_value

EXT = u'.rvt'
BAD_CHARS = u'\\/:*?"<>|'
_NAMESPACE = 'EOM.ViewManager.Templates.N' + uuid.uuid4().hex


class _UseDestinationTypes(IDuplicateTypeNamesHandler):
    """Одинаковые типы (линии, штриховки…) — брать уже существующие в модели."""
    __namespace__ = _NAMESPACE

    def OnDuplicateTypeNamesFound(self, args):
        return DuplicateTypeAction.UseDestinationTypes


class _DropWarnings(IFailuresPreprocessor):
    __namespace__ = _NAMESPACE

    def PreprocessFailures(self, accessor):
        for f in list(accessor.GetFailureMessages()):
            if f.GetSeverity() == FailureSeverity.Warning:
                accessor.DeleteWarning(f)
            else:
                return FailureProcessingResult.ProceedWithRollBack
        return FailureProcessingResult.Continue


def _options():
    opts = CopyPasteOptions()
    opts.SetDuplicateTypeNamesHandler(_UseDestinationTypes())
    return opts


def view_templates(doc):
    """[{'id', 'name', 'type'}] шаблонов вида модели, по имени."""
    result = []
    for v in FilteredElementCollector(doc).OfClass(View):
        if v.IsTemplate:
            result.append({'id': _id_value(v.Id), 'name': v.Name, 'type': unicode(v.ViewType)})
    result.sort(key=lambda x: x['name'].lower())
    return result


def file_name(template_name):
    name = template_name
    for ch in BAD_CHARS:
        name = name.replace(ch, u'_')
    return name.strip() + EXT


def library(folder):
    """Файлы шаблонов в папке: [{'name', 'path'}]."""
    if not folder or not os.path.isdir(folder):
        return []
    items = []
    for f in os.listdir(folder):
        if f.lower().endswith(EXT) and not f.startswith(u'~'):
            # резервные копии Revit: «имя.0001.rvt»
            stem = f[:-len(EXT)]
            if len(stem) > 5 and stem[-5] == u'.' and stem[-4:].isdigit():
                continue
            items.append({'name': stem, 'path': os.path.join(folder, f)})
    items.sort(key=lambda x: x['name'].lower())
    return items


# ---------- экспорт ----------
def export(uiapp, doc, template_ids, folder):
    """Сохранить каждый шаблон в <папка>/<имя шаблона>.rvt. Возвращает [пути]."""
    if not folder or not os.path.isdir(folder):
        raise ViewManagerError(u'Не выбрана папка для шаблонов.')
    saved = []
    app = uiapp.Application
    for tid in template_ids:
        template = doc.GetElement(ElementId(tid))
        if template is None or not template.IsTemplate:
            continue
        path = os.path.join(folder, file_name(template.Name))
        box = app.NewProjectDocument(UnitSystem.Metric)
        try:
            tx = Transaction(box, u'Шаблон вида')
            options = tx.GetFailureHandlingOptions()
            options.SetFailuresPreprocessor(_DropWarnings())
            tx.SetFailureHandlingOptions(options)
            tx.Start()
            ElementTransformUtils.CopyElements(doc, List[ElementId]([template.Id]), box,
                                               Transform.Identity, _options())
            if tx.Commit() != TransactionStatus.Committed:
                raise ViewManagerError(u'Не удалось скопировать шаблон «{0}».'.format(template.Name))
            save = SaveAsOptions()
            save.OverwriteExistingFile = True
            box.SaveAs(path, save)
            saved.append(path)
        finally:
            box.Close(False)
    return saved


# ---------- импорт ----------
def templates_in_file(uiapp, path):
    """Имена шаблонов вида внутри файла библиотеки (открывает файл в фоне)."""
    src = uiapp.Application.OpenDocumentFile(path)
    try:
        return [t['name'] for t in view_templates(src)]
    finally:
        src.Close(False)


def _all_ids(doc):
    ids = set(_id_value(i) for i in
              FilteredElementCollector(doc).WhereElementIsNotElementType().ToElementIds())
    ids.update(_id_value(i) for i in
               FilteredElementCollector(doc).WhereElementIsElementType().ToElementIds())
    return ids


def _kind(el):
    from Autodesk.Revit.DB import (FillPatternElement, LinePatternElement, ParameterElement,
                                   ParameterFilterElement)
    if isinstance(el, ParameterFilterElement):
        return u'Фильтры'
    if isinstance(el, LinePatternElement):
        return u'Типы линий (образцы)'
    if isinstance(el, FillPatternElement):
        return u'Штриховки'
    if isinstance(el, ParameterElement):
        return u'Параметры'
    try:
        if el.Category is not None:
            return el.Category.Name
    except Exception:
        pass
    return el.GetType().Name


def _extras(doc, before, templates):
    """{вид элемента: [имена]} — что добавилось в модель кроме шаблонов и фильтров.

    Не считаются: служебные части самого шаблона (настройки солнца и теней
    «<В сеансе, освещение>» и всё, что принадлежит виду-шаблону)."""
    from Autodesk.Revit.DB import SunAndShadowSettings
    groups = {}
    for value in _all_ids(doc) - before - templates:
        el = doc.GetElement(ElementId(value))
        if el is None or isinstance(el, SunAndShadowSettings):
            continue
        try:
            if el.ViewSpecific and _id_value(el.OwnerViewId) in templates:
                continue
        except Exception:
            pass
        kind = _kind(el)
        if kind == u'Фильтры':
            continue                         # фильтры шаблона — это нормально
        name = getattr(el, 'Name', None) or u'#{0}'.format(value)
        groups.setdefault(kind, []).append(name)
    return groups


def _ask_keep(groups):
    from System.Windows import MessageBox, MessageBoxButton, MessageBoxImage, MessageBoxResult
    lines = []
    for kind in sorted(groups):
        names = sorted(groups[kind])
        tail = u' и ещё {0}'.format(len(names) - 8) if len(names) > 8 else u''
        lines.append(u'• {0}: {1}{2}'.format(kind, u', '.join(names[:8]), tail))
    answer = MessageBox.Show(
        u'Вместе с шаблоном в модель добавится не только он и его фильтры:\n\n{0}\n\n'
        u'Да — оставить (шаблон будет как в файле).\nНет — отменить импорт, модель не изменится.'
        .format(u'\n'.join(lines)), u'View Manager — импорт шаблона',
        MessageBoxButton.YesNo, MessageBoxImage.Warning)
    return answer == MessageBoxResult.Yes


def import_files(uiapp, doc, paths, mode, label):
    """Скопировать шаблоны из файлов библиотеки в модель.

    Если в файле один шаблон, он получает имя файла (файл переименовали — шаблон
    придёт с новым именем). mode: 'replace' | 'copy'.
    Возвращает [(имя, 'новый'|'заменён'|'копия')]."""
    sources = []
    try:
        for path in paths:
            src = uiapp.Application.OpenDocumentFile(path)
            stem = os.path.splitext(os.path.basename(path))[0]
            sources.append((src, stem))
        existing = dict((t['name'], ElementId(t['id'])) for t in view_templates(doc))
        report = []
        before = _all_ids(doc)
        new_templates = set()

        def action():
            for src, stem in sources:
                in_file = view_templates(src)
                for t in in_file:
                    name = stem if len(in_file) == 1 else t['name']
                    old_id = existing.get(name)
                    if old_id is not None:
                        # временно переименовать старый, чтобы новый встал под своим именем
                        doc.GetElement(old_id).Name = u'{0} (старый {1})'.format(name, uuid.uuid4().hex[:6])
                    new_ids = ElementTransformUtils.CopyElements(
                        src, List[ElementId]([ElementId(t['id'])]), doc, Transform.Identity, _options())
                    new_tpl = None
                    for nid in new_ids:
                        el = doc.GetElement(nid)
                        if isinstance(el, View) and el.IsTemplate:
                            new_tpl = el
                    if new_tpl is None:
                        raise ViewManagerError(u'Шаблон «{0}» не скопировался.'.format(name))
                    new_templates.add(_id_value(new_tpl.Id))
                    if old_id is None:
                        new_tpl.Name = name
                        report.append((name, u'новый'))
                    elif mode == 'replace':
                        old_val = _id_value(old_id)
                        for v in FilteredElementCollector(doc).OfClass(View):
                            if not v.IsTemplate and _id_value(v.ViewTemplateId) == old_val:
                                v.ViewTemplateId = new_tpl.Id
                        # шаблон по умолчанию у типов видов (например «План этажа»)
                        for vft in FilteredElementCollector(doc).OfClass(ViewFamilyType):
                            try:
                                if _id_value(vft.DefaultTemplateId) == old_val:
                                    vft.DefaultTemplateId = new_tpl.Id
                            except Exception:
                                pass
                        doc.Delete(old_id)
                        new_tpl.Name = name
                        report.append((name, u'заменён'))
                    else:
                        # сначала освободить имя: новый — «(импорт)», потом старому вернуть своё
                        new_tpl.Name = _free_name(doc, u'{0} (импорт)'.format(name))
                        doc.GetElement(old_id).Name = name
                        report.append((new_tpl.Name, u'копия'))
            extras = _extras(doc, before, new_templates)
            if extras and not _ask_keep(extras):
                raise ViewManagerError(u'Импорт отменён — модель не изменена.')

        vm_revit._run(doc, label, action, tolerate_warnings=True)
        return report
    finally:
        for src in sources:
            try:
                src.Close(False)
            except Exception:
                pass


def _free_name(doc, wanted):
    names = set(t['name'] for t in view_templates(doc))
    if wanted not in names:
        return wanted
    n = 2
    while u'{0} {1}'.format(wanted, n) in names:
        n += 1
    return u'{0} {1}'.format(wanted, n)
