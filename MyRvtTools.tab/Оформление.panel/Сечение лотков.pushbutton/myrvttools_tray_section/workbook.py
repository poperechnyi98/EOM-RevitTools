# -*- coding: utf-8 -*-
"""Read saved cell values without opening or recalculating the workbook."""
import os
import posixpath
import re
from xml.etree import ElementTree as ET
from zipfile import ZipFile


class CellError(object):
    def __init__(self, message):
        self.message = message


class Sheet(object):
    def __init__(self, name, rows):
        self.name = name
        self.rows = rows


_COLUMNS = frozenset(('A', 'B', 'D', 'E', 'F', 'H', 'I', 'J', 'K', 'L'))
_CELL = re.compile(r'^([A-Z]+)([1-9][0-9]*)$')
_MAIN = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
_RELS = '{http://schemas.openxmlformats.org/package/2006/relationships}'
_REL_ID = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'


def _part_path(base, target):
    if target.startswith('/'):
        return target.lstrip('/')
    return posixpath.normpath(posixpath.join(posixpath.dirname(base), target))


def _relationships(archive, filename):
    root = ET.fromstring(archive.read(filename))
    return {
        node.get('Id'): (node.get('Type').rsplit('/', 1)[-1], node.get('Target'))
        for node in root.findall(_RELS + 'Relationship')
        if node.get('TargetMode') != 'External'
    }


def _text(element):
    parts = []
    for child in list(element):
        if child.tag == _MAIN + 't':
            parts.append(child.text or u'')
        elif child.tag == _MAIN + 'r':
            run = child.find(_MAIN + 't')
            if run is not None:
                parts.append(run.text or u'')
    return u''.join(parts)


def _xlsx_cell(cell, shared):
    kind = cell.get('t', 'n')
    node = cell.find(_MAIN + 'v')
    raw = node.text if node is not None else None
    if kind == 'inlineStr':
        string = cell.find(_MAIN + 'is')
        return _text(string) if string is not None else None
    if kind == 'e':
        return CellError(u'ошибка Excel {}'.format(raw or u''))
    if raw is None:
        if cell.find(_MAIN + 'f') is not None:
            return CellError(u'у формулы нет сохранённого результата')
        return None
    if kind == 's':
        try:
            index = int(raw)
            if index < 0:
                raise IndexError()
            return shared[index]
        except (ValueError, IndexError):
            return CellError(u'повреждённая строка Excel')
    if kind == 'b':
        return raw == '1'
    return raw


def _xlsx_sheet(archive, part, shared):
    rows = {}
    with archive.open(part) as stream:
        for _, row in ET.iterparse(stream, events=('end',)):
            if row.tag != _MAIN + 'row':
                continue
            for cell in row.findall(_MAIN + 'c'):
                match = _CELL.match(cell.get('r') or '')
                if match is None or match.group(1) not in _COLUMNS:
                    continue
                value = _xlsx_cell(cell, shared)
                if value is not None:
                    rows.setdefault(int(match.group(2)), {})[match.group(1)] = value
            row.clear()
    return rows


def _read_xlsx(path):
    with ZipFile(path, 'r') as archive:
        package = _relationships(archive, '_rels/.rels')
        workbook = next(_part_path('', target) for kind, target in package.values()
                        if kind == 'officeDocument')
        folder, name = posixpath.split(workbook)
        parts = _relationships(archive, posixpath.join(folder, '_rels', name + '.rels'))
        shared = []
        for kind, target in parts.values():
            if kind == 'sharedStrings':
                root = ET.fromstring(archive.read(_part_path(workbook, target)))
                shared = [_text(item) for item in root.findall(_MAIN + 'si')]
                break
        root = ET.fromstring(archive.read(workbook))
        sheets = []
        for item in root.findall(_MAIN + 'sheets/' + _MAIN + 'sheet'):
            if item.get('state', 'visible') != 'visible':
                continue
            kind, target = parts.get(item.get(_REL_ID), (None, None))
            if kind == 'worksheet':
                rows = _xlsx_sheet(archive, _part_path(workbook, target), shared)
                sheets.append(Sheet(item.get('name'), rows))
        return sheets


def _read_xls(path):
    try:
        import xlrd
    except ImportError:
        raise ValueError(u'Для чтения .xls нужна библиотека xlrd из pyRevit. '
                         u'Восстановите установку pyRevit или пересохраните книгу в .xlsx.')
    workbook = xlrd.open_workbook(path, on_demand=True)
    try:
        sheets = []
        for index, name in enumerate(workbook.sheet_names()):
            sheet = workbook.sheet_by_index(index)
            if sheet.visibility != 0:
                continue
            rows = {}
            for number in range(sheet.nrows):
                row = {}
                for col in _COLUMNS:
                    offset = ord(col) - ord('A')
                    if offset >= sheet.row_len(number):
                        continue
                    cell = sheet.cell(number, offset)
                    if cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
                        continue
                    value = cell.value
                    if cell.ctype == xlrd.XL_CELL_ERROR:
                        value = CellError(u'ошибка Excel {}'.format(
                            xlrd.error_text_from_code.get(value, value)))
                    elif cell.ctype == xlrd.XL_CELL_BOOLEAN:
                        value = bool(value)
                    row[col] = value
                if row:
                    rows[number + 1] = row
            sheets.append(Sheet(name, rows))
        return sheets
    finally:
        workbook.release_resources()


def read_sheets(path):
    suffix = os.path.splitext(path)[1].lower()
    if suffix == '.xlsx':
        return _read_xlsx(path)
    if suffix == '.xls':
        return _read_xls(path)
    raise ValueError(u'Выберите книгу Excel в формате .xlsx или .xls.')
