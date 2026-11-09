# -*- coding: utf-8 -*-
"""Подготовка общей раскладки новых и существующих марок без Revit API."""


def merge_existing_entries(entries, pairs, id_value):
    """Добавляет существующие марки один раз, сохраняя новые задания.

    Закреплённые марки не добавляются: Revit-часть учтёт их как препятствия.
    Несколько разных марок одного элемента сохраняются отдельно.
    """
    result = list(entries)
    seen = set(id_value(tag.Id) for _el, tag, _type in result if tag is not None)
    for el, tag in pairs:
        key = id_value(tag.Id)
        if key in seen or tag.Pinned:
            continue
        seen.add(key)
        result.append((el, tag, None))
    return result
