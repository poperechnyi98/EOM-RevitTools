# -*- coding: utf-8 -*-
"""Compute the cable centres and the occupied outline in millimetres."""
from collections import namedtuple
from itertools import groupby
from math import sqrt

from .section_data import TRAY_THICKNESS


class PlacedCable(namedtuple('PlacedCable', 'cable x y position group_repeat role')):
    __slots__ = ()

    @property
    def radius(self):
        return self.cable.diameter / 2.0


Layout = namedtuple('Layout', 'cables width height overflow_width overflow_height')
Group = namedtuple('Group', 'method cable_class cables')
_TIER_COUNTS = ((), (1,), (2,), (2, 1), (2, 2), (3, 2), (3, 2, 1),
                (3, 2, 2), (3, 3, 2), (4, 3, 2))
_EPSILON = 1e-9
_CABLE_LINE_COLORS = {'phase': (0, 0, 0), 'N': (0, 0, 255), 'PE': (0, 128, 0)}
_FIRE_RESISTANT_COLOR = (255, 0, 0)


def cable_line_color(item, is_fire_resistant):
    if is_fire_resistant:
        return _FIRE_RESISTANT_COLOR
    return _CABLE_LINE_COLORS.get(item.role)


def _single_core_line(cable, count):
    d = cable.diameter
    radius = d / 2.0
    top = radius + sqrt(3.0) * radius
    phases = [PlacedCable(cable, radius, radius, None, None, 'phase'),
              PlacedCable(cable, d, top, None, None, 'phase'),
              PlacedCable(cable, radius + d, radius, None, None, 'phase')]
    if count >= 4:
        phases.append(PlacedCable(cable, radius + 2.0 * d, radius, None, None, 'N'))
    if count == 5:
        phases.append(PlacedCable(cable, 2.0 * d, top, None, None, 'PE'))
    return phases


def _right(cables):
    return max(item.x + item.radius for item in cables)


def _left(cables):
    return min(item.x - item.radius for item in cables)


def _tangent_offset(distance, orthogonal_offset):
    if abs(orthogonal_offset) >= distance:
        return 0.0
    return sqrt(distance * distance - orthogonal_offset * orthogonal_offset)


def _bundle(cables):
    ordered = sorted(cables, key=lambda cable: -cable.diameter)
    placed = []
    previous_row = []
    index = 0
    for count in _TIER_COUNTS[len(ordered)]:
        selected = ordered[index:index + count]
        index += count
        width = sum(cable.diameter for cable in selected)
        left = 0.0
        if previous_row:
            left = (_left(previous_row) + _right(previous_row) - width) / 2.0
            if abs(_right(previous_row) - _left(previous_row) - width) < _EPSILON:
                left += previous_row[0].radius
        row = []
        for cable in selected:
            radius = cable.diameter / 2.0
            x = left + radius
            y = radius
            for support in placed:
                delta = x - support.x
                minimum = radius + support.radius
                if abs(delta) < minimum:
                    y = max(y, support.y + _tangent_offset(minimum, delta))
            if previous_row:
                y = max(y, max(support.y for support in previous_row))
            row.append(PlacedCable(cable, x, y, None, None, None))
            left += cable.diameter
        placed.extend(row)
        previous_row = row
    origin = _left(placed)
    return [item._replace(x=item.x - origin) for item in placed]


def _groups(section):
    position = 1
    for line in section.lines:
        if line.method in (1, 2):
            pattern = _single_core_line(line.cables[0], line.cable_count)
            pattern.sort(key=lambda item: item.x)
            for repeat, cable in enumerate(line.cables):
                start = repeat * line.cable_count + 1
                group_repeat = repeat if line.split_groups else None
                numbered = [item._replace(cable=cable, group_repeat=group_repeat,
                                          position=u'{}.{}'.format(position, index))
                            for index, item in enumerate(pattern, start)]
                yield Group(line.method, 1, numbered)
            position += 1
    for line in section.lines:
        if line.method in (3, 4):
            for cable in line.cables:
                radius = cable.diameter / 2.0
                for _ in range(line.cable_count):
                    placed = PlacedCable(cable, radius, radius, u'{}'.format(position), None, None)
                    yield Group(line.method, 2, [placed])
                    position += 1
    for bundle in section.bundles:
        cables = _bundle(bundle.cables)
        numbered = [item._replace(position=u'{}'.format(index))
                    for index, item in enumerate(cables, position)]
        yield Group(None, 3, numbered)
        position += len(cables)


def _gap(previous, group):
    if previous.cable_class != group.cable_class or not (
            previous.method in (1, 3) or group.method in (1, 3)):
        return 0.0
    return max(item.cable.diameter for item in previous.cables + group.cables)


def _shift(placed, previous, group, occupied_width):
    if previous is None:
        return 0.0
    gap = _gap(previous, group)
    if gap:
        return occupied_width + gap
    offset = 0.0
    for item in group.cables:
        for existing in reversed(placed):
            distance = existing.radius + item.radius
            delta = item.y - existing.y
            if abs(delta) < distance:
                tangent = _tangent_offset(distance, delta)
                offset = max(offset, existing.x + tangent - item.x)
    return offset


def plan_layout(section):
    if section.errors:
        raise ValueError(u'Сначала исправьте ошибки выбранного листа.')
    placed = []
    previous = None
    width = 0.0
    for group in _groups(section):
        offset = _shift(placed, previous, group, width)
        new_items = [item._replace(x=item.x + offset) for item in group.cables]
        placed.extend(new_items)
        width = max(width, _right(new_items))
        previous = group
    height = max(item.y + item.radius for item in placed)
    inner_width = section.width - 2 * TRAY_THICKNESS
    inner_height = section.height - section.support_height
    return Layout(placed, width, height, max(0.0, width - inner_width),
                  max(0.0, height - inner_height))


def tray_region_boundaries(section):
    width, height = section.width, section.height
    wall = TRAY_THICKNESS
    right = width - wall
    support = section.support_height
    outer = [(0.0, height), (0.0, 0.0), (width, 0.0), (width, height),
             (right, height), (right, support), (wall, support),
             (wall, height), (0.0, height)]
    boundaries = [outer]
    if section.is_ladder:
        under_step = support - wall
        boundaries.append([(wall, wall), (wall, under_step), (right, under_step),
                           (right, wall), (wall, wall)])
    return boundaries


def group_legend(cables, use_group_names=False):
    def key(item):
        return item.position.split(u'.', 1)[0], item.group_repeat if use_group_names else None

    lines = []
    for _, group in groupby(cables, key=key):
        items = list(group)
        first = items[0]
        label = first.position if len(items) == 1 else u'{}-{}'.format(
            first.position, items[-1].position)
        name = first.cable.group_name if use_group_names else u''
        lines.append(u'{}_{}'.format(label, name or u'Номер группы'))
    return u'\r'.join(lines)


def fill_label(percent):
    return u'{:.1f}%'.format(percent).replace(u'.', u',')
