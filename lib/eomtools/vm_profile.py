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
import io
import json
import os

FILE_NAME = 'EOMViewManager_profile.json'
VERSION = 1


def default():
    return {'version': VERSION, 'allow_template': False, 'groups': [],
            'categories': {'model': [], 'annotation': []},
            # как сокращать имена рабочих наборов на плитках (только показ)
            'workset_names': {'strip_prefix': True, 'rules': []}}


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
    cats = data.get('categories') or {}
    if isinstance(cats, dict):
        result['categories']['model'] = _names(cats.get('model'))
        result['categories']['annotation'] = _names(cats.get('annotation'))
    naming = data.get('workset_names')
    if isinstance(naming, dict):
        result['workset_names'] = {'strip_prefix': bool(naming.get('strip_prefix', True)),
                                   'rules': _rules(naming.get('rules'))}
    return result


def short_workset_name(name, naming):
    """Имя рабочего набора для плитки по правилам «Наименование РБ»."""
    import re
    text = name
    if naming.get('strip_prefix'):
        text = re.sub(r'^\d+_', u'', text)
    for rule in naming.get('rules', []):
        text = text.replace(rule['find'], rule['replace'])
    text = u' '.join(text.split())
    return text or name


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
