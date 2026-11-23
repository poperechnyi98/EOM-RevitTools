# -*- coding: utf-8 -*-
"""View Manager-like, safe drag-and-drop editor for link groups."""
import io
import os

import clr
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')

from System.Windows import (Window, Thickness, HorizontalAlignment, VerticalAlignment,
                            FontWeights, DragDrop, DragDropEffects, DataObject,
                            MessageBox, MessageBoxButton, MessageBoxImage,
                            MessageBoxResult, SystemParameters, Visibility)
from System.Windows.Controls import (Border, Button, DockPanel, ListBox, ListBoxItem,
                                     Orientation, ScrollViewer, StackPanel, TextBlock, TextBox)
from System.Windows.Input import Keyboard, MouseButtonState, ModifierKeys, Cursors
from System.Windows.Markup import XamlReader
from System.Windows.Media import Brushes, VisualTreeHelper
from System.Windows.Interop import WindowInteropHelper
from pyrevit import forms

try:
    unicode
except NameError:
    unicode = str

HERE = os.path.dirname(os.path.abspath(__file__))
DRAG_FORMAT = 'EOM.CoordinationViews.Links'
DDE_NONE = getattr(DragDropEffects, 'None')
MOD_NONE = getattr(ModifierKeys, 'None')


def _read_xaml():
    with io.open(os.path.join(HERE, 'groups_dialog.xaml'), 'r', encoding='utf-8') as stream:
        xaml = stream.read()
    with io.open(os.path.join(HERE, 'vm_style.xaml'), 'r', encoding='utf-8') as stream:
        return xaml.replace('__VM_STYLE__', stream.read())


def _text(value, gray=False):
    item = TextBlock()
    item.Text = unicode(value or u'')
    if gray:
        item.Foreground = Brushes.Gray
    return item


class GroupEditor(object):
    def __init__(self, rows, groups, owner=None):
        self.rows = list(rows)
        self.by_key = dict((row['profile_key'], row) for row in self.rows)
        self.assignment = dict(groups or {})
        self.names = []
        for value in self.assignment.values():
            value = unicode(value or u'').strip()
            if value and value != u'Без группы' and value not in self.names:
                self.names.append(value)
        self.names.sort(key=lambda value: value.lower())
        self.selected_group = 0 if self.names else None
        self.drag = None
        self.drag_start = None
        self.drag_item = None
        self.pending_select = None
        self.cards = []
        self.win = XamlReader.Parse(_read_xaml())
        if owner is not None:
            try:
                self.win.Owner = owner
            except Exception:
                try:
                    WindowInteropHelper(self.win).Owner = WindowInteropHelper(owner).Handle
                except Exception:
                    pass
        self.left_count = self.win.FindName('left_count')
        self.search = self.win.FindName('search_box')
        self.only_free = self.win.FindName('only_free_box')
        self.listbox = self.win.FindName('link_list')
        self.cards_panel = self.win.FindName('cards_panel')
        self.footer_hint = self.win.FindName('footer_hint')
        self.search.TextChanged += self._update_left
        self.only_free.Click += self._update_left
        self.listbox.PreviewMouseLeftButtonDown += self._on_list_down
        self.listbox.PreviewMouseLeftButtonUp += self._on_list_up
        self.listbox.PreviewMouseMove += self._on_list_move
        self.listbox.MouseDoubleClick += self._on_list_double
        self.listbox.DragOver += self._on_list_drag_over
        self.listbox.Drop += self._on_list_drop
        self.win.FindName('new_group_button').Click += self._new_group
        self.win.FindName('ok_button').Click += self._accept
        self.win.FindName('cancel_button').Click += lambda sender, args: self.win.Close()
        self.result = None
        self._fill_left()
        self._render()

    def _row_name(self, key):
        row = self.by_key[key]
        return unicode(row.get('name') or row.get('model_name') or key)

    def _group_for(self, key):
        value = unicode(self.assignment.get(key, u'Без группы') or u'Без группы').strip()
        return value or u'Без группы'

    def _fill_left(self):
        self.listbox.Items.Clear()
        for row in sorted(self.rows, key=lambda item: self._row_name(item['profile_key']).lower()):
            item = ListBoxItem()
            item.Tag = row['profile_key']
            item.ToolTip = self._row_name(row['profile_key'])
            item.Content = _text(self._row_name(row['profile_key']))
            self.listbox.Items.Add(item)
        self._update_left()

    def _update_left(self, sender=None, args=None):
        query = unicode(self.search.Text or u'').strip().lower()
        only_free = bool(self.only_free.IsChecked)
        visible = 0
        for item in self.listbox.Items:
            key = getattr(item, 'Tag', None)
            if not key:
                continue
            name = self._row_name(key)
            shown = (not query or query in name.lower()) and (not only_free or self._group_for(key) == u'Без группы')
            item.Visibility = Visibility.Visible if shown else Visibility.Collapsed
            if not shown:
                item.IsSelected = False
            visible += 1 if shown else 0
        self.left_count.Text = u'{} из {}'.format(visible, len(self.rows))

    def _selected_keys(self):
        return [item.Tag for item in self.listbox.SelectedItems if getattr(item, 'Tag', None)]

    def _find_item(self, source):
        item = source
        while item is not None and not isinstance(item, ListBoxItem):
            try:
                item = VisualTreeHelper.GetParent(item)
            except Exception:
                item = getattr(item, 'Parent', None)
        return item

    def _on_list_down(self, sender, args):
        item = self._find_item(args.OriginalSource)
        self.drag_start = args.GetPosition(None) if item is not None else None
        self.drag_item = item
        self.pending_select = None
        if (item is not None and item.IsSelected and self.listbox.SelectedItems.Count > 1
                and Keyboard.Modifiers == MOD_NONE):
            self.pending_select = item
            args.Handled = True

    def _on_list_up(self, sender, args):
        if self.pending_select is not None:
            self.listbox.SelectedItems.Clear()
            self.pending_select.IsSelected = True
        self.pending_select = None
        self.drag_start = None

    def _on_list_move(self, sender, args):
        if self.drag_start is None or args.LeftButton != MouseButtonState.Pressed:
            return
        position = args.GetPosition(None)
        if (abs(position.X - self.drag_start.X) < SystemParameters.MinimumHorizontalDragDistance
                and abs(position.Y - self.drag_start.Y) < SystemParameters.MinimumVerticalDragDistance):
            return
        keys = self._selected_keys()
        if not keys and self.drag_item is not None and self.drag_item.Tag:
            keys = [self.drag_item.Tag]
        self.drag_start = None
        self.pending_select = None
        if keys:
            self._start_drag(self.listbox, keys, None)

    def _on_list_double(self, sender, args):
        keys = self._selected_keys()
        if not keys:
            return
        if self.selected_group is None or self.selected_group >= len(self.names):
            self._create_group(keys)
        else:
            self._assign(keys, self.names[self.selected_group])

    def _start_drag(self, source, keys, origin):
        self.drag = {'keys': list(keys), 'origin': origin}
        try:
            DragDrop.DoDragDrop(source, DataObject(DRAG_FORMAT, u'\n'.join(keys)),
                                DragDropEffects.Copy | DragDropEffects.Move)
        finally:
            self.drag = None

    def _drag_effect(self, target):
        if self.drag is None:
            return DDE_NONE
        if self.drag['origin'] is not None and self.drag['origin'] == target:
            return DDE_NONE
        return DragDropEffects.Move

    def _on_list_drag_over(self, sender, args):
        args.Effects = self._drag_effect(None)
        args.Handled = True

    def _on_list_drop(self, sender, args):
        if self.drag is None:
            return
        for key in self.drag['keys']:
            self.assignment[key] = u'Без группы'
        args.Handled = True
        self._render()

    def _assign(self, keys, name):
        for key in keys or []:
            self.assignment[key] = name
        self._render()

    def _new_group(self, sender, args):
        self._create_group(self._selected_keys())

    def _create_group(self, keys=None):
        value = forms.ask_for_string(default=u'', prompt=u'Введите название новой группы:', title=u'Новая группа')
        value = unicode(value or u'').strip()
        if not value or value == u'Без группы' or value in self.names:
            return
        self.names.append(value)
        self.selected_group = len(self.names) - 1
        self._assign(keys or [], value)

    def _group_keys(self, index):
        name = self.names[index]
        return [key for key in self.by_key if self._group_for(key) == name]

    def _render(self):
        self.cards_panel.Children.Clear()
        self.cards = []
        for index, name in enumerate(self.names):
            card = self._card(index, name)
            self.cards_panel.Children.Add(card)
            self.cards.append(card)
        self.cards_panel.Children.Add(self._new_zone())
        self._update_left()
        self._paint_selection()

    def _card(self, index, name):
        card = Border()
        card.Style = self.win.FindResource('Card')
        card.Margin = Thickness(0, 0, 0, 8)
        card.Tag = index
        card.AllowDrop = True
        card.PreviewMouseLeftButtonDown += lambda sender, args, i=index: self._select_group(i)
        card.DragEnter += self._drag_over(card, index)
        card.DragOver += self._drag_over(card, index)
        card.DragLeave += self._drag_leave(card)
        card.Drop += self._drop(index, card)
        box = StackPanel()
        header = DockPanel()
        tools = StackPanel()
        tools.Orientation = Orientation.Horizontal
        DockPanel.SetDock(tools, Dock.Right)
        delete = Button()
        delete.Content = u'×'
        delete.Width = 24
        delete.Height = 24
        delete.Padding = Thickness(0)
        delete.ToolTip = u'Удалить группу'
        delete.Click += lambda sender, args, i=index: self._delete_group(i)
        tools.Children.Add(delete)
        header.Children.Add(tools)
        title = TextBox()
        title.Text = name
        title.FontWeight = FontWeights.SemiBold
        title.FontSize = 13
        title.Background = Brushes.Transparent
        title.BorderThickness = Thickness(0, 0, 0, 1)
        title.LostFocus += lambda sender, args, i=index: self._rename_group(i, sender.Text)
        header.Children.Add(title)
        count = TextBlock()
        count.Text = u'  ({})'.format(len(self._group_keys(index)))
        count.Foreground = Brushes.Gray
        count.VerticalAlignment = VerticalAlignment.Center
        DockPanel.SetDock(count, Dock.Right)
        header.Children.Add(count)
        box.Children.Add(header)
        chips = StackPanel()
        chips.Orientation = Orientation.Horizontal
        chips.Margin = Thickness(0, 8, 0, 0)
        keys = self._group_keys(index)
        for key in sorted(keys, key=lambda item: self._row_name(item).lower()):
            chips.Children.Add(self._chip(index, key))
        if not keys:
            chips.Children.Add(_text(u'Перетащите сюда связи', gray=True))
        box.Children.Add(chips)
        card.Child = box
        return card

    def _chip(self, index, key):
        chip = Border()
        chip.Background = Brushes.White
        chip.BorderBrush = Brushes.LightGray
        chip.BorderThickness = Thickness(1)
        chip.Padding = Thickness(7, 3, 3, 3)
        chip.Margin = Thickness(0, 0, 5, 5)
        chip.Cursor = Cursors.SizeAll
        row = StackPanel()
        row.Orientation = Orientation.Horizontal
        row.Children.Add(_text(self._row_name(key)))
        remove = Button()
        remove.Content = u'×'
        remove.Width = 18
        remove.Height = 18
        remove.Padding = Thickness(0)
        remove.Margin = Thickness(5, 0, 0, 0)
        remove.ToolTip = u'Убрать из группы'
        remove.Click += lambda sender, args, item=key: self._assign([item], u'Без группы')
        row.Children.Add(remove)
        chip.Child = row
        chip.PreviewMouseLeftButtonDown += self._chip_down
        chip.PreviewMouseMove += lambda sender, args, i=index, item=key: self._chip_move(sender, args, i, item)
        return chip

    def _chip_down(self, sender, args):
        self.drag_start = args.GetPosition(None)

    def _chip_move(self, sender, args, index, key):
        if self.drag_start is None or args.LeftButton != MouseButtonState.Pressed:
            return
        position = args.GetPosition(None)
        if (abs(position.X - self.drag_start.X) < SystemParameters.MinimumHorizontalDragDistance
                and abs(position.Y - self.drag_start.Y) < SystemParameters.MinimumVerticalDragDistance):
            return
        self.drag_start = None
        self._start_drag(sender, [key], index)

    def _new_zone(self):
        zone = Border()
        zone.Background = Brushes.White
        zone.BorderBrush = Brushes.LightGray
        zone.BorderThickness = Thickness(1)
        zone.Padding = Thickness(14)
        zone.Margin = Thickness(0, 0, 0, 8)
        zone.AllowDrop = True
        zone.Child = _text(u'+ Новая группа — перетащите сюда связи', gray=True)
        zone.DragEnter += self._drag_over(zone, None)
        zone.DragOver += self._drag_over(zone, None)
        zone.DragLeave += self._drag_leave(zone)
        zone.Drop += self._drop(None, zone)
        return zone

    def _select_group(self, index):
        self.selected_group = index
        self._paint_selection()

    def _paint_selection(self):
        for index, card in enumerate(self.cards):
            card.BorderBrush = Brushes.Green if index == self.selected_group else Brushes.LightGray

    def _rename_group(self, index, value):
        value = unicode(value or u'').strip()
        if not value or value == u'Без группы' or (value in self.names and self.names.index(value) != index):
            return
        old = self.names[index]
        self.names[index] = value
        for key in self.by_key:
            if self._group_for(key) == old:
                self.assignment[key] = value
        self._render()

    def _delete_group(self, index):
        if index is None or index >= len(self.names):
            return
        name = self.names[index]
        answer = MessageBox.Show(self.win, u'Удалить группу <{}>? Связи останутся без группы.'.format(name),
                                 u'Группы связей', MessageBoxButton.YesNo, MessageBoxImage.Question)
        if answer != MessageBoxResult.Yes:
            return
        for key in self.by_key:
            if self._group_for(key) == name:
                self.assignment[key] = u'Без группы'
        del self.names[index]
        self.selected_group = min(index, len(self.names) - 1) if self.names else None
        self._render()

    def _drag_over(self, element, target):
        def handler(sender, args):
            effect = self._drag_effect(target)
            args.Effects = effect
            if effect != DDE_NONE:
                element.Background = Brushes.LightGreen
            args.Handled = True
        return handler

    def _drag_leave(self, element):
        def handler(sender, args):
            element.Background = Brushes.White
        return handler

    def _drop(self, target, element):
        def handler(sender, args):
            args.Handled = True
            element.Background = Brushes.White
            if self.drag is None:
                return
            keys = list(self.drag['keys'])
            if target is None:
                self._create_group(keys)
            else:
                self._assign(keys, self.names[target])
        return handler

    def _accept(self, sender, args):
        self.result = dict((key, self._group_for(key)) for key in self.by_key)
        self.win.Close()

    def show(self):
        self.win.ShowDialog()
        return self.result


def show(rows, groups, owner=None):
    return GroupEditor(rows, groups, owner).show()
