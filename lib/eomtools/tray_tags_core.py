# -*- coding: utf-8 -*-
"""
Чистая геометрия кнопки «Марки лотков» (Tools.panel/TagTrays), без
Revit API — покрыта ``tests/test_tray_tags_core.py``. Revit-часть —
``tray_tags.py``, раскладка марок — общая с «Марками оборудования»
(``tag_layout.layout``).

Лоток на виде — полоса: отрезок оси (в координатах вида u/v) и
полуширина. Марка смотрит в середину видимой части оси, «прямоугольник
элемента» для раскладки — кусок полосы вокруг этой середины, остальная
полоса режется на куски-препятствия (на лоток марки не кладутся; один
габарит длинного косого лотка накрыл бы полвида). Конец выноски — там,
где выноска выходит из полосы (край лотка), при любом направлении лотка.
"""

import math


def slope_deg(p0, p1):
    """Наклон оси лотка к горизонтали, градусы (90 — вертикальный).
    p0, p1 — (x, y, z)."""
    dx, dy, dz = p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2]
    horiz = math.hypot(dx, dy)
    if horiz < 1e-9:
        return 0.0 if abs(dz) < 1e-9 else 90.0
    return math.degrees(math.atan2(abs(dz), horiz))


def is_sloped(p0, p1, max_deg):
    """Лоток идёт под наклоном (круче max_deg) — такие не маркируются."""
    return slope_deg(p0, p1) > max_deg


def clip_segment(p, q, rect):
    """
    Часть отрезка p→q внутри прямоугольника rect (u0, v0, u1, v1) —
    (t0, t1), параметры вдоль p→q (0 — p, 1 — q), или None, если отрезок
    в rect не заходит (Лианг–Барски).
    """
    x0, y0 = p
    dx, dy = q[0] - x0, q[1] - y0
    t0, t1 = 0.0, 1.0
    for pk, qk in ((-dx, x0 - rect[0]), (dx, rect[2] - x0),
                   (-dy, y0 - rect[1]), (dy, rect[3] - y0)):
        if abs(pk) < 1e-12:
            if qk < 0:
                return None
            continue
        t = float(qk) / pk
        if pk < 0:
            t0 = max(t0, t)
        else:
            t1 = min(t1, t)
        if t0 > t1:
            return None
    return t0, t1


def lerp(p, q, t):
    return tuple(a + (b - a) * t for a, b in zip(p, q))


def _unit(p, q):
    dx, dy = q[0] - p[0], q[1] - p[1]
    ln = math.hypot(dx, dy)
    if ln < 1e-12:
        return None
    return (dx / ln, dy / ln)


def strip_rect(p, q, half_w):
    """Габарит (u0, v0, u1, v1) участка полосы p–q полуширины half_w."""
    d = _unit(p, q)
    if d is None:
        return (p[0] - half_w, p[1] - half_w, p[0] + half_w, p[1] + half_w)
    nu, nv = -d[1] * half_w, d[0] * half_w
    us = [p[0] + nu, p[0] - nu, q[0] + nu, q[0] - nu]
    vs = [p[1] + nv, p[1] - nv, q[1] + nv, q[1] - nv]
    return (min(us), min(vs), max(us), max(vs))


def anchor_rect(p, q, half_w, min_len, t=0.5):
    """
    «Прямоугольник элемента» для раскладки: кусок полосы p–q у точки t
    (0.5 — середина) длиной max(2·half_w, min_len), но не длиннее самой
    полосы и не за её концами.
    """
    d = _unit(p, q)
    at = lerp(p, q, t)
    if d is None:
        return strip_rect(at, at, half_w)
    length = math.hypot(q[0] - p[0], q[1] - p[1])
    half_len = min(max(half_w, min_len * 0.5), length * 0.5)
    s = min(max(t * length, half_len), length - half_len)
    a = (p[0] + d[0] * (s - half_len), p[1] + d[1] * (s - half_len))
    b = (p[0] + d[0] * (s + half_len), p[1] + d[1] * (s + half_len))
    return strip_rect(a, b, half_w)


def strip_pieces(p, q, half_w, max_pieces=40):
    """Полоса p–q кусками (габаритами), чтобы косой лоток не превращался
    в один огромный прямоугольник-препятствие. Кусок — не короче ширины
    полосы, всего не больше max_pieces."""
    length = math.hypot(q[0] - p[0], q[1] - p[1])
    piece = max(2.0 * half_w, length / float(max_pieces), 1e-9)
    n = max(1, int(math.ceil(length / piece)))
    return [strip_rect(lerp(p, q, float(i) / n), lerp(p, q, float(i + 1) / n), half_w)
            for i in range(n)]


def strip_exit(anchor, toward, p, q, half_w):
    """
    Точка, где отрезок anchor→toward выходит из полосы оси p–q
    полуширины half_w (anchor — на оси). Если toward сам в полосе или
    полоса вырождена — anchor.
    """
    d = _unit(p, q)
    if d is None:
        return anchor
    du, dv = toward[0] - anchor[0], toward[1] - anchor[1]
    across = abs(-d[1] * du + d[0] * dv)
    if across <= half_w or across < 1e-12:
        return anchor
    t = half_w / across
    return (anchor[0] + du * t, anchor[1] + dv * t)



def stagger_anchors(strips, near, step):
    """
    Где на каждом лотке ставить марку и с кем в группе: ({key: t},
    {key: номер группы или None}); t — параметр вдоль видимой оси a→b
    (0.5 — середина). strips — [(key, a, b)].

    У параллельных лотков рядом (трасса из нескольких лотков) середины
    стоят на одной линии поперёк трассы, и выноски от них легли бы друг
    на друга (у горизонтальных — одна вертикаль, у вертикальных — одна
    горизонталь). Поэтому такие лотки (параллельны, середины ближе near
    и вдоль, и поперёк) собираются в группу, и точки марок в ней
    расходятся «лесенкой» вдоль трассы с шагом step — по порядку
    поперёк трассы, вокруг общей середины группы. Точка не выходит за
    10 % от концов своего участка. Группа уходит в
    ``tag_layout.TagItem.group``: её марки встают одним блоком.
    """
    info = []  # (key, a, b, d, середина, развёрнут ли)
    for key, a, b in strips:
        d = _unit(a, b)
        flipped = d is not None and (d[0] < -1e-9 or (abs(d[0]) <= 1e-9 and d[1] < 0))
        if flipped:  # у параллельных — одно направление
            a, b, d = b, a, (-d[0], -d[1])
        info.append((key, a, b, d, lerp(a, b, 0.5), flipped))

    parent = list(range(len(info)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(info)):
        di, mi = info[i][3], info[i][4]
        if di is None:
            continue
        for j in range(i):
            dj, mj = info[j][3], info[j][4]
            if dj is None or abs(di[0] * dj[1] - di[1] * dj[0]) > 0.02:
                continue
            du, dv = mj[0] - mi[0], mj[1] - mi[1]
            if (abs(du * di[0] + dv * di[1]) <= near and
                    abs(-di[1] * du + di[0] * dv) <= near):
                parent[find(i)] = find(j)

    groups = {}
    for i in range(len(info)):
        groups.setdefault(find(i), []).append(i)

    result = dict((it[0], 0.5) for it in info)
    group_of = dict((it[0], None) for it in info)
    for number, members in enumerate(groups.values()):
        if len(members) < 2:
            continue
        d = info[members[0]][3]
        n = (-d[1], d[0])
        mids = [info[i][4] for i in members]
        center = (sum(m[0] for m in mids) / len(mids) * d[0] +
                  sum(m[1] for m in mids) / len(mids) * d[1])
        across = sorted(info[i][4][0] * n[0] + info[i][4][1] * n[1] for i in members)
        members.sort(key=lambda i: info[i][4][0] * n[0] + info[i][4][1] * n[1])
        # шаг не меньше расстояния между лотками: лесенка тогда вытянута
        # вдоль трассы, и раскладка ставит марки одним блоком поперёк неё
        # (стопкой над горизонтальной трассой, колонкой у вертикальной)
        group_step = max([step] + [(y - x) * 1.1 for x, y in zip(across, across[1:])])
        for rank, i in enumerate(members):
            key, a, b, _d, _m, flipped = info[i]
            target = center + (rank - (len(members) - 1) * 0.5) * group_step
            t = (target - (a[0] * d[0] + a[1] * d[1])) / math.hypot(b[0] - a[0], b[1] - a[1])
            t = min(max(t, 0.1), 0.9)
            result[key] = 1.0 - t if flipped else t
            group_of[key] = number
    return result, group_of
