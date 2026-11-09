# -*- coding: utf-8 -*-
"""
Контекстный поиск имён параметров в полях окон настроек.

Под полем, куда вводится имя параметра, по мере набора открывается список
подходящих имён параметров модели (поиск по вхождению, без учёта регистра,
«ё» = «е», несколько слов — все должны встретиться, набранное не в той
раскладке — тоже). ↓/↑ — выбор, Enter или
Tab — подставить, Esc — закрыть, ↓ или Ctrl+Пробел в поле — весь список.
Список — только подсказка: в поле по-прежнему можно вписать любое имя.

Поля-списки («через запятую», «по одному на строке», «A=Этаж; B=Секция»)
дополняются по текущему элементу списка под кареткой — см. token_span.

Чистая логика (rank, token_span, replace_token, spec_for_key) — без Revit и
WPF, есть тесты. Сбор имён (collect_parameter_names) и окно-подсказка
(attach) — Revit API/WPF, вызываются только из окон настроек.

Подключение:
    names = param_autocomplete.NameSource(doc)   # одна на окно, имена — лениво
    param_autocomplete.attach_by_keys(boxes, names)   # {ключ: TextBox}
    param_autocomplete.attach(box, names)             # одиночное поле
"""

from eomtools import text_search
from eomtools.i18n import N_, tr
from eomtools.text_search import normalize  # noqa: F401 — реэкспорт, им же сортируются списки

# --- чистая логика ------------------------------------------------------------

# Разбивка по ключу настройки: (разделители элементов списка, символ, после
# которого начинается имя внутри элемента или None). Разделители "" — всё
# поле — одно имя.
SINGLE = (u"", None)
LIST = (u",;\n", None)

# Маска помещения «Имя (Номер)», «Имя, Номер»: имена — между «,», «(», «)».
MASK = (u",()", None)

_SINGLE_SUFFIXES = ("_param", "_param_name")
_LIST_SUFFIXES = ("_params", "_param_names", "param_names_text")


def spec_for_key(key):
    """
    Как дополнять поле с этим ключом настроек: SINGLE для ключей имени
    параметра (*_param, *_param_name), LIST для списков имён (*_params,
    *_param_names, *param_names_text), None — поле не про параметры.
    """
    key = (key or "").lower()
    if key.endswith(_LIST_SUFFIXES):
        return LIST
    if key.endswith(_SINGLE_SUFFIXES):
        return SINGLE
    return None


# Поля, где подсказывается не имя параметра (или параметр — по другому
# правилу): ключ → (что подсказывать, spec, ключ поля, от которого зависит
# список). Что подсказывать:
#   "params"        — имена параметров модели;
#   "views"         — имена видов;
#   "view_prefixes" — начала имён видов («1», «20» из «1 Этаж», «20_План»);
#   "families"      — имена семейств (для фильтров по части имени);
#   "system_types"  — типы электрических цепей Revit;
#   "lines_of"      — строки другого поля (имя категории схемы — из списка
#                     категорий выше в том же окне);
#   "values_of"     — значения в модели параметра, имя которого — в другом
#                     поле («Тип трассы» = …).
EXTRA_RULES = {
    "room_mask": ("params", MASK, None),
    "schematic_view_name": ("views", SINGLE, None),
    "view_name_prefixes": ("view_prefixes", LIST, None),
    "family_filter": ("families", SINGLE, None),
    "wire_line_family_filter": ("families", SINGLE, None),
    "circuit_system_type": ("system_types", SINGLE, None),
    "cabinet_category_name": ("lines_of", SINGLE, "schematic_device_categories_text"),
    "route_param_value": ("values_of", SINGLE, "route_param_name"),
    "route_param_value_riser": ("values_of", SINGLE, "route_param_name"),
    "device_cable_type_value": ("values_of", SINGLE, "cable_param_name"),
    "install_pipe_key": ("values_of", SINGLE, "cable_param_name"),
    "install_pipe_open_key": ("values_of", SINGLE, "cable_param_name"),
    "install_tray_key": ("values_of", SINGLE, "cable_param_name"),
}


def rule_for_key(key):
    """
    (что подсказывать, spec, ключ поля-зависимости) для ключа настроек или
    None — см. EXTRA_RULES; иначе имена параметров по spec_for_key.
    """
    if key in EXTRA_RULES:
        return EXTRA_RULES[key]
    spec = spec_for_key(key)
    if spec is not None:
        return ("params", spec, None)
    return None


def text_lines(text):
    """Непустые строки поля без повторов, по порядку."""
    result = []
    for line in (text or u"").replace(u"\r", u"\n").split(u"\n"):
        line = line.strip()
        if line and line not in result:
            result.append(line)
    return result


def view_name_prefix(name):
    """Начало имени вида до первого пробела/«_»/«-»/«.»: «20_План» → «20»."""
    name = (name or u"").strip()
    for index, ch in enumerate(name):
        if ch in u" _-.":
            return name[:index]
    return name


# Поиск — общий для LowLife (по вхождению, слова в любом порядке, набранное не
# в той раскладке — тоже): см. text_search.


def rank(names, query, limit=300):
    """
    Имена, подходящие под query, лучшие первыми: точное совпадение, начало
    имени, начало слова, вхождение, все слова запроса в любом порядке.
    Запрос, набранный не в той раскладке, тоже ищется (чуть ниже в списке).
    Внутри одной группы — по алфавиту. Пустой запрос — все имена по алфавиту.
    """
    return text_search.rank(names, query, alphabetical=True, limit=limit)


def token_span(text, caret, spec=SINGLE):
    """
    Границы имени, которое сейчас редактируется: (начало, конец) в text или
    None, если каретка не на имени (например, на букве до «=» в «A=Этаж»).
    Пробелы по краям имени в него не входят.
    """
    text = text or u""
    separators, after = spec
    caret = max(0, min(caret, len(text)))

    start = 0
    for index in range(caret - 1, -1, -1):
        if text[index] in separators:
            start = index + 1
            break
    end = len(text)
    for index in range(caret, len(text)):
        if text[index] in separators:
            end = index
            break

    if after:
        mark = text.find(after, start, end)
        if mark < 0 or mark >= caret:
            return None
        start = mark + 1

    while start < end and text[start] in u" \t\r":
        start += 1
    while end > start and text[end - 1] in u" \t\r":
        end -= 1
    return start, end


def current_token(text, caret, spec=SINGLE):
    span = token_span(text, caret, spec)
    if span is None:
        return None
    return (text or u"")[span[0]:span[1]]


def replace_token(text, caret, name, spec=SINGLE):
    """
    Подставляет name вместо имени под кареткой. Возвращает (новый текст,
    новая позиция каретки — сразу после имени) или None, если подставлять
    некуда.
    """
    text = text or u""
    span = token_span(text, caret, spec)
    if span is None:
        return None
    start, end = span
    if start > 0 and text[start - 1] in u",;":
        # «A,|» → «A, Имя»: после запятой/«;» — пробел, как пишут руками
        name = u" " + name
    return text[:start] + name + text[end:], start + len(name)


# --- имена параметров модели (Revit API) -------------------------------------

def _definition_name(param):
    try:
        definition = param.Definition
        return definition.Name if definition is not None else None
    except Exception:
        return None


class _Collector(object):
    """Собирает имена, пока не кончится отведённое время."""

    def __init__(self, budget_ms):
        from System.Diagnostics import Stopwatch
        self.watch = Stopwatch.StartNew()
        self.budget_ms = budget_ms
        self.names = set()
        self.seen_param_ids = set()

    def out_of_time(self):
        return self.watch.ElapsedMilliseconds > self.budget_ms

    def add(self, name):
        if name:
            name = name.strip()
            if name:
                self.names.add(name)

    def add_parameters(self, element):
        try:
            params = element.Parameters
        except Exception:
            return
        for param in params:
            try:
                pid = param.Id.IntegerValue
            except Exception:
                pid = None
            if pid is not None:
                if pid in self.seen_param_ids:
                    continue
                self.seen_param_ids.add(pid)
            self.add(_definition_name(param))

    def collect(self, doc):
        from Autodesk.Revit.DB import FilteredElementCollector, ParameterElement

        # параметры проекта, общие (в том числе пришедшие с семействами), глобальные
        try:
            for element in FilteredElementCollector(doc).OfClass(ParameterElement):
                try:
                    self.add(element.GetDefinition().Name)
                except Exception:
                    pass
        except Exception:
            pass
        try:
            iterator = doc.ParameterBindings.ForwardIterator()
            while iterator.MoveNext():
                try:
                    self.add(iterator.Key.Name)
                except Exception:
                    pass
        except Exception:
            pass

        # параметры семейств и штатные — по одному экземпляру на категорию+тип
        seen_types = set()
        try:
            instances = FilteredElementCollector(doc).WhereElementIsNotElementType()
            for element in instances:
                if self.out_of_time():
                    return
                try:
                    category = element.Category
                    if category is None:
                        continue
                    type_key = (category.Id.IntegerValue, element.GetTypeId().IntegerValue)
                except Exception:
                    continue
                if type_key in seen_types:
                    continue
                seen_types.add(type_key)
                self.add_parameters(element)
                type_id = element.GetTypeId()
                if type_id is not None and type_id.IntegerValue > 0:
                    element_type = doc.GetElement(type_id)
                    if element_type is not None:
                        self.add_parameters(element_type)
        except Exception:
            pass

    def linked_docs(self, doc):
        from Autodesk.Revit.DB import FilteredElementCollector, RevitLinkInstance
        result = []
        seen = set()
        try:
            for link in FilteredElementCollector(doc).OfClass(RevitLinkInstance):
                try:
                    link_doc = link.GetLinkDocument()
                except Exception:
                    link_doc = None
                if link_doc is None:
                    continue
                key = link_doc.PathName or link_doc.Title
                if key in seen:
                    continue
                seen.add(key)
                result.append(link_doc)
        except Exception:
            pass
        return result


def collect_parameter_names(doc, budget_ms=4000, include_links=True):
    """
    Имена параметров модели (и загруженных связей — там часто параметры
    помещений), отсортированные. Параметры проекта и общие — все; параметры
    семейств и штатные — с одного экземпляра (и его типа) каждой пары
    категория+тип, пока не истечёт budget_ms (на больших моделях список
    может быть неполным, но окно не подвиснет).
    """
    if doc is None:
        return []
    collector = _Collector(budget_ms)
    collector.collect(doc)
    if include_links:
        for link_doc in collector.linked_docs(doc):
            if collector.out_of_time():
                break
            collector.seen_param_ids = set()
            collector.collect(link_doc)
    return sorted(collector.names, key=normalize)


def _budget_watch():
    from System.Diagnostics import Stopwatch
    return Stopwatch.StartNew()


def _element_name(element):
    try:
        from Autodesk.Revit.DB import Element
        return Element.Name.GetValue(element)
    except Exception:
        return None


def collect_family_names(doc):
    """Имена загруженных семейств."""
    from Autodesk.Revit.DB import FilteredElementCollector, Family
    names = set()
    for family in FilteredElementCollector(doc).OfClass(Family):
        name = _element_name(family)
        if name:
            names.add(name)
    return sorted(names, key=normalize)


def collect_view_names(doc):
    """Имена видов (без шаблонов видов)."""
    from Autodesk.Revit.DB import FilteredElementCollector, View
    names = set()
    for view in FilteredElementCollector(doc).OfClass(View):
        try:
            if view.IsTemplate:
                continue
        except Exception:
            pass
        name = _element_name(view)
        if name:
            names.add(name)
    return sorted(names, key=normalize)


def collect_view_name_prefixes(doc):
    """Начала имён видов (view_name_prefix), без повторов."""
    prefixes = set(view_name_prefix(n) for n in collect_view_names(doc))
    prefixes.discard(u"")
    return sorted(prefixes, key=normalize)


def collect_system_type_names(doc=None):
    """Типы электрических цепей Revit (Data, Security, FireAlarm…)."""
    from Autodesk.Revit.DB.Electrical import ElectricalSystemType
    return sorted(
        a for a in dir(ElectricalSystemType)
        if not a.startswith("_")
        and isinstance(getattr(ElectricalSystemType, a, None), ElectricalSystemType)
    )


def _param_text(param):
    try:
        text = param.AsString()
        if not text:
            text = param.AsValueString()
        return (text or u"").strip()
    except Exception:
        return u""


def collect_param_values(doc, param_name, budget_ms=3000):
    """
    Разные непустые значения параметра param_name у элементов модели (у
    экземпляра, а если у него такого нет — у его типа), пока не истечёт
    budget_ms.
    """
    from Autodesk.Revit.DB import FilteredElementCollector
    param_name = (param_name or u"").strip()
    if doc is None or not param_name:
        return []
    watch = _budget_watch()
    values = set()
    seen_types = set()
    for element in FilteredElementCollector(doc).WhereElementIsNotElementType():
        if watch.ElapsedMilliseconds > budget_ms:
            break
        try:
            param = element.LookupParameter(param_name)
        except Exception:
            param = None
        if param is None:
            try:
                type_id = element.GetTypeId()
                if type_id is None or type_id.IntegerValue <= 0 \
                        or type_id.IntegerValue in seen_types:
                    continue
                seen_types.add(type_id.IntegerValue)
                element_type = doc.GetElement(type_id)
                param = element_type.LookupParameter(param_name) if element_type else None
            except Exception:
                param = None
        if param is not None:
            text = _param_text(param)
            if text:
                values.add(text)
    return sorted(values, key=normalize)


def active_doc():
    """Активный документ Revit (для окон, которым doc не передают)."""
    try:
        from pyrevit import revit
        return revit.doc
    except Exception:
        return None


def _with_wait_cursor(work):
    cursor_set = False
    try:
        from System.Windows.Input import Mouse, Cursors
        Mouse.OverrideCursor = Cursors.Wait
        cursor_set = True
    except Exception:
        pass
    try:
        return work()
    except Exception:
        return []
    finally:
        if cursor_set:
            Mouse.OverrideCursor = None


HINT = N_(u"Начните вводить — появятся подходящие параметры модели "
        u"(↓ или Ctrl+Пробел — весь список, Enter — подставить).")

_HINTS = {
    "views": N_(u"виды модели"),
    "view_prefixes": N_(u"начала имён видов модели"),
    "families": N_(u"семейства модели"),
    "system_types": N_(u"типы электрических цепей Revit"),
    "lines_of": N_(u"значения из списка выше"),
    "values_of": N_(u"значения этого параметра в модели"),
}


def _hint(what):
    return tr(u"Начните вводить — появятся подходящие {} (↓ или Ctrl+Пробел — "
              u"весь список, Enter — подставить).").format(tr(what))


class NameSource(object):
    """
    Имена для подсказки в одном окне: собираются один раз, при первой
    подсказке (не задерживают открытие окна). doc=None — активный документ.
    collect(doc) → список имён; по умолчанию — имена параметров.
    """

    def __init__(self, doc=None, names=None, collect=None, hint=HINT):
        self.doc = doc
        self._names = list(names) if names is not None else None
        self.collect = collect or collect_parameter_names
        self.hint = hint

    def get_doc(self):
        return self.doc if self.doc is not None else active_doc()

    def names(self):
        if self._names is None:
            doc = self.get_doc()
            self._names = _with_wait_cursor(lambda: self.collect(doc)) or []
        return self._names


class DependentSource(object):
    """
    Имена, зависящие от текста другого поля (get_text()): значения
    параметра, чьё имя введено рядом, или строки списка выше. collect(doc,
    text) → список; запоминается для каждого text.
    """

    def __init__(self, get_text, collect, doc=None, hint=HINT, wait=True):
        self.get_text = get_text
        self.collect = collect
        self.doc = doc
        self.hint = hint
        self.wait = wait
        self._cache = {}

    def names(self):
        try:
            text = self.get_text() or u""
        except Exception:
            text = u""
        if text not in self._cache:
            doc = self.doc if self.doc is not None else active_doc()
            work = lambda: self.collect(doc, text)
            self._cache[text] = (_with_wait_cursor(work) if self.wait else work()) or []
        return self._cache[text]


def source_for_rule(rule, params_source, boxes=None, values=None):
    """
    Источник подсказки для правила rule_for_key: имена параметров —
    общий params_source окна, остальное — новый источник на том же doc.
    Поле-зависимость берётся из boxes, а если его нет в окне — из values
    (сохранённые настройки).
    """
    kind, _, dep_key = rule
    if kind == "params":
        return params_source
    doc = getattr(params_source, "doc", None)
    hint = _hint(_HINTS.get(kind, N_(u"значения")))
    if kind == "views":
        return NameSource(doc, collect=collect_view_names, hint=hint)
    if kind == "view_prefixes":
        return NameSource(doc, collect=collect_view_name_prefixes, hint=hint)
    if kind == "families":
        return NameSource(doc, collect=collect_family_names, hint=hint)
    if kind == "system_types":
        return NameSource(doc, collect=collect_system_type_names, hint=hint)

    boxes = boxes or {}
    values = values or {}

    def get_text():
        if dep_key in boxes:
            return boxes[dep_key].Text
        return values.get(dep_key) or u""

    if kind == "lines_of":
        return DependentSource(get_text, lambda doc, text: text_lines(text),
                               doc, hint, wait=False)
    if kind == "values_of":
        return DependentSource(lambda: get_text().strip(), collect_param_values, doc, hint)
    return None


# --- окно-подсказка под полем (WPF) ------------------------------------------

def attach_by_keys(boxes, source, overrides=None, values=None):
    """
    Подключает подсказки ко всем полям {ключ: TextBox}, для которых есть
    правило (rule_for_key): имена параметров — из source (общий
    NameSource окна), виды/семейства/значения и т. п. — из своих
    источников. overrides — {ключ: spec имён параметров или None}, если
    правило по имени ключа не подходит. values — сохранённые настройки:
    откуда брать поле-зависимость, если его нет в окне.
    """
    overrides = overrides or {}
    for key, box in boxes.items():
        if key in overrides:
            if overrides[key] is not None:
                attach(box, source, overrides[key])
            continue
        rule = rule_for_key(key)
        if rule is None:
            continue
        rule_source = source_for_rule(rule, source, boxes, values)
        if rule_source is not None:
            attach(box, rule_source, rule[1])


def attach(box, source, spec=SINGLE):
    """
    Подключает подсказку к TextBox; source — NameSource/DependentSource
    (что угодно с .names()). Возвращает box.
    """
    try:
        _Completion(box, source, spec)
    except Exception:
        # подсказка — удобство; окно настроек без неё должно работать
        pass
    return box


class _Completion(object):

    MAX_HEIGHT = 260

    def __init__(self, box, source, spec):
        import clr
        clr.AddReference('PresentationFramework')
        clr.AddReference('PresentationCore')
        from System.Windows import Thickness, Setter, Style, UIElement
        from System.Windows.Controls import (
            Border, Control, ListBox, ListBoxItem, ScrollViewer, ScrollBarVisibility
        )
        from System.Windows.Controls.Primitives import Popup, PlacementMode
        from System.Windows.Media import Brushes

        self.box = box
        self.source = source
        self.spec = spec
        self.updating = False
        self.window_hooked = False

        self.list = ListBox()
        # Ничто в подсказке не забирает фокус у поля — иначе поле теряет
        # фокус и подсказка закрывается посреди клика.
        self.list.Focusable = False
        item_style = Style(clr.GetClrType(ListBoxItem))
        item_style.Setters.Add(Setter(UIElement.FocusableProperty, False))
        item_style.Setters.Add(Setter(Control.PaddingProperty, Thickness(4, 1, 4, 1)))
        self.list.ItemContainerStyle = item_style
        self.list.MaxHeight = self.MAX_HEIGHT
        self.list.BorderThickness = Thickness(0)
        ScrollViewer.SetHorizontalScrollBarVisibility(self.list, ScrollBarVisibility.Disabled)
        self.list.PreviewMouseLeftButtonDown += self.on_list_click

        border = Border()
        border.BorderBrush = Brushes.Gray
        border.BorderThickness = Thickness(1)
        border.Background = Brushes.White
        border.Child = self.list

        self.popup = Popup()
        self.popup.PlacementTarget = box
        self.popup.Placement = PlacementMode.Bottom
        self.popup.StaysOpen = True
        self.popup.Child = border

        box.TextChanged += self.on_text_changed
        box.PreviewKeyDown += self.on_key
        box.LostKeyboardFocus += self.on_lost_focus
        if box.ToolTip is None:
            box.ToolTip = tr(getattr(source, "hint", None) or HINT)

    # --- показ ---

    def close(self, *args):
        if self.popup.IsOpen:
            self.popup.IsOpen = False

    def hook_window(self):
        if self.window_hooked:
            return
        self.window_hooked = True
        try:
            from System.Windows import Window
            window = Window.GetWindow(self.box)
            if window is not None:
                # поле уезжает при прокрутке/перемещении окна — подсказку закрываем
                window.LocationChanged += self.close
                window.Deactivated += self.close
                window.PreviewMouseWheel += self.close
                window.SizeChanged += self.close
        except Exception:
            pass

    def refresh(self, show_all=False):
        token = current_token(self.box.Text, self.box.CaretIndex, self.spec)
        if token is None:
            self.close()
            return
        query = u"" if show_all else token
        if not query and not show_all:
            self.close()
            return
        matches = rank(self.source.names(), query)
        if not matches or (not show_all and len(matches) == 1 and matches[0] == token):
            self.close()
            return
        self.list.Items.Clear()
        for name in matches:
            self.list.Items.Add(name)
        self.list.SelectedIndex = 0
        if token in matches:
            self.list.SelectedIndex = matches.index(token)
        self.list.ScrollIntoView(self.list.SelectedItem)
        self.popup.MinWidth = max(self.box.ActualWidth, 200)
        self.hook_window()
        self.popup.IsOpen = True

    def choose(self, name):
        replaced = replace_token(self.box.Text, self.box.CaretIndex, name, self.spec)
        self.close()
        if replaced is None:
            return
        text, caret = replaced
        self.updating = True
        try:
            self.box.Text = text
            self.box.CaretIndex = caret
        finally:
            self.updating = False

    def move(self, delta):
        count = self.list.Items.Count
        if not count:
            return
        index = self.list.SelectedIndex + delta
        index = max(0, min(count - 1, index))
        self.list.SelectedIndex = index
        self.list.ScrollIntoView(self.list.SelectedItem)

    # --- события ---

    def on_text_changed(self, sender, args):
        if self.updating or not self.box.IsKeyboardFocusWithin:
            return
        self.refresh()

    def on_lost_focus(self, sender, args):
        self.close()

    def on_key(self, sender, args):
        from System.Windows.Input import Key, Keyboard, ModifierKeys

        key = args.Key
        is_open = self.popup.IsOpen

        if key == Key.Space and Keyboard.Modifiers == ModifierKeys.Control:
            self.refresh(show_all=True)
            args.Handled = True
        elif key == Key.Down:
            if is_open:
                self.move(1)
                args.Handled = True
            elif not self.box.AcceptsReturn or Keyboard.Modifiers == ModifierKeys.Alt:
                # в многострочном поле ↓ — переход по строкам; список — Alt+↓
                self.refresh(show_all=True)
                args.Handled = True
        elif key == Key.Up and is_open:
            self.move(-1)
            args.Handled = True
        elif key == Key.PageDown and is_open:
            self.move(10)
            args.Handled = True
        elif key == Key.PageUp and is_open:
            self.move(-10)
            args.Handled = True
        elif key == Key.Escape and is_open:
            self.close()
            args.Handled = True
        elif key in (Key.Enter, Key.Return) and is_open:
            if self.list.SelectedItem is not None:
                self.choose(self.list.SelectedItem)
                args.Handled = True
            else:
                self.close()
        elif key == Key.Tab and is_open:
            if self.list.SelectedItem is not None:
                self.choose(self.list.SelectedItem)
            self.close()

    def on_list_click(self, sender, args):
        from System.Windows.Controls import ListBoxItem
        from System.Windows.Media import VisualTreeHelper, Visual

        node = args.OriginalSource
        while node is not None and not isinstance(node, ListBoxItem):
            if not isinstance(node, Visual):
                node = getattr(node, "Parent", None)
                continue
            node = VisualTreeHelper.GetParent(node)
        if node is not None:
            self.choose(node.Content)
            args.Handled = True
