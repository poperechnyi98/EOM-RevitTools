# -*- coding: utf-8 -*-
"""
Общий поиск по тексту для всех полей «Поиск» LowLife.

Совпадение — по части текста (вхождение, не только начало), без учёта
регистра, «ё» = «е»; несколько слов запроса — все должны встретиться, в любом
порядке. Запрос, набранный не в той раскладке («wtgb» вместо «цепи», «вцп»
вместо «dwg»), тоже находит — он пробуется в обеих раскладках.

Чистая логика, без Revit и WPF, есть тесты (tests/test_text_search.py).
Пользуются: ribbon_catalog (кнопка «Поиск»), param_autocomplete (подсказки
имён параметров), select_list (списки выбора pyRevit), окна со своим полем
поиска (сводка заметок, помещения для дверей СКУД).
"""

# Одни и те же клавиши в раскладках QWERTY и ЙЦУКЕН.
EN_KEYS = u"`qwertyuiop[]asdfghjkl;'zxcvbnm,./"
RU_KEYS = u"ёйцукенгшщзхъфывапролджэячсмитьбю."
EN_TO_RU = dict(zip(EN_KEYS, RU_KEYS))
RU_TO_EN = dict(zip(RU_KEYS, EN_KEYS))

# Символы, после которых в имени начинается новое слово.
WORD_STARTS = u" _-.([«\"/"


def normalize(text):
    """Для сравнения: нижний регистр, «ё» → «е»."""
    return (text or u"").lower().replace(u"ё", u"е")


def switch_layout(text, table=EN_TO_RU):
    """Текст (уже normalize) так, как те же клавиши дали бы в другой раскладке."""
    return u"".join(table.get(ch, ch) for ch in text)


def query_variants(query):
    """Запрос (normalize, лишние пробелы убраны), затем он же в другой раскладке —
    без повторов. Пустой запрос — пустой список."""
    query = u" ".join(normalize(query).split())
    if not query:
        return []
    variants = [query]
    for table in (EN_TO_RU, RU_TO_EN):
        other = normalize(switch_layout(query, table))
        if other not in variants:
            variants.append(other)
    return variants


def _score(text_norm, variant, words):
    if text_norm == variant:
        return 0
    if text_norm.startswith(variant):
        return 1
    index = text_norm.find(variant)
    if index > 0 and text_norm[index - 1] in WORD_STARTS:
        return 2
    if index > 0:
        return 3
    if len(words) > 1 and all(w in text_norm for w in words):
        return 4
    return None


def score(text, query, variants=None):
    """
    Насколько text подходит под query (меньше — лучше) или None, если не подходит:
    точное совпадение, начало, начало слова, вхождение, все слова в любом порядке.
    Совпадение в другой раскладке — сразу после того же вида совпадения в набранной.
    Пустой запрос — 0 (подходит всё). variants — готовый query_variants(query),
    чтобы не считать его заново для каждого текста.
    """
    if variants is None:
        variants = query_variants(query)
    if not variants:
        return 0
    text_norm = normalize(text)
    best = None
    for index, variant in enumerate(variants):
        s = _score(text_norm, variant, variant.split())
        if s is not None:
            s = s * 2 + (1 if index else 0)
            if best is None or s < best:
                best = s
    return best


def matches(text, query):
    """Подходит ли text под query (пустой запрос — подходит всё)."""
    return score(text, query) is not None


def rank(items, query, key=None, alphabetical=False, limit=None):
    """
    Элементы items, подходящие под query, лучшие первыми. key(item) — текст, по
    которому искать (по умолчанию сам элемент). Внутри одной группы — исходный
    порядок, или по алфавиту при alphabetical=True. Пустой запрос — все элементы
    (по алфавиту при alphabetical=True).
    """
    key = key or (lambda item: item)
    variants = query_variants(query)
    scored = []
    for index, item in enumerate(items):
        text = key(item)
        s = score(text, query, variants)
        if s is not None:
            scored.append((s, normalize(text) if alphabetical else u"", index, item))
    scored.sort(key=lambda x: x[:3])
    result = [x[3] for x in scored]
    return result[:limit] if limit else result
