# -*- coding: utf-8 -*-
"""View Manager: профиль настроек пользователя.

Профиль хранится в %APPDATA%\\pyRevit\\EOMViewManager_profile.json и
подхватывается при каждом запуске. Копию можно сохранить в любой файл и
загрузить обратно (кнопки в панели). Группы хранят ИМЕНА рабочих наборов,
поэтому один профиль работает во всех проектах.
"""
try:
    unicode
except NameError:
    unicode = str

import copy
import fnmatch
import io
import json
import os

FILE_NAME = 'EOMViewManager_profile.json'
VERSION = 1

OTHER_SECTION = u'Прочие'


def default_sections():
    """Подгруппы списка рабочих наборов по умолчанию (как было раньше — по префиксу)."""
    return [{'name': u'0 — Связи', 'patterns': [u'0_*'], 'hidden': False},
            {'name': u'1 — Второстепенные', 'patterns': [u'1_*'], 'hidden': False},
            {'name': u'2 — ЭОМ', 'patterns': [u'2_*'], 'hidden': False},
            {'name': OTHER_SECTION, 'patterns': [], 'hidden': False}]


def default():
    return {'version': VERSION, 'allow_template': False, 'groups': [],
            # подгруппы списка РБ на панели: {'name', 'patterns': [правила], 'hidden'};
            # подгруппа без правил — «всё остальное»
            'sections': default_sections(),
            'categories': {'model': [], 'annotation': []},
            # как сокращать имена рабочих наборов на плитках (только показ)
            'workset_names': {'rules': []},
            # вкладка «Цвета»: окраска по рабочим наборам (в т.ч. внутри связей).
            # items: {'name', 'scope': 'own'|'link', 'enabled', 'color': [r,g,b], 'transparency'}
            # param — параметр, которым различаются одинаковые РБ у нас и в связях
            'colors': {'param': u'SMNX_Фильтр', 'items': []},
            # вкладка «Шаблоны вида»: папка с файлами шаблонов
            'templates_folder': u''}


def _color(value):
    try:
        r, g, b = [max(0, min(255, int(x))) for x in value]
        return [r, g, b]
    except Exception:
        return [128, 128, 128]


def _color_items(values):
    out, seen = [], set()
    for it in values or []:
        if not isinstance(it, dict) or not it.get('name'):
            continue
        scope = it.get('scope') if it.get('scope') in ('own', 'link') else 'link'
        key = (scope, unicode(it['name']))
        if key in seen:
            continue
        seen.add(key)
        out.append({'name': key[1], 'scope': scope, 'enabled': bool(it.get('enabled', True)),
                    'color': _color(it.get('color')),
                    'transparency': max(0, min(100, int(it.get('transparency') or 0)))})
    return out


def _rules(values):
    out = []
    for r in values or []:
        if isinstance(r, dict) and r.get('find'):
            out.append({'find': unicode(r['find']), 'replace': unicode(r.get('replace') or u'')})
    return out


def path():
    appdata = os.environ.get('APPDATA') or os.path.expanduser('~')
    folder = os.path.join(appdata, 'pyRevit')
    if not os.path.isdir(folder):
        os.makedirs(folder)
    return os.path.join(folder, FILE_NAME)


def _names(values):
    out = []
    for v in values or []:
        if isinstance(v, (str, unicode)) and v and v not in out:
            out.append(unicode(v))
    return out


def normalize(data):
    """Привести загруженный JSON к ожидаемому виду; лишнее отбросить."""
    if not isinstance(data, dict):
        raise ValueError(u'Файл не похож на профиль View Manager.')
    result = default()
    result['allow_template'] = bool(data.get('allow_template', False))
    for g in data.get('groups') or []:
        if isinstance(g, dict) and g.get('name'):
            result['groups'].append({'name': unicode(g['name']),
                                     'worksets': _names(g.get('worksets'))})
    sections = data.get('sections')
    if isinstance(sections, list):
        result['sections'] = []
        for s in sections:
            if isinstance(s, dict) and s.get('name'):
                result['sections'].append({'name': unicode(s['name']),
                                           'patterns': _names(s.get('patterns')),
                                           'hidden': bool(s.get('hidden', False))})
    cats = data.get('categories') or {}
    if isinstance(cats, dict):
        result['categories']['model'] = _names(cats.get('model'))
        result['categories']['annotation'] = _names(cats.get('annotation'))
    naming = data.get('workset_names')
    if isinstance(naming, dict):
        result['workset_names'] = {'rules': _rules(naming.get('rules'))}
    colors = data.get('colors')
    if isinstance(colors, dict):
        result['colors'] = {'param': unicode(colors.get('param') or u'SMNX_Фильтр'),
                            'items': _color_items(colors.get('items'))}
    result['templates_folder'] = unicode(data.get('templates_folder') or u'')
    return result


def short_workset_name(name, naming):
    """Имя рабочего набора для плитки по правилам «Наименование РБ»."""
    text = name
    for rule in naming.get('rules', []):
        text = text.replace(rule['find'], rule['replace'])
    text = u' '.join(text.split())
    return text or name


def _pattern_score(pattern, name):
    """Насколько правило подходит к имени РБ: None — не подходит, иначе «точность»
    (число значащих символов правила). Без * и ? — поиск подстроки, с ними — маска
    на всё имя. Регистр не важен."""
    pat = pattern.strip().lower()
    if not pat:
        return None
    low = name.lower()
    if '*' in pat or '?' in pat:
        ok = fnmatch.fnmatchcase(low, pat)
    else:
        ok = pat in low
    if not ok:
        return None
    return len(pat.replace('*', u'').replace('?', u''))


def section_index(name, sections):
    """Индекс подгруппы для РБ: побеждает самое точное (длинное) правило, при равенстве —
    подгруппа выше по списку. Не подошло ни одно правило — первая подгруппа без правил;
    её нет — None (показывается как «Прочие»)."""
    best, best_score = None, -1
    for i, s in enumerate(sections):
        for pat in s['patterns']:
            score = _pattern_score(pat, name)
            if score is not None and score > best_score:
                best, best_score = i, score
    if best is not None:
        return best
    for i, s in enumerate(sections):
        if not s['patterns']:
            return i
    return None


def split_sections(names, sections):
    """[(индекс подгруппы или None, [имена])] в порядке подгрупп; пустые пропускаются."""
    buckets = {}
    for name in names:
        buckets.setdefault(section_index(name, sections), []).append(name)
    order = [i for i in range(len(sections)) if i in buckets]
    if None in buckets:
        order.append(None)
    return [(i, buckets[i]) for i in order]


def read_file(file_path):
    with io.open(file_path, 'r', encoding='utf-8-sig') as f:
        return normalize(json.load(f))


def write_file(file_path, data):
    text = json.dumps(normalize(copy.deepcopy(data)), ensure_ascii=False, indent=2)
    if not isinstance(text, unicode):
        text = text.decode('utf-8')
    with io.open(file_path, 'w', encoding='utf-8') as f:
        f.write(text)


def load():
    try:
        return read_file(path())
    except Exception:
        return default()


def save(data):
    write_file(path(), data)
