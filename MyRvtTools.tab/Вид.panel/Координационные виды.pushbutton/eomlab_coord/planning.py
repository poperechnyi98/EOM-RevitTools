# -*- coding: utf-8 -*-
"""Document-independent matching, colors and independent batch results."""
import fnmatch

from eomlab_coord import text_search


def candidates(options, query, wildcard=False):
    """Options are (name, value) pairs; a blank filter keeps every option."""
    if wildcard:
        pattern = text_search.normalize(query.strip())
        return [item for item in options if not pattern or
                fnmatch.fnmatchcase(text_search.normalize(item[0]), pattern)]
    return text_search.rank(options, query, key=lambda item: item[0])


def automatic(options, host_name, query=u'', wildcard=False):
    """Prefer an exact corresponding name; never choose an ambiguous first hit."""
    found = candidates(options, query, wildcard)
    same = [item for item in found if item[0] == host_name]
    if len(same) == 1:
        return same[0][1]
    return found[0][1] if len(found) == 1 else None


def rgb(value):
    """A missing color preserves existing overrides; RGB channels must be bytes."""
    if value is None:
        return None
    if len(value) != 3 or any(isinstance(x, bool) or not isinstance(x, int)
                              or x < 0 or x > 255 for x in value):
        raise ValueError('Expected three integer color channels in 0..255')
    return tuple(value)


def color_text(value):
    value = rgb(value)
    return '#{:02X}{:02X}{:02X}'.format(*value) if value else u'Без окраски'


def effective_color(row, profiles):
    mode = row.get('color_mode', 'inherit')
    if mode == 'keep':
        return None
    return rgb(row.get('color') if mode == 'rgb' else profiles.get(row['model_key']))


def batch(rows, apply_one):
    """A rejected document cannot roll back previously committed documents."""
    groups = []
    for row in rows:
        group = next((g for g in groups if g[0] == row['doc']), None)
        if group is None:
            group = (row['doc'], [])
            groups.append(group)
        group[1].append(row)
    result = []
    for doc, items in groups:
        try:
            count = apply_one(doc, items)
            result.append(dict(doc=doc, ok=True, count=count, error=None))
        except Exception as ex:
            result.append(dict(doc=doc, ok=False, count=0, error=ex))
    return result
