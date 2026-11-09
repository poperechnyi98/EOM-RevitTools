# -*- coding: utf-8 -*-
"""
Раскладка марок оборудования без пересечений — чистая 2D-геометрия, без
Revit API (поэтому модуль можно гонять и проверять вне Revit). Всю
работу с Revit (сбор элементов, размеры марок, запись позиций) делает
``equipment_tags.py``, сюда приходят уже прямоугольники в координатах
вида (u — вправо по экрану, v — вверх, единицы — футы модели).

Как раскладывается (см. :func:`layout`):

  1. **Кучки.** Оборудование, стоящее ближе ``cluster_dist`` друг к другу
     (по зазору между габаритами), объединяется в одну кучку — у кучки
     марки ставятся одним согласованным блоком, а не каждая сама по себе.
  2. **Форма блока** зависит от формы кучки:
       - «столбик» (кучка выше, чем шире — оборудование стоит одно над
         другим): марки в колонку сбоку, на ОДНОМ отступе от кучки,
         каждая напротив своего элемента; если соседние марки не влезают
         по высоте, они раздвигаются с зазором ``gap`` (минимально
         отходя от своих элементов). Выноска — наклонная от элемента до
         полки длиной ``shelf`` перед маркой. Если столбик плотнее, чем
         высота марок, одна колонка вылезает за кучку вверх и вниз и
         выноски расходятся крутым «веером» — тогда марки ставятся
         двумя колонками, по обе стороны кучки;
       - «стопка» (одиночный элемент или оборудование рядом по
         горизонтали): марки стопкой друг над другом над (или под)
         кучкой, выноски прямоугольные — вертикально вверх от элемента,
         затем горизонтальная полка к марке. Порядок марок в стопке
         подобран так, чтобы выноски не пересекались: ближайшая к
         кучке марка — у крайнего к колонке марок элемента. Длинный ряд
         может получить две стопки: левая половина — влево, правая —
         вправо (вдвое ниже и короче);
       - «сетка» (несколько рядов и столбцов): пробуются разбиения —
         по рядам, по столбцам, крайние ряды (столбцы) стопками наружу
         + середина колонками марок по бокам, каждый элемент отдельно,
         вся сетка одним блоком — и берётся разбиение с наименьшим
         суммарным штрафом. Части сетки ставятся от крайних к
         центральной: крайним есть куда вынести марки наружу.
  3. **Выбор стороны.** Для каждой кучки перебираются варианты (справа/
     слева, вверх/вниз, ½×/1×/2×/3× отступ, запасная форма блока) и
     выбирается вариант с наименьшим штрафом: наложение марок на уже
     поставленные марки и на оборудование, пересечение и наложение
     выносок друг на друга (в том числе внутри одного блока), выноски
     через марки и чужое оборудование, длина, наклонные выноски
     (заметно наклонные и особенно круче 45° — это и есть «веер»,
     похожий на скаты крыши). Кучки обрабатываются от самых больших к
     одиночным — большим блокам сложнее найти место.
"""


# ------------------------------------------------------------
# ПРЯМОУГОЛЬНИКИ И ОТРЕЗКИ
# ------------------------------------------------------------

def rect_w(r):
    return r[2] - r[0]


def rect_h(r):
    return r[3] - r[1]


def rect_center(r):
    return ((r[0] + r[2]) * 0.5, (r[1] + r[3]) * 0.5)


def rect_union(rects):
    rects = list(rects)
    return (min(r[0] for r in rects), min(r[1] for r in rects),
            max(r[2] for r in rects), max(r[3] for r in rects))


def rect_gap(a, b):
    """Расстояние между прямоугольниками (0, если касаются/перекрываются)."""
    dx = max(0.0, max(a[0], b[0]) - min(a[2], b[2]))
    dy = max(0.0, max(a[1], b[1]) - min(a[3], b[3]))
    return (dx * dx + dy * dy) ** 0.5


def rect_overlap_area(a, b):
    dx = min(a[2], b[2]) - max(a[0], b[0])
    dy = min(a[3], b[3]) - max(a[1], b[1])
    if dx <= 0 or dy <= 0:
        return 0.0
    return dx * dy


def rect_inflate(r, d):
    return (r[0] - d, r[1] - d, r[2] + d, r[3] + d)


def segment_hits_rect(p, q, r):
    """Проходит ли отрезок p-q через внутренность прямоугольника r
    (Лианг–Барски; касание границы не считается)."""
    x0, y0 = p
    dx, dy = q[0] - x0, q[1] - y0
    t0, t1 = 0.0, 1.0
    for pk, qk in ((-dx, x0 - r[0]), (dx, r[2] - x0),
                   (-dy, y0 - r[1]), (dy, r[3] - y0)):
        if abs(pk) < 1e-12:
            if qk <= 0:
                return False
            continue
        t = float(qk) / pk
        if pk < 0:
            if t > t1:
                return False
            if t > t0:
                t0 = t
        else:
            if t < t0:
                return False
            if t < t1:
                t1 = t
    return t1 - t0 > 1e-9


def exit_point(p, q, r):
    """
    Точка, где отрезок p→q (p внутри прямоугольника r) выходит за его
    границу. Если p снаружи или q тоже внутри — возвращает p как есть.
    Нужна, чтобы выноска начиналась от края УГО, а не из его середины.
    """
    if not (r[0] <= p[0] <= r[2] and r[1] <= p[1] <= r[3]):
        return p
    dx, dy = q[0] - p[0], q[1] - p[1]
    t = 1.0
    if dx > 1e-12:
        t = min(t, float(r[2] - p[0]) / dx)
    elif dx < -1e-12:
        t = min(t, float(r[0] - p[0]) / dx)
    if dy > 1e-12:
        t = min(t, float(r[3] - p[1]) / dy)
    elif dy < -1e-12:
        t = min(t, float(r[1] - p[1]) / dy)
    if t >= 1.0:
        return p
    return (p[0] + dx * t, p[1] + dy * t)


def extrapolate_rect(r1, k1, r2, k2, k0, tol=1e-4):
    """
    Габарит вложенной аннотации (УГО) элемента на виде масштаба k0 по
    габаритам элемента на том же виде при масштабах k1 и k2 (k1 < k2,
    оба много крупнее k0). Аннотация растёт с масштабом линейно, а 3D-тело
    семейства — нет, поэтому каждая сторона габарита — линейная функция
    масштаба, пока при k1/k2 её задаёт аннотация; продолжаем её до k0.
    Сторона, которая между k1 и k2 не сдвинулась (аннотация в эту сторону
    от своей точки не выходит), остаётся как есть. Ни одна сторона не
    сдвинулась — аннотации нет: None. Вырожденный результат — тоже None.
    """
    if r1 is None or r2 is None or k2 == k1:
        return None
    if all(abs(b - a) <= tol for a, b in zip(r1, r2)):
        return None
    t = float(k0 - k1) / (k2 - k1)
    r = tuple(a + (b - a) * t for a, b in zip(r1, r2))
    if r[2] - r[0] <= tol or r[3] - r[1] <= tol:
        return None
    return r


def rect_intersection(a, b):
    """Общая часть прямоугольников или None."""
    r = (max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3]))
    if r[2] <= r[0] or r[3] <= r[1]:
        return None
    return r


def _cross(o, a, b):
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def segments_cross(p1, p2, q1, q2):
    """Строгое пересечение отрезков (общие концы/касание не считаются)."""
    d1 = _cross(q1, q2, p1)
    d2 = _cross(q1, q2, p2)
    d3 = _cross(p1, p2, q1)
    d4 = _cross(p1, p2, q2)
    return (((d1 > 1e-12 and d2 < -1e-12) or (d1 < -1e-12 and d2 > 1e-12)) and
            ((d3 > 1e-12 and d4 < -1e-12) or (d3 < -1e-12 and d4 > 1e-12)))


def segments_overlap(p1, p2, q1, q2, tol=1e-9):
    """Отрезки лежат на одной прямой и накладываются на общем участке
    (выноска поверх выноски — :func:`segments_cross` такого не видит)."""
    if abs(_cross(p1, p2, q1)) > tol or abs(_cross(p1, p2, q2)) > tol:
        return False
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    ll = dx * dx + dy * dy
    if ll < tol:
        return False
    t1 = ((q1[0] - p1[0]) * dx + (q1[1] - p1[1]) * dy) / ll
    t2 = ((q2[0] - p1[0]) * dx + (q2[1] - p1[1]) * dy) / ll
    lo, hi = max(0.0, min(t1, t2)), min(1.0, max(t1, t2))
    return (hi - lo) * ll ** 0.5 > 1e-6


def _seg_len(p, q):
    return ((q[0] - p[0]) ** 2 + (q[1] - p[1]) ** 2) ** 0.5


# ------------------------------------------------------------
# ВХОД / ВЫХОД
# ------------------------------------------------------------

class TagItem(object):
    """
    Один элемент под марку.

    key       — что угодно, по чему вызывающий потом найдёт свою марку;
    anchor    — (u, v) точка, куда смотрит выноска (центр/точка вставки);
    elem_rect — габарит элемента на виде (u0, v0, u1, v1);
    size      — (w, h) габарит самой марки без выноски;
    attach_dv — на сколько по высоте точка, где Revit подводит выноску к
                марке, выше (+) или ниже (−) центра этого габарита. Revit
                подводит выноску к середине ПОЛНОЙ высоты марки, а у
                двухэтажной марки с одним верхним этажом в раскладку идёт
                только верхняя половина — выноска подходит к её нижнему
                краю (к черте между этажами). Полка выноски ставится на эту
                высоту, иначе у марки получается «излом» перед полкой.
    group     — элементы с одинаковым group (не None) всегда в одной кучке
                и раскладываются одним блоком, без разбиения «сетки» на
                части (параллельные лотки одной трассы — «Марки лотков»).
    """

    def __init__(self, key, anchor, elem_rect, size, attach_dv=0.0, group=None):
        self.key = key
        self.anchor = anchor
        self.elem_rect = elem_rect
        self.size = size
        self.attach_dv = attach_dv
        self.group = group


class Placement(object):
    """
    Результат для одного элемента: tag_rect — где должен оказаться
    габарит марки; elbow — точка излома выноски; end — конец выноски у
    элемента; attach — точка, где выноска подходит к марке (край марки).
    """

    def __init__(self, key, tag_rect, elbow, end, attach):
        self.key = key
        self.tag_rect = tag_rect
        self.elbow = elbow
        self.end = end
        self.attach = attach

    def leader_segments(self):
        return [(self.end, self.elbow), (self.elbow, self.attach)]


# ------------------------------------------------------------
# КУЧКИ
# ------------------------------------------------------------

def find_clusters(items, cluster_dist, split=True):
    """Объединение-поиск по зазору между габаритами элементов. split —
    сразу резать «сетки» на ряды (:func:`_split_2d`); без него кучки
    возвращаются целиком."""
    parent = list(range(len(items)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            same_group = items[i].group is not None and items[i].group == items[j].group
            if same_group or rect_gap(items[i].elem_rect, items[j].elem_rect) <= cluster_dist:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[ri] = rj

    groups = {}
    for i, it in enumerate(items):
        groups.setdefault(find(i), []).append(it)
    if not split:
        return list(groups.values())
    result = []
    for g in groups.values():
        result.extend(_split_2d(g))
    return result


def _split_2d(cluster):
    """Ряды или столбцы сетки — что даёт меньше частей (см.
    :func:`_split_options`)."""
    opts = _split_options(cluster)
    return opts[0]


def _split_options(cluster):
    """
    Варианты разбиения кучки на части, которые раскладываются по
    отдельности. Обычная кучка (одна линия) — только она сама. «Сетка»
    (несколько рядов и столбцов) — ряды, столбцы и каждый элемент
    отдельно: одной колонкой/стопкой на всю сетку марки не поставить
    без длинных выносок через чужое оборудование, а у средних рядов
    стопка вверх/вниз упирается в соседние ряды — там марки часто лучше
    встают поодиночке в просвет между рядами. Какой вариант лучше,
    решает :func:`layout` по суммарному штрафу.
    """
    if len(cluster) < 3:
        return [[cluster]]
    xs = [it.anchor[0] for it in cluster]
    ys = [it.anchor[1] for it in cluster]
    size = max(max(rect_w(it.elem_rect), rect_h(it.elem_rect)) for it in cluster)
    if min(max(xs) - min(xs), max(ys) - min(ys)) <= size:
        return [[cluster]]

    def lines(axis):
        tol = sum((rect_h(it.elem_rect) if axis == 1 else rect_w(it.elem_rect))
                  for it in cluster) / float(len(cluster)) * 0.75
        ordered = sorted(cluster, key=lambda it: it.anchor[axis])
        groups = [[ordered[0]]]
        for it in ordered[1:]:
            if it.anchor[axis] - groups[-1][-1].anchor[axis] > tol:
                groups.append([])
            groups[-1].append(it)
        return groups

    rows = lines(1)
    cols = lines(0)
    first, second = (rows, cols) if len(rows) <= len(cols) else (cols, rows)
    options = [first, second]
    # крайние ряды (столбцы) — стопками наружу с прямоугольными
    # выносками, середина — одним блоком (колонки марок по бокам)
    for lines_ in (rows, cols):
        if len(lines_) >= 3:
            inner = [it for line in lines_[1:-1] for it in line]
            options.append([lines_[0], lines_[-1], inner])
    options.append([[it] for it in cluster])
    options.append([cluster])
    return options


def _keeps_groups(parts):
    """Ни одна группа (:attr:`TagItem.group`) не разрезана по частям."""
    seen = {}
    for index, part in enumerate(parts):
        for it in part:
            if it.group is not None and seen.setdefault(it.group, index) != index:
                return False
    return True


def _is_column(cluster):
    """Кучка — «столбик» (оборудование одно над другим)?"""
    if len(cluster) < 2:
        return False
    xs = [it.anchor[0] for it in cluster]
    ys = [it.anchor[1] for it in cluster]
    return (max(ys) - min(ys)) > (max(xs) - min(xs))


# ------------------------------------------------------------
# ВАРИАНТЫ БЛОКА МАРОК
# ------------------------------------------------------------

def _spread_1d(desired, sizes, gap):
    """
    Раздвигает отрезки вдоль оси так, чтобы они не перекрывались (с
    зазором gap) и центры отошли от желаемых как можно меньше.
    desired — желаемые центры (уже по возрастанию), sizes — длины.
    Классика: сливаем налезающие группы и центрируем каждую группу на
    среднем желаемом положении.
    """
    # блок: индексы членов, их смещения от начала блока, начало, длина;
    # want — сумма «желаемых начал» членов (желаемое начало блока, если
    # мерить от этого члена), начало = want / число членов
    blocks = []
    for i, c in enumerate(desired):
        start = c - sizes[i] * 0.5
        blocks.append({"idx": [i], "start": start, "offsets": [0.0],
                       "length": sizes[i], "want": start})
        while len(blocks) > 1:
            b = blocks[-1]
            a = blocks[-2]
            if a["start"] + a["length"] + gap <= b["start"] + 1e-12:
                break
            # слить b в a: члены b сдвигаются на shift от начала a
            shift = a["length"] + gap
            a["idx"].extend(b["idx"])
            a["offsets"].extend(off + shift for off in b["offsets"])
            a["want"] += b["want"] - shift * len(b["idx"])
            a["length"] = a["length"] + gap + b["length"]
            blocks.pop()
            a["start"] = a["want"] / float(len(a["idx"]))

    centers = [0.0] * len(desired)
    for b in blocks:
        for k, off in zip(b["idx"], b["offsets"]):
            centers[k] = b["start"] + off + sizes[k] * 0.5
    return centers


def _layout_side(cluster, side, off, gap, shelf, box=None):
    """Колонка марок сбоку кучки, каждая напротив своего элемента.
    box — габарит, от которого считается отступ (по умолчанию — сама
    кучка; для колонок с двух сторон — вся кучка, а не её половина)."""
    items = sorted(cluster, key=lambda it: it.anchor[1])
    if box is None:
        box = rect_union(it.elem_rect for it in cluster)
    # марка напротив элемента — полкой выноски (а не центром) на его уровне
    desired = [it.anchor[1] - it.attach_dv for it in items]
    shelf = min(shelf, off)
    elbow_x = box[2] + off - shelf if side == "right" else box[0] - off + shelf

    def slots(order):
        return _spread_1d(desired, [it.size[1] for it in order], gap)

    # Порядок по высоте не гарантирует, что наклонные выноски не
    # пересекутся (элементы стоят не строго в линию). Распутываем:
    # пересекающиеся выноски меняются местами — суммарная длина при этом
    # строго падает, так что цикл конечен. Проходы целиком, без
    # перезапуска после каждой перестановки; места марок пересчитываются,
    # только если марки разной высоты (иначе от порядка они не зависят).
    same_size = len(set(it.size[1] for it in items)) <= 1
    centers = slots(items)
    for _ in range(len(items) * len(items)):
        swapped = False
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                if segments_cross(items[i].anchor, (elbow_x, centers[i] + items[i].attach_dv),
                                  items[j].anchor, (elbow_x, centers[j] + items[j].attach_dv)):
                    items[i], items[j] = items[j], items[i]
                    swapped = True
                    if not same_size:
                        centers = slots(items)
        if not swapped:
            break
    centers = slots(items)

    result = []
    for it, cy in zip(items, centers):
        w, h = it.size
        ly = cy + it.attach_dv
        if side == "right":
            x0 = box[2] + off
            rect = (x0, cy - h * 0.5, x0 + w, cy + h * 0.5)
            attach = (x0, ly)
            elbow = (x0 - shelf, ly)
        else:
            x1 = box[0] - off
            rect = (x1 - w, cy - h * 0.5, x1, cy + h * 0.5)
            attach = (x1, ly)
            elbow = (x1 + shelf, ly)
        result.append(Placement(it.key, rect, elbow, it.anchor, attach))
    return result


def _layout_two_sides(cluster, off, gap, shelf, by_x):
    """
    Колонки марок с ОБЕИХ сторон кучки-«столбика». Когда оборудование
    стоит в столбик плотнее, чем высота марки, одна колонка марок
    получается выше самой кучки и раздвигается вверх и вниз — выноски
    расходятся «веером» с крутыми наклонами (как скаты крыши). Две
    колонки вдвое ниже, марки остаются напротив своих элементов.
    by_x — левая половина элементов (по горизонтали) — влево, правая —
    вправо; иначе — через один по высоте.
    """
    box = rect_union(it.elem_rect for it in cluster)
    if by_x:
        ordered = sorted(cluster, key=lambda it: it.anchor[0])
        half = len(ordered) // 2
        left, right = ordered[:half], ordered[half:]
    else:
        ordered = sorted(cluster, key=lambda it: it.anchor[1])
        right, left = ordered[0::2], ordered[1::2]
    result = []
    if right:
        result.extend(_layout_side(right, "right", off, gap, shelf, box))
    if left:
        result.extend(_layout_side(left, "left", off, gap, shelf, box))
    return result


def _layout_stack(cluster, vdir, hdir, off, gap, shelf, box=None):
    """Стопка марок над/под кучкой, выноски вертикаль + полка. box — как
    в :func:`_layout_side`."""
    if box is None:
        box = rect_union(it.elem_rect for it in cluster)
    # ближайшая к кучке марка — у элемента, ближайшего к колонке марок
    if hdir == "right":
        items = sorted(cluster, key=lambda it: -it.anchor[0])
        col_x = max(it.anchor[0] for it in cluster) + shelf
    else:
        items = sorted(cluster, key=lambda it: it.anchor[0])
        col_x = min(it.anchor[0] for it in cluster) - shelf

    result = []
    if vdir == "up":
        cursor = box[3] + off
    else:
        cursor = box[1] - off

    for it in items:
        w, h = it.size
        if vdir == "up":
            y0, y1 = cursor, cursor + h
            cursor = y1 + gap
        else:
            y0, y1 = cursor - h, cursor
            cursor = y0 - gap
        ly = (y0 + y1) * 0.5 + it.attach_dv
        if hdir == "right":
            rect = (col_x, y0, col_x + w, y1)
        else:
            rect = (col_x - w, y0, col_x, y1)
        attach = (col_x, ly)
        elbow = (it.anchor[0], ly)
        result.append(Placement(it.key, rect, elbow, it.anchor, attach))
    return result


def _layout_split_stack(cluster, vdir, off, gap, shelf):
    """
    Две стопки над (под) длинным рядом: левая половина элементов —
    стопкой влево, правая — вправо. Каждая стопка вдвое ниже одной
    общей, выноски вдвое короче и тоже не пересекаются (вертикали левой
    половины левее вертикалей правой, полки расходятся в разные стороны).
    """
    box = rect_union(it.elem_rect for it in cluster)
    ordered = sorted(cluster, key=lambda it: it.anchor[0])
    half = len(ordered) // 2
    return (_layout_stack(ordered[:half], vdir, "left", off, gap, shelf, box) +
            _layout_stack(ordered[half:], vdir, "right", off, gap, shelf, box))


def _candidates(cluster, off, gap, shelf):
    """(штраф за «неприоритетность», раскладка) — все варианты для кучки."""
    column = _is_column(cluster)
    out = []
    for k in (0.5, 1, 2, 3):
        o = off * k
        # пол-отступа — когда марка влезает в просвет между соседями
        dist_pen = 2.0 if k < 1 else (k - 1) * 3.0
        side_pen = 0.0 if column else 6.0
        stack_pen = 6.0 if column else 0.0
        for side, pen in (("right", 0.0), ("left", 0.5)):
            out.append((side_pen + pen + dist_pen,
                        _layout_side(cluster, side, o, gap, shelf)))
        if len(cluster) >= 2:
            # столбик: при плотной расстановке — основной вариант;
            # ряд/сетка: марки по бокам, выноски вдоль рядов
            if column:
                two_pen = 1.0 if len(cluster) >= 4 else 3.0
            else:
                two_pen = 6.0
            for by_x in (True, False):
                out.append((two_pen + dist_pen,
                            _layout_two_sides(cluster, o, gap, shelf, by_x)))
        for vdir, vpen in (("up", 0.0), ("down", 1.0)):
            for hdir, hpen in (("right", 0.0), ("left", 0.5)):
                out.append((stack_pen + vpen + hpen + dist_pen,
                            _layout_stack(cluster, vdir, hdir, o, gap, shelf)))
            if len(cluster) >= 4:
                out.append((stack_pen + vpen + 0.25 + dist_pen,
                            _layout_split_stack(cluster, vdir, o, gap, shelf)))
    return out


# ------------------------------------------------------------
# ШТРАФ И ВЫБОР
# ------------------------------------------------------------

W_TAG_ON_TAG = 1000.0      # марка на марке — хуже всего
W_TAG_ON_ELEM = 200.0      # марка на оборудовании
W_TAG_ON_OBST = 100.0      # марка на прочем (чужие марки/тексты)
W_LEADER_THRU_TAG = 60.0   # выноска через марку
W_LEADER_CROSS = 40.0      # выноска через выноску (и выноска поверх выноски)
W_LEADER_THRU_ELEM = 20.0  # выноска через другое оборудование
W_LENGTH = 0.5             # за длину выноски (в долях высоты марки)
W_STEEP = 2.0              # за наклонную выноску круче 45° (в долях высоты марки)
W_SLANT = 8.0              # за наклонный (не прямоугольный) участок выноски


def _leaders_clash(a, b, c, d):
    return segments_cross(a, b, c, d) or segments_overlap(a, b, c, d)


def _seg_box(a, b):
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1]))


def _boxes_touch(r, q):
    return r[0] <= q[2] and q[0] <= r[2] and r[1] <= q[3] and q[1] <= r[3]


def _score(placements, elem_rects, obstacles, placed, placed_leaders, unit,
           limit=None):
    """
    Штраф варианта. limit — штраф лучшего уже найденного варианта: как
    только набрано больше, считать дальше незачем (дешёвые слагаемые
    считаются первыми, перебор пар выносок — последним) — на больших
    кучках это главное ускорение.
    """
    segs = []  # (a, b, габарит отрезка, key)
    s = 0.0
    for p in placements:
        for a, b in p.leader_segments():
            if _seg_len(a, b) < 1e-9:
                continue
            segs.append((a, b, _seg_box(a, b), p.key))
            s += W_LENGTH * _seg_len(a, b) / unit
            # наклонный участок круче 45° — марка далеко ушла от своего
            # элемента по высоте; у многих таких выносок — «веер»
            du, dv = abs(b[0] - a[0]), abs(b[1] - a[1])
            if du > 1e-9 and dv > 1e-9:
                # сдвиг в пределах высоты марки — почти прямая, не штрафуем
                slant = min(du, dv) / unit - 1.0
                if slant > 0:
                    s += W_SLANT * min(1.0, slant)
                if dv > du:
                    s += W_STEEP * (dv - du) / unit
    if limit is not None and s > limit:
        return s

    for p in placements:
        tag_area = max(rect_w(p.tag_rect) * rect_h(p.tag_rect), 1e-9)
        for r in placed:
            s += W_TAG_ON_TAG * rect_overlap_area(p.tag_rect, r) / tag_area
        for key, r in elem_rects:
            s += W_TAG_ON_ELEM * rect_overlap_area(p.tag_rect, r) / tag_area
        for r in obstacles:
            s += W_TAG_ON_OBST * rect_overlap_area(p.tag_rect, r) / tag_area
    if limit is not None and s > limit:
        return s

    placed_boxes = [(c, d, _seg_box(c, d)) for c, d in placed_leaders]
    for a, b, box, own_key in segs:
        for r in placed:
            if _boxes_touch(box, r) and segment_hits_rect(a, b, r):
                s += W_LEADER_THRU_TAG
        for c, d, cbox in placed_boxes:
            if _boxes_touch(box, cbox) and _leaders_clash(a, b, c, d):
                s += W_LEADER_CROSS
        for key, r in elem_rects:
            if key != own_key and _boxes_touch(box, r) and segment_hits_rect(a, b, r):
                s += W_LEADER_THRU_ELEM
        if limit is not None and s > limit:
            return s

    # выноски внутри самого варианта: у блока из многих марок (стопка
    # на столбик, колонка на ряд) они могут лечь друг на друга
    for i in range(len(segs)):
        a, b, box, key = segs[i]
        for j in range(i):
            c, d, cbox, key2 = segs[j]
            if key2 != key and _boxes_touch(box, cbox) and _leaders_clash(a, b, c, d):
                s += W_LEADER_CROSS
        if limit is not None and s > limit:
            return s
    return s


def layout(items, obstacles=None, offset=1.0, gap=0.2, shelf=0.5, cluster_dist=1.0,
           start_at_edge=True):
    """
    Раскладывает марки. items — список :class:`TagItem`; obstacles —
    прямоугольники, на которые марки лучше не ставить (прочее
    оборудование на виде, чужие марки, тексты). Все размеры — в тех же
    единицах, что и координаты. start_at_edge — конец выноски не в
    anchor, а там, где первый участок выноски выходит из elem_rect (от
    края УГО, а не из его центра). Возвращает список :class:`Placement`
    в том же порядке, что items.
    """
    obstacles = list(obstacles or [])
    if not items:
        return []

    elem_rects = [(it.key, it.elem_rect) for it in items]
    item_by_key = dict((it.key, it) for it in items)
    unit = max(sum(it.size[1] for it in items) / float(len(items)), 1e-6)

    def order(clusters):
        # большие кучки первыми; при равенстве — слева направо, сверху вниз
        return sorted(clusters, key=lambda c: (-len(c),
                                               min(it.anchor[0] for it in c),
                                               -max(it.anchor[1] for it in c)))

    def place(cluster, rects, leaders):
        """Лучший вариант для кучки при уже поставленных rects/leaders:
        (штраф, раскладка) — раскладка с концами выносок на краю УГО."""
        # проверяем только то, до чего марки кучки вообще могут дотянуться
        # (3× отступ + вся стопка + самая широкая марка) — на виде с сотнями
        # элементов иначе перебор заметно тормозит
        reach = (3 * offset + shelf + max(it.size[0] for it in cluster) +
                 sum(it.size[1] + gap for it in cluster))
        zone = rect_inflate(rect_union(it.elem_rect for it in cluster), reach)
        near_elems = [(k, r) for k, r in elem_rects if rect_gap(r, zone) == 0.0]
        near_obst = [r for r in obstacles if rect_gap(r, zone) == 0.0]
        near_placed = [r for r in rects if rect_gap(r, zone) == 0.0]

        near_leaders = [(a, b) for a, b in leaders
                        if _boxes_touch(_seg_box(a, b), zone)]

        best = None
        for pref_pen, cand in _candidates(cluster, offset, gap, shelf):
            limit = None if best is None else best[0] - pref_pen
            sc = pref_pen + _score(cand, near_elems, near_obst,
                                   near_placed, near_leaders, unit, limit)
            if best is None or sc < best[0]:
                best = (sc, cand)
        if start_at_edge:
            for p in best[1]:
                it = item_by_key[p.key]
                p.end = exit_point(p.end, p.elbow, it.elem_rect)
        return best

    def outer_first(parts):
        # крайние части сетки (верхний/нижний ряд, крайние элементы)
        # первыми: им есть куда вынести марки наружу, а внутренним
        # достаётся то, что осталось
        if len(parts) < 2:
            return parts
        cu, cv = rect_center(rect_union(it.elem_rect for p in parts for it in p))

        def dist(part):
            pu, pv = rect_center(rect_union(it.elem_rect for it in part))
            return (pu - cu) ** 2 + (pv - cv) ** 2

        return sorted(order(parts), key=lambda part: -dist(part))

    def place_parts(parts, rects, leaders, limit=None):
        """Части кучки по очереди; (суммарный штраф, раскладки). Как
        только штраф превысил limit — бросаем (вариант уже хуже)."""
        rects, leaders = list(rects), list(leaders)
        total, result = 0.0, []
        for part in outer_first(parts):
            sc, cand = place(part, rects, leaders)
            total += sc
            if limit is not None and total > limit:
                return total, None
            result.extend(cand)
            for p in cand:
                rects.append(rect_inflate(p.tag_rect, gap * 0.5))
                leaders.extend(p.leader_segments())
        return total, result

    placed_rects = []
    placed_leaders = []
    by_key = {}

    for cluster in order(find_clusters(items, cluster_dist, split=False)):
        # «сетку» можно разбить по-разному (ряды/столбцы/поодиночке) —
        # берём разбиение с наименьшим суммарным штрафом
        best = None
        for parts in _split_options(cluster):
            if not _keeps_groups(parts):
                continue
            sc, cand = place_parts(parts, placed_rects, placed_leaders,
                                   None if best is None else best[0])
            if cand is not None and (best is None or sc < best[0]):
                best = (sc, cand)
        for p in best[1]:
            by_key[p.key] = p
            placed_rects.append(rect_inflate(p.tag_rect, gap * 0.5))
            placed_leaders.extend(p.leader_segments())

    return [by_key[it.key] for it in items]


def count_conflicts(placements, items):
    """Для отчёта: сколько пар марок перекрываются и сколько пар выносок
    пересекается после раскладки (в идеале 0 и 0)."""
    overlaps = 0
    crossings = 0
    for i in range(len(placements)):
        for j in range(i + 1, len(placements)):
            a, b = placements[i], placements[j]
            if rect_overlap_area(a.tag_rect, b.tag_rect) > 1e-9:
                overlaps += 1
            hit = False
            for s1 in a.leader_segments():
                for s2 in b.leader_segments():
                    if _leaders_clash(s1[0], s1[1], s2[0], s2[1]):
                        hit = True
            if hit:
                crossings += 1
    return overlaps, crossings
