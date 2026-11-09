# -*- coding: utf-8 -*-
"""Язык интерфейса LowLife: русский (основной) или английский.

Выбирается списком «Язык» на панели LowLife и хранится в
``%APPDATA%\\pyRevit\\EOMLanguage_settings.json``. Без Revit API и clr —
модуль импортируется и в программе «Задачи из почты».

Лента переводится целиком (``ribbon_language``: английские ``title_en``/``tooltip_en``
из ``bundle.yaml`` каждой кнопки и панели). Текст окон и сообщений пишется через
``tr(u"по-русски", u"in English")`` — русский остаётся основным, английский
подставляется, если выбран English.
"""
import os

RU = "ru"
EN = "en"
LANGUAGES = (RU, EN)
DEFAULT = RU

# Подписи участников списка «Язык» — одинаковые при любом языке, чтобы свой язык было видно всегда.
LABELS = {RU: u"Русский", EN: u"English"}

_STORE = None
_CACHE = {"mtime": None, "lang": None}


def _store():
    global _STORE
    if _STORE is None:
        from eomtools import settings_core
        _STORE = settings_core.JsonStore("EOMLanguage_settings.json", u"языка интерфейса",
                                         share=False)
    return _STORE


def _mtime(path):
    try:
        return os.path.getmtime(path)
    except (IOError, OSError):
        return None


def current():
    """Выбранный язык (``"ru"``/``"en"``). Файл перечитывается, только когда он изменился:
    у движка каждой кнопки своя копия модуля, а язык меняет кнопка-список."""
    try:
        path = _store().path()
    except Exception:
        return DEFAULT
    mtime = _mtime(path)
    if _CACHE["lang"] is None or mtime != _CACHE["mtime"]:
        lang = None
        try:
            lang = _store().read().get("language")
        except Exception:
            lang = None
        _CACHE["lang"] = lang if lang in LANGUAGES else DEFAULT
        _CACHE["mtime"] = mtime
    return _CACHE["lang"]


def save(lang):
    """Запомнить язык; неизвестный код игнорируется. Возвращает сохранённый язык или None."""
    if lang not in LANGUAGES:
        return None
    _store().update({"language": lang})
    _CACHE["lang"] = None  # перечитать при следующем current()
    return lang


def is_en():
    return current() == EN


CATALOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "locale", "en.json")
_CATALOG = {}


def catalog():
    """{русская строка: английская} из ``locale/en.json`` (читается один раз за движок)."""
    if "en" not in _CATALOG:
        data = {}
        try:
            import io
            import json
            with io.open(CATALOG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
        _CATALOG["en"] = data
    return _CATALOG["en"]


# Номера разделов окон настроек («① …»): у tr() перевод подписи после номера, если вся строка
# (номер + подпись, собранные в коде) в каталог не попала
NUMBER_MARKS = u"①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳⓪"


def tr(ru, en=None):
    """Строка на выбранном языке. Русский — как есть; при English — ``en``, если передан, иначе
    перевод из каталога ``locale/en.json`` (нет перевода — русская строка).

    Оборачивайте строку до ``.format``: ``tr(u"Найдено цепей: {}").format(n)`` — ключ каталога
    остаётся постоянным. ``tools/i18n_tool.py extract`` собирает все ``tr(u"…")`` и ``N_(u"…")``
    в каталог, ``tests/test_i18n.py`` проверяет, что у каждой есть перевод."""
    if not ru or current() != EN:
        return ru
    if en:
        return en
    found = catalog().get(ru)
    if found:
        return found
    if len(ru) > 2 and ru[0] in NUMBER_MARKS and ru[1] == u" ":
        # «② Раздел» собран в коде из номера и подписи — переводится подпись
        rest = catalog().get(ru[2:])
        if rest:
            return ru[:2] + rest
    return ru


def N_(text):
    """Пометка «строку перевести при показе» для строк в данных (подписи полей настроек и т. п.):
    возвращает текст как есть, перевод — ``tr(...)`` там, где он показывается."""
    return text


def pick(values, lang=None):
    """Значение из словаря ``{"ru": ..., "en": ...}`` на выбранном языке (иначе — русское)."""
    lang = lang or current()
    return values.get(lang) or values.get(RU) or u""
