# -*- coding: utf-8 -*-
"""Recognize the workbook template and validate the selected cable schedule."""
import math
import re
from collections import namedtuple

from .workbook import CellError


Cable = namedtuple('Cable', 'diameter cores section group_name')
Line = namedtuple('Line', 'method cable_count cables split_groups')
Bundle = namedtuple('Bundle', 'number cables')
TRAY_THICKNESS = 1.5
LADDER_STEP = 19.0


class Section(namedtuple('Section', 'name width height required_width lines bundles errors '
                         'count is_ladder fill_percent')):
    __slots__ = ()

    @property
    def support_height(self):
        return LADDER_STEP if self.is_ladder else TRAY_THICKNESS


Source = namedtuple('Source', 'sheet header width_row height_row required_row type_row fill_row')
BUNDLE_METHOD = u'П'
_BUNDLE_TITLE = re.compile(u'пучок\\s*№\\s*(\\d+)', re.IGNORECASE)
_HEADERS = (('D', u'кол-во кл'), ('E', u'кол-во кабел'),
            ('F', u'кол-во жил'), ('H', u'сечение'), ('I', u'одного кабеля'),
            ('J', u'способ укладки'))

_GROUP_SEPARATOR = re.compile(u'[;,](?:[^\\S\\n]*\\n)*|\\n')

try:
    _string = unicode
except NameError:
    _string = str


def _norm(value):
    if value is None or isinstance(value, CellError):
        return u''
    return u' '.join(_string(value).lower().split())


def recognize(sheet):
    header = None
    labels = {}
    for number in sorted(sheet.rows):
        row = sheet.rows[number]
        label = _norm(row.get('A'))
        for key, title in (('width', u'ширина лотка'), ('height', u'высота лотка'),
                           ('required', u'треб. ширина'), ('type', u'тип лотка'),
                           ('fill', u'заполняемость')):
            if label.startswith(title):
                labels.setdefault(key, number)
        if header is None and all(text in _norm(row.get(col)) for col, text in _HEADERS):
            header = number
    if header and 'width' in labels and 'height' in labels:
        return Source(sheet, header, labels['width'], labels['height'], labels.get('required'),
                      labels.get('type'), labels.get('fill'))
    return None


def find_sources(sheets):
    return [source for source in (recognize(sheet) for sheet in sheets) if source is not None]


def _number(value, integer=False, allow_zero=False):
    if isinstance(value, CellError):
        raise ValueError(value.message + u'. Пересчитайте и сохраните книгу')
    if value is None or isinstance(value, bool):
        raise ValueError(u'ожидается число')
    try:
        text = _string(value).strip().replace(u' ', u'').replace(u'\xa0', u'').replace(u',', u'.')
        number = float(text)
    except (ValueError, TypeError):
        raise ValueError(u'ожидается число')
    if math.isinf(number) or math.isnan(number) or number < 0 or (not allow_zero and number == 0):
        raise ValueError(u'ожидается {}'.format(
            u'неотрицательное число' if allow_zero else u'положительное число'))
    if integer and number != math.floor(number):
        raise ValueError(u'ожидается целое число')
    return int(number) if integer else number


def _name(row):
    name = [row.get(col) for col in ('K', 'L') if _norm(row.get(col))]
    return u' ({})'.format(u' / '.join(_string(value).strip() for value in name)) if name else u''


def _group_names(value, count):
    if value is None or isinstance(value, (CellError, bool)):
        return [u''] * count, False
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = _string(value).strip().replace(u'\r\n', u'\n').replace(u'\r', u'\n')
    parts = _GROUP_SEPARATOR.split(text) if count > 1 else [text.rstrip(u',;')]
    if len(parts) > 1 and not parts[-1]:
        parts.pop()
    names = [u' '.join(part.split()) for part in parts]
    names = [u'' if name in (u'-', u'–', u'—') else name for name in names]
    if len(names) == 1:
        return names * count, False
    return (names + [u''] * max(0, count - len(names)))[:count], True


class _SheetParser(object):
    def __init__(self, source):
        self.source = source
        self.errors = []
        self.lines = []
        self.bundles = {}
        self.bundle_headers = {}

    def error(self, number, col, row, message):
        self.errors.append(u'{}{}, строка {}{} — {}.'.format(col, number, number, _name(row), message))

    def number(self, number, col, row, integer=False, allow_zero=False):
        try:
            return _number(row.get(col), integer, allow_zero)
        except ValueError as error:
            self.error(number, col, row, error.args[0])
            return None

    def method(self, number, row, qty_col, qty, in_bundle):
        raw = row.get('J')
        if isinstance(raw, CellError):
            self.error(number, 'J', row, raw.message + u'. Пересчитайте и сохраните книгу')
            return None
        code = _norm(raw).upper()
        if code != BUNDLE_METHOD:
            try:
                code = _number(raw, integer=True, allow_zero=True)
            except ValueError:
                code = None
        allowed = (BUNDLE_METHOD,) if in_bundle else (1, 2, 3, 4)
        if code not in allowed:
            self.error(number, 'J', row, u'способ укладки {} недопустим при {}{} = {}'.format(
                u'«{}»'.format(raw) if _norm(raw) else u'не задан', qty_col, number, qty))
            return None
        return code

    def parse_row(self, number, row, bundle):
        in_bundle = bundle is not None
        qty_col = 'E' if in_bundle else 'D'
        raw_qty = row.get(qty_col)
        if raw_qty is None or (not isinstance(raw_qty, CellError) and not _norm(raw_qty)):
            return
        qty = self.number(number, qty_col, row, integer=True, allow_zero=True)
        if not qty:
            return
        method = self.method(number, row, qty_col, qty, in_bundle)
        if method is None:
            return
        cores = self.number(number, 'F', row, integer=True)
        section = self.number(number, 'H', row)
        diameter = self.number(number, 'I', row)
        count = qty if in_bundle else self.number(number, 'E', row, integer=True)
        if None in (cores, section, diameter, count):
            return
        if (method in (1, 2)) != (cores == 1):
            self.error(number, 'J', row, u'способ {} несовместим с числом жил {}'.format(method, cores))
            return
        if method in (1, 2) and count not in (3, 4, 5):
            self.error(number, 'E', row, u'для треугольника нужны 3, 4 или 5 одножильных кабелей на КЛ')
            return
        if in_bundle and len(self.bundles[bundle]) + count > 9:
            self.error(number, 'E', row, u'в пучке №{} может быть не более 9 кабелей'.format(bundle))
            return
        names, split_groups = _group_names(row.get('K'), qty)
        cables = [Cable(diameter, cores, section, name) for name in names]
        if in_bundle:
            self.bundles[bundle].extend(cables)
        else:
            self.lines.append(Line(method, count, cables, split_groups))

    def tray_kind(self):
        number = self.source.type_row
        if number is None:
            self.errors.append(u'На листе нет строки «Тип лотка».')
            return None
        row = self.source.sheet.rows[number]
        raw = row.get('B')
        if isinstance(raw, CellError):
            self.error(number, 'B', row, raw.message + u'. Пересчитайте и сохраните книгу')
            return None
        kind = _norm(raw)
        if kind.startswith(u'лестнич'):
            return True
        if kind.startswith((u'перфорир', u'неперфор', u'листов')):
            return False
        self.error(number, 'B', row, u'укажите лестничный или листовой тип лотка')
        return None

    def tray_fill(self):
        number = self.source.fill_row
        if number is None:
            self.errors.append(u'На листе нет строки «Заполняемость, %».')
            return None
        row = self.source.sheet.rows[number]
        value = row.get('B')
        if isinstance(value, _string):
            value = value.strip()
            if value.endswith(u'%'):
                value = value[:-1]
        return self.number(number, 'B', dict(row, B=value), allow_zero=True)

    def parse(self):
        source = self.source
        rows = source.sheet.rows
        bundle = None
        for number in sorted(rows):
            if number <= source.header:
                continue
            row = rows[number]
            label = _norm(row.get('A'))
            if label.startswith(u'примечани'):
                break
            title = _BUNDLE_TITLE.search(label)
            if title:
                bundle = int(title.group(1))
                if bundle in self.bundle_headers:
                    self.error(number, 'A', row, u'повторяется пучок №{}'.format(bundle))
                else:
                    self.bundle_headers[bundle] = number
                    self.bundles[bundle] = []
            self.parse_row(number, row, bundle)

        width = self.number(source.width_row, 'B', rows[source.width_row])
        height = self.number(source.height_row, 'B', rows[source.height_row])
        is_ladder = self.tray_kind()
        fill_percent = self.tray_fill()
        if width is not None and width <= 2 * TRAY_THICKNESS:
            self.error(source.width_row, 'B', rows[source.width_row],
                       u'ширина лотка должна быть больше 3 мм')
        support_height = LADDER_STEP if is_ladder else TRAY_THICKNESS
        if height is not None and is_ladder is not None and height <= support_height:
            self.error(source.height_row, 'B', rows[source.height_row],
                       u'высота лотка должна быть больше {} мм'.format(
                           u'19' if is_ladder else u'1,5'))
        required = None
        if source.required_row is not None:
            try:
                required = _number(rows[source.required_row].get('B'), allow_zero=True)
            except ValueError:
                pass
        bundles = [Bundle(number, self.bundles[number]) for number in sorted(self.bundles)
                   if self.bundles[number]]
        count = sum(len(line.cables) * line.cable_count for line in self.lines)
        count += sum(len(block.cables) for block in bundles)
        if not count and not self.errors:
            self.errors.append(u'На листе нет кабелей для размещения.')
        return Section(source.sheet.name, width, height, required, self.lines, bundles,
                       self.errors, count, is_ladder, fill_percent)


def parse_section(source):
    return _SheetParser(source).parse()
