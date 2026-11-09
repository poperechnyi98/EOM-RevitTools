# -*- coding: utf-8 -*-
u"""
Сравнение уровней и осей модели с уровнями и осями связи (КФП) — часть без
Revit API (кнопка `Tools.panel/CompareLinkDatums`, «Оси и уровни КФП»).

На вход — обычные словари (координаты уже в системе модели, в мм):
  уровень: {"name", "elevation", "id"}
  ось:     {"name", "id", "line": ((x0, y0), (x1, y1))}   — прямая
           {"name", "id", "arc": ((cx, cy), r)}            — дуга
           без "line"/"arc" — ось непонятной формы (сравнивается только имя).

Пары подбираются в три прохода: одинаковое имя → то же имя без учёта
регистра и пробелов → совпадающее положение (другое имя). Всё, что не нашло
пары, — «нет в модели» / «нет в связи». Покрыто
`tests/test_link_datums_core.py`; идёт и под Python 3.
"""

import math
from eomtools.i18n import N_, tr
import re

# Статусы строк отчёта (порядок — порядок сортировки «сначала проблемы»).
OK = u"ok"
MOVED = u"moved"
RENAMED = u"renamed"
MISSING_IN_MODEL = u"missing_in_model"
MISSING_IN_LINK = u"missing_in_link"

STATUS_TEXT = {
    OK: N_(u"Совпадает"),
    MOVED: N_(u"Расходится"),
    RENAMED: N_(u"Другое имя"),
    MISSING_IN_MODEL: N_(u"Нет в модели"),
    MISSING_IN_LINK: N_(u"Нет в связи"),
}
_STATUS_ORDER = [MISSING_IN_MODEL, MOVED, RENAMED, MISSING_IN_LINK, OK]

_PARALLEL_SIN = 1e-9  # синус угла, ниже которого прямые считаются параллельными


def natural_key(text):
    u"""Ключ сортировки: числа внутри строки сравниваются как числа (А2 < А10)."""
    parts = re.split(r"(\d+)", (text or u"").strip().lower())
    return [(0, int(p), u"") if p.isdigit() else (1, 0, p) for p in parts if p != u""]


def loose_name(name):
    u"""Имя без учёта регистра и лишних пробелов: « Ось  А » → «ось а»."""
    return u" ".join((name or u"").split()).lower()


class Row(object):
    u"""
    Строка отчёта: model/link — словарь уровня/оси или None, status — один
    из статусов выше, delta — расхождение, мм (по отметке у уровней, по
    положению у осей; None — не сравнивалось), angle — поворот оси, градусы
    (None у уровней и у дуг), note — пояснение (форма оси и т. п.).
    """

    def __init__(self, model, link, status, delta=None, angle=None, note=u""):
        self.model = model
        self.link = link
        self.status = status
        self.delta = delta
        self.angle = angle
        self.note = note

    @property
    def status_text(self):
        return tr(STATUS_TEXT[self.status])

    @property
    def name(self):
        return (self.model or self.link or {}).get("name") or u""


# --- геометрия осей -----------------------------------------------------------

def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1])


def _cross(a, b):
    return a[0] * b[1] - a[1] * b[0]


def _length(v):
    return math.hypot(v[0], v[1])


def _point_line_distance(p, a, b):
    u"""Расстояние от точки p до бесконечной прямой через a и b."""
    d = _sub(b, a)
    length = _length(d)
    if length == 0:
        return _length(_sub(p, a))
    return abs(_cross(d, _sub(p, a))) / length


def _line_angle(a0, a1, b0, b1):
    u"""Угол между направлениями прямых без учёта направления, 0…90°."""
    da, db = _sub(a1, a0), _sub(b1, b0)
    la, lb = _length(da), _length(db)
    if la == 0 or lb == 0:
        return 0.0
    sin = abs(_cross(da, db)) / (la * lb)
    if sin < _PARALLEL_SIN:
        return 0.0
    return math.degrees(math.asin(min(1.0, sin)))


def grid_deviation(model, link):
    u"""
    (расхождение, мм; угол, °; пояснение) для пары осей. Расхождение прямых —
    наибольшее расстояние от концов каждой оси до прямой другой (длина оси
    и положение пузырей не важны, важна сама линия); дуг — смещение центра
    плюс разница радиусов. None вместо расхождения — оси разной формы или
    форма неизвестна: сравнить нельзя.
    """
    if "line" in model and "line" in link:
        m0, m1 = model["line"]
        l0, l1 = link["line"]
        delta = max(_point_line_distance(l0, m0, m1), _point_line_distance(l1, m0, m1),
                    _point_line_distance(m0, l0, l1), _point_line_distance(m1, l0, l1))
        return delta, _line_angle(m0, m1, l0, l1), u""
    if "arc" in model and "arc" in link:
        (mc, mr), (lc, lr) = model["arc"], link["arc"]
        return _length(_sub(mc, lc)) + abs(mr - lr), None, tr(u"дуга")
    if ("line" in model or "arc" in model) and ("line" in link or "arc" in link):
        return None, None, tr(u"разная форма (прямая/дуга)")
    return None, None, tr(u"форма оси не распознана")


def level_deviation(model, link):
    return abs(model["elevation"] - link["elevation"]), None, u""


# --- подбор пар -----------------------------------------------------------------

def _pair_by(models, links, key, deviation):
    u"""
    Пары с одинаковым key(...) — среди повторяющихся имён ближайшие по
    deviation. Возвращает [(model, link)], пары убираются из списков.
    """
    pairs = []
    by_key = {}
    for link in links:
        by_key.setdefault(key(link), []).append(link)
    for model in list(models):
        candidates = by_key.get(key(model))
        if not candidates:
            continue

        def cost(link):
            delta = deviation(model, link)[0]
            return float("inf") if delta is None else delta

        best = min(candidates, key=cost)
        candidates.remove(best)
        models.remove(model)
        links.remove(best)
        pairs.append((model, best))
    return pairs


def _pair_by_position(models, links, deviation, tolerance):
    u"""Пары без общего имени, совпадающие по положению (в пределах допуска)."""
    pairs = []
    for model in list(models):
        best, best_delta = None, None
        for link in links:
            delta = deviation(model, link)[0]
            if delta is None or delta > tolerance:
                continue
            if best is None or delta < best_delta:
                best, best_delta = link, delta
        if best is not None:
            models.remove(model)
            links.remove(best)
            pairs.append((model, best))
    return pairs


def _compare(models, links, deviation, tolerance):
    models, links = list(models), list(links)
    rows = []

    def add_pair(model, link, same_name):
        delta, angle, note = deviation(model, link)
        if delta is not None and delta > tolerance:
            status = MOVED
        elif same_name:
            status = OK
        else:
            status = RENAMED
        if delta is None and same_name:
            # одноимённые, но сравнить положение нельзя — пусть видно будет
            status = MOVED
        rows.append(Row(model, link, status, delta, angle, note))

    for model, link in _pair_by(models, links, lambda d: d.get("name") or u"", deviation):
        add_pair(model, link, True)
    for model, link in _pair_by(models, links, lambda d: loose_name(d.get("name")), deviation):
        add_pair(model, link, False)
    for model, link in _pair_by_position(models, links, deviation, tolerance):
        add_pair(model, link, False)
    rows.extend(Row(None, link, MISSING_IN_MODEL) for link in links)
    rows.extend(Row(model, None, MISSING_IN_LINK) for model in models)
    return rows


def compare_levels(model_levels, link_levels, tolerance=1.0):
    u"""Строки сравнения уровней, по отметке снизу вверх."""
    rows = _compare(model_levels, link_levels, level_deviation, tolerance)

    def elevation(row):
        return (row.link or row.model)["elevation"]

    return sorted(rows, key=lambda r: (elevation(r), natural_key(r.name)))


def compare_grids(model_grids, link_grids, tolerance=1.0):
    u"""Строки сравнения осей, по имени (как в связи, если ось там есть)."""
    rows = _compare(model_grids, link_grids, grid_deviation, tolerance)
    return sorted(rows, key=lambda r: natural_key((r.link or r.model).get("name")))


def problems_first(rows):
    u"""Те же строки, сначала расхождения (устойчиво внутри статуса)."""
    return sorted(rows, key=lambda r: _STATUS_ORDER.index(r.status))


def count_by_status(rows):
    u"""{статус: количество} только для встретившихся статусов."""
    counts = {}
    for row in rows:
        counts[row.status] = counts.get(row.status, 0) + 1
    return counts


def summary_text(counts):
    u"""«совпадает 12, расходится 1, нет в модели 2» — в порядке статусов."""
    parts = [u"{} {}".format(tr(STATUS_TEXT[s]).lower(), counts[s])
             for s in reversed(_STATUS_ORDER) if counts.get(s)]
    return u", ".join(parts) if parts else tr(u"нет")


def format_mm(value, decimals=1):
    u"""Миллиметры текстом: «1234,5»; None — пусто."""
    if value is None:
        return u""
    text = u"{:.{}f}".format(float(value), decimals)
    if text.startswith(u"-") and float(text) == 0:
        text = text[1:]
    return text.replace(u".", u",")


def format_angle(value):
    u"""Угол текстом: «0,25°»; ноль и None — пусто."""
    if not value:
        return u""
    return u"{:.3f}°".format(float(value)).replace(u".", u",")


def link_matches(name, keyword):
    u"""Имя связи содержит ключевое слово (без учёта регистра); пустое — любое."""
    keyword = loose_name(keyword)
    return not keyword or keyword in loose_name(name)
