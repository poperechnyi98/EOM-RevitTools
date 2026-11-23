# -*- coding: utf-8 -*-
"""Matching and field preferences independent of Revit and WPF."""

try:
    unicode
except NameError:
    unicode = str


def normalize(value):
    return u' '.join(unicode(value or u'').lower().replace(u'ё', u'е').split())


def select_fields(saved, available, defaults):
    selected = []
    for key in saved:
        if key in available and key not in selected:
            selected.append(key)
        if len(selected) == 2:
            break
    return selected or [key for key in defaults if key in available]


def matches(entries, query):
    """Rank a match in either field by exact value, prefix, then substring."""
    query = normalize(query)
    if not query:
        return []
    found = []
    for entry in entries:
        ranks = [0 if value == query else 1 if value.startswith(query) else 2
                 for value in entry[1] if query in value]
        if ranks:
            found.append((min(ranks), entry))
    found.sort(key=lambda item: item[0])
    return [entry for rank, entry in found]
