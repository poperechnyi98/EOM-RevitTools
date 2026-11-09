# -*- coding: utf-8 -*-
"""
Чистая логика кнопки «Найти помещение» (ToolsRooms.panel/FindRoom) без
Revit API — сравнение имён уровней хоста и связей.

Уровни в АР и в своей модели обычно называются одинаково, кроме кода
корпуса: в АР «подземная часть» записана как «ПЧ», а в своей модели — как
«0» или «00». Поэтому имя уровня режется на куски по любым символам, кроме
букв и цифр, и куски-синонимы из BUILDING_ALIASES приводятся к одному
значению; остальное сравнивается без учёта регистра и разделителей.
"""

import re

# Куски имени уровня, которые считаются одним и тем же корпусом.
BUILDING_ALIASES = (u"пч", u"0", u"00")

_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)


def level_key(name):
    """Ключ сравнения имени уровня: «ПЧ_Этаж 01», «0_Этаж 01» и
    «00-этаж 01» дают один и тот же ключ."""
    if not name:
        return u""
    tokens = []
    for token in _TOKEN_RE.findall(name.lower()):
        if token in BUILDING_ALIASES:
            token = BUILDING_ALIASES[0]
        tokens.append(token)
    return u" ".join(tokens)


def same_level(a, b):
    """True, если имена уровней совпадают с учётом синонимов корпуса."""
    if not a or not b:
        return False
    return a == b or level_key(a) == level_key(b)
