# -*- coding: utf-8 -*-
"""
Таблица «что маркировать и какой маркой» для режима «Поставить марки»
кнопки «Марки оборудования» (Tools.panel/TagEquipment).

Строка на каждое семейство среди выбранного оборудования без марки на
виде: категория, семейство, сколько элементов без марки и выпадающий
список типов марок, подходящих категории («— не маркировать —» первым).
Выбор запоминается по имени семейства между запусками
(%APPDATA%\\pyRevit\\EOMEquipmentTags_types.json). Сверху —
«Всем — не маркировать» / «Всем — как в прошлый раз», чтобы быстро
оставить одну-две строки.

Показ — своё окно WPF (а не forms.SelectFromList): нужна таблица с выбором
значения в каждой строке (тот же приём, что skud_door_rooms_form.py).
"""

try:
    unicode  # noqa: F821 — IronPython/Python 2
except NameError:  # Python 3
    unicode = str


import clr
from eomtools.i18n import N_, tr
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')

from System.Windows import (
    Window, WindowStartupLocation, Thickness, FontWeights, HorizontalAlignment,
    VerticalAlignment, TextWrapping, GridLength, GridUnitType
)
from System.Windows.Controls import (
    StackPanel, TextBlock, Button, ComboBox, Orientation, DockPanel, Dock,
    ScrollViewer, ScrollBarVisibility, Grid, ColumnDefinition
)
from System.Windows.Media import Brushes

from eomtools import settings_core

SKIP = N_(u"— не маркировать —")

_STORE = settings_core.JsonStore("EOMEquipmentTags_types.json", u"марок оборудования")

_COLUMNS = [(N_(u"Категория"), 190), (N_(u"Семейство"), None), (N_(u"Без марки"), 80),
            (N_(u"Марка"), 300)]


class TagGroup(object):
    """
    Строка таблицы. category/family — подписи; count — элементов без
    марки; options — [(подпись типа марки, ElementId)], первым — тип «по
    умолчанию» для категории; default — подпись, выбранная изначально
    (None — «не маркировать»).
    """

    def __init__(self, key, category, family, count, options, default=None):
        self.key = key
        self.category = category
        self.family = family
        self.count = count
        self.options = options
        self.default = default


def load_saved():
    """{семейство: подпись типа марки или "" («не маркировать»)}."""
    data = _STORE.read().get("by_family")
    return data if isinstance(data, dict) else {}


def save_choice(groups, chosen):
    """chosen — {key: ElementId или None}; запоминает выбор по семействам."""
    data = load_saved()
    for g in groups:
        type_id = chosen.get(g.key)
        label = u""
        for name, tid in g.options:
            if type_id is not None and tid == type_id:
                label = name
        data[g.family] = label
    _STORE.update({"by_family": data})


def _make_grid():
    grid = Grid()
    for _title, width in _COLUMNS:
        column = ColumnDefinition()
        column.Width = GridLength(1, GridUnitType.Star) if width is None else GridLength(width)
        grid.ColumnDefinitions.Add(column)
    return grid


def _place(grid, element, column):
    Grid.SetColumn(element, column)
    element.Margin = Thickness(2, 2, 6, 2)
    element.VerticalAlignment = VerticalAlignment.Center
    grid.Children.Add(element)


def _button(text, bold=False):
    btn = Button()
    btn.Content = text
    btn.Padding = Thickness(10, 4, 10, 4)
    btn.Margin = Thickness(0, 0, 8, 0)
    if bold:
        btn.FontWeight = FontWeights.Bold
    return btn


def show_groups(groups, skipped_tagged=0):
    """
    Окно выбора. Возвращает None (отмена) или {key: ElementId типа марки}
    только для строк, где марка выбрана.
    """
    result = {"value": None}

    win = Window()
    win.Title = tr(u"Марки оборудования — что маркировать")
    win.Width = 960
    win.Height = 560
    win.WindowStartupLocation = WindowStartupLocation.CenterScreen

    outer = DockPanel()
    outer.LastChildFill = True

    header = StackPanel()
    header.Margin = Thickness(16, 12, 16, 6)
    DockPanel.SetDock(header, Dock.Top)

    title = TextBlock()
    title.Text = tr(u"Выбранное оборудование без марки на этом виде")
    title.FontSize = 16
    title.FontWeight = FontWeights.Bold
    header.Children.Add(title)

    hint = TextBlock()
    hint.Text = (
        tr(u"Для каждого семейства выберите тип марки или «не маркировать». "
        u"Выбор запоминается по семейству. Новые и уже стоящие марки оборудования "
        u"на текущем виде раскладываются совместно. Закреплённые марки не двигаются.")
    )
    if skipped_tagged:
        hint.Text += tr(u" Уже с маркой на этом виде (без создания дублей): {}.").format(skipped_tagged)
    hint.FontSize = 11
    hint.Foreground = Brushes.Gray
    hint.TextWrapping = TextWrapping.Wrap
    hint.Margin = Thickness(0, 2, 0, 8)
    header.Children.Add(hint)

    tools = StackPanel()
    tools.Orientation = Orientation.Horizontal
    tools.Margin = Thickness(0, 0, 0, 8)
    none_btn = _button(tr(u"Всем — не маркировать"))
    reset_btn = _button(tr(u"Всем — как в прошлый раз"))
    tools.Children.Add(none_btn)
    tools.Children.Add(reset_btn)
    header.Children.Add(tools)

    head = _make_grid()
    for index, (column_title, _width) in enumerate(_COLUMNS):
        cell = TextBlock()
        cell.Text = tr(column_title)
        cell.FontWeight = FontWeights.Bold
        _place(head, cell, index)
    header.Children.Add(head)

    body = StackPanel()
    body.Margin = Thickness(16, 0, 16, 8)

    rows = []  # (group, combo, names)

    def default_index(group, names):
        return names.index(group.default) if group.default in names else 0

    for group in groups:
        row = _make_grid()
        for index, text in enumerate([group.category, group.family, unicode(group.count)]):
            cell = TextBlock()
            cell.Text = text or u""
            cell.TextWrapping = TextWrapping.Wrap
            _place(row, cell, index)

        names = [SKIP] + [name for name, _tid in group.options]
        combo = ComboBox()
        for name in names:
            combo.Items.Add(tr(name) if name == SKIP else name)
        combo.SelectedIndex = default_index(group, names)
        if not group.options:
            combo.IsEnabled = False
            combo.ToolTip = tr(u"Для этой категории не загружено ни одной марки")
        _place(row, combo, 3)
        body.Children.Add(row)
        rows.append((group, combo, names))

    def on_none(sender, args):
        for _g, combo, _n in rows:
            combo.SelectedIndex = 0

    def on_reset(sender, args):
        for group, combo, names in rows:
            combo.SelectedIndex = default_index(group, names)

    none_btn.Click += on_none
    reset_btn.Click += on_reset

    buttons = StackPanel()
    buttons.Orientation = Orientation.Horizontal
    buttons.HorizontalAlignment = HorizontalAlignment.Right
    buttons.Margin = Thickness(16, 8, 8, 12)
    DockPanel.SetDock(buttons, Dock.Bottom)

    cancel_btn = _button(tr(u"Отмена"))
    ok_btn = _button(tr(u"Поставить марки"), bold=True)

    def on_ok(sender, args):
        chosen = {}
        for group, combo, _names in rows:
            index = combo.SelectedIndex
            if index > 0 and combo.IsEnabled:
                chosen[group.key] = group.options[index - 1][1]
        result["value"] = chosen
        win.Close()

    def on_cancel(sender, args):
        win.Close()

    ok_btn.Click += on_ok
    cancel_btn.Click += on_cancel
    buttons.Children.Add(cancel_btn)
    buttons.Children.Add(ok_btn)

    scroll = ScrollViewer()
    scroll.VerticalScrollBarVisibility = ScrollBarVisibility.Auto
    scroll.Content = body

    outer.Children.Add(header)
    outer.Children.Add(buttons)
    outer.Children.Add(scroll)

    win.Content = outer
    win.ShowDialog()

    return result["value"]
