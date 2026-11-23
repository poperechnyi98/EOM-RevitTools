# -*- coding: utf-8 -*-
"""View Manager style dialog for linked coordination views."""
import io
import json
import os
import hashlib

import clr
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')
clr.AddReference('System.Windows.Forms')
clr.AddReference('System.Drawing')

from System.Windows import Window, WindowStartupLocation, Thickness, HorizontalAlignment, FontWeights, GridLength
from System.Windows.Controls import (Border, Button, CheckBox, Expander, Grid, ColumnDefinition,
                                      Orientation, StackPanel, TabItem, TextBlock)
from System.Windows.Markup import XamlReader
from System.Windows.Interop import WindowInteropHelper
from System.Windows.Media import Brushes, Color, SolidColorBrush
from System.Windows.Forms import ColorDialog, DialogResult
from System.Drawing import Color as DrawingColor
from Microsoft.Win32 import OpenFileDialog, SaveFileDialog
from pyrevit import forms

from eomlab_coord import backend, link_views, text_search

try:
    unicode
except NameError:
    unicode = str

HERE = os.path.dirname(os.path.abspath(__file__))
CANCEL = object()


def _id_value(value):
    raw = getattr(value, 'Value', None)
    return value.IntegerValue if raw is None else raw


def _read(name):
    with io.open(os.path.join(HERE, name), 'r', encoding='utf-8') as stream:
        return stream.read()


def _root():
    xaml = _read('coordination_views.xaml')
    return XamlReader.Parse(xaml.replace('__VM_STYLE__', _read('vm_style.xaml')))


def _set_text(control, value):
    control.Text = unicode(value or u'')


def _button_color(button, rgb):
    if rgb is None:
        button.Content = u'Без цвета'
        button.Background = Brushes.LightGray
        button.Foreground = Brushes.Black
        return
    button.Content = u'#{:02X}{:02X}{:02X}'.format(*rgb)
    button.Background = SolidColorBrush(Color.FromRgb(*rgb))
    button.Foreground = Brushes.White if sum(rgb) < 380 else Brushes.Black


def _swatch(button, rgb):
    button.Content = u''
    button.Width = 20
    button.Height = 20
    button.ToolTip = u'Без цвета' if rgb is None else u'#{:02X}{:02X}{:02X}'.format(*rgb)
    button.Background = Brushes.LightGray if rgb is None else SolidColorBrush(Color.FromRgb(*rgb))
    button.BorderBrush = Brushes.Gray


def _choose_color(current):
    picker = ColorDialog()
    picker.FullOpen = True
    if current is not None:
        picker.Color = DrawingColor.FromArgb(*current)
    try:
        if picker.ShowDialog() != DialogResult.OK:
            return CANCEL
        return (int(picker.Color.R), int(picker.Color.G), int(picker.Color.B))
    finally:
        picker.Dispose()


class Controller(object):
    def __init__(self, rows, doc, host_view, owner_hwnd=None):
        self.rows = rows
        self.doc = doc
        self.host_view = host_view
        self.win = _root()
        self.root = self.win
        if owner_hwnd:
            try:
                WindowInteropHelper(self.win).Owner = owner_hwnd
            except Exception:
                pass
        self.win.Title = u'MyRvtTools — Координационные виды'
        self.selected = None
        self.groups = {}
        self.colors = {}
        self.color_order = []
        self._changing_groups = False
        self._load_defaults()
        self._wire()
        _set_text(self.root.FindName('context_label'),
                  u'Модель: {}   •   Вид: {}'.format(doc.Title, link_views.safe_name(host_view)))
        self.render_groups()
        self.render_links()
        self.render_views()
        self.render_color_tabs()

    def _row_key(self, row):
        return u'{}#type:{}'.format(row['model_key'], _id_value(row['link'].GetTypeId()))

    def _load_defaults(self):
        for row in self.rows:
            key = self._row_key(row)
            row['profile_key'] = key
            self.groups[key] = u'Без группы'
            row['target'] = row.get('current_view')
            row['manual'] = False
            row['cleared'] = False
            if row.get('target') is not None:
                self.color_order.append(key)
        data = self._read_autosave()
        if data:
            try:
                self._load_profile_data(data)
            except Exception:
                pass

    def _autosave_path(self):
        path = getattr(self.doc, 'PathName', None) or getattr(self.doc, 'Title', u'project')
        view_id = getattr(getattr(self.host_view, 'Id', None), 'IntegerValue', None)
        path = u'{}#{}'.format(path, view_id if view_id is not None else u'view')
        digest = hashlib.md5(unicode(path).encode('utf-8')).hexdigest()
        root = os.environ.get('APPDATA') or os.path.expanduser('~')
        folder = os.path.join(root, 'EOM Lab', 'CoordinationViews')
        if not os.path.isdir(folder):
            try:
                os.makedirs(folder)
            except Exception:
                return None
        return os.path.join(folder, digest + '.json')

    def _read_autosave(self):
        path = self._autosave_path()
        if not path or not os.path.isfile(path):
            return None
        try:
            with io.open(path, 'r', encoding='utf-8-sig') as stream:
                return json.load(stream)
        except Exception:
            return None

    def _write_autosave(self):
        path = self._autosave_path()
        if not path:
            return
        try:
            with io.open(path, 'w', encoding='utf-8') as stream:
                stream.write(json.dumps(self._profile_data(), ensure_ascii=False, indent=2))
        except Exception:
            pass

    def _wire(self):
        self.root.FindName('link_search_box').TextChanged += self._on_filter
        self.root.FindName('group_filter').SelectionChanged += self._on_filter
        self.root.FindName('configured_only_box').Checked += self._on_filter
        self.root.FindName('configured_only_box').Unchecked += self._on_filter
        self.root.FindName('view_search_box').TextChanged += self._on_view_filter
        self.root.FindName('group_button').Click += self._on_group
        self.root.FindName('expand_all_button').Click += self._expand_all
        self.root.FindName('collapse_all_button').Click += self._collapse_all
        self.root.FindName('view_all_on_button').Click += self._choose_first
        self.root.FindName('view_clear_button').Click += self._clear_view
        self.root.FindName('save_profile_button').Click += self.export_profile
        self.root.FindName('load_profile_button').Click += self.import_profile
        self.root.FindName('cancel_button').Click += lambda s, a: self.win.Close()
        self.root.FindName('apply_button').Click += self.apply

    def _load_profile_data(self, data):
        if not isinstance(data, dict):
            raise ValueError(u'Файл профиля должен содержать объект JSON.')
        links = data.get('links') or {}
        groups = data.get('groups') or {}
        by_name = {}
        for item in links.values():
            if not isinstance(item, dict) or not item.get('model_name'):
                continue
            name = item.get('model_name')
            by_name.setdefault(name, []).append(item)
        for row in self.rows:
            key = self._row_key(row)
            fallback = by_name.get(row['model_name']) or []
            item = links.get(key) or links.get(row['model_key'])
            group_key = key
            if item is None:
                legacy_keys = [u'{}#{}'.format(row['model_key'], _id_value(link.Id))
                               for link in row['instances']]
                legacy_keys = [old for old in legacy_keys if isinstance(links.get(old), dict)]
                chosen = [old for old in legacy_keys if links[old].get('view_name')]
                if len(set(links[old]['view_name'] for old in chosen)) <= 1 and legacy_keys:
                    group_key = (chosen or legacy_keys)[0]
                    item = links[group_key]
                elif not legacy_keys and len(fallback) == 1:
                    item = fallback[0]
            self.groups[key] = unicode(groups.get(group_key, groups.get(row['model_key'], u'Без группы')) or u'Без группы')
            if not isinstance(item, dict):
                continue
            wanted = item.get('view_name')
            if wanted:
                row['target'] = next((view for view in row['views']
                                      if link_views.safe_name(view) == wanted), None)
            color = item.get('color')
            if isinstance(color, list) and len(color) == 3:
                self.colors[key] = tuple(int(x) for x in color)
            if row.get('target') is not None and key not in self.color_order:
                self.color_order.append(key)

    def render_groups(self):
        combo = self.root.FindName('group_filter')
        old = combo.SelectedItem
        self._changing_groups = True
        try:
            combo.Items.Clear()
            combo.Items.Add(u'Все группы')
            names = sorted(set(self.groups.values()), key=lambda value: value.lower())
            for name in names:
                combo.Items.Add(name)
            combo.SelectedItem = old if old in combo.Items else u'Все группы'
        finally:
            self._changing_groups = False

    def _link_visible(self, row):
        query = unicode(self.root.FindName('link_search_box').Text or u'').strip()
        if query and not text_search.matches(unicode(row['name']), query):
            return False
        group = self.root.FindName('group_filter').SelectedItem
        if group and group != u'Все группы' and group != self.groups.get(row['profile_key']):
            return False
        configured_only = bool(self.root.FindName('configured_only_box').IsChecked)
        if configured_only and row.get('target') is None:
            return False
        return True

    def _make_link_button(self, row):
        button = Button()
        button.Style = self.root.FindResource('Tile')
        button.HorizontalContentAlignment = HorizontalAlignment.Left
        panel = StackPanel()
        title = TextBlock()
        title.Text = unicode(row['name'])
        subtitle = TextBlock()
        subtitle.FontSize = 10
        subtitle.Foreground = Brushes.Gray
        target = row.get('target')
        subtitle.Text = (u'✓ ' + link_views.display_view_name(target)) if target else u'Вид не выбран'
        panel.Children.Add(title)
        panel.Children.Add(subtitle)
        button.Content = panel
        button.ToolTip = u'{}\nГруппа: {}\n{}'.format(
            row['name'], self.groups[row['profile_key']], row['current'])
        button.Background = SolidColorBrush(Color.FromRgb(215, 236, 216)) if row is self.selected else Brushes.Transparent
        button.Click += lambda sender, args, item=row: self.select_link(item)
        return button

    def render_links(self):
        panel = self.root.FindName('links_panel')
        panel.Children.Clear()
        visible = [row for row in self.rows if self._link_visible(row)]
        grouped = {}
        for row in visible:
            grouped.setdefault(self.groups.get(row['profile_key'], u'Без группы'), []).append(row)
        for group in sorted(grouped, key=lambda value: value.lower()):
            expander = Expander()
            expander.IsExpanded = True
            header = StackPanel()
            header.Orientation = Orientation.Horizontal
            text = TextBlock()
            text.Text = u'{}  ({})'.format(group, len(grouped[group]))
            text.FontWeight = FontWeights.SemiBold
            header.Children.Add(text)
            expander.Header = header
            inside = StackPanel()
            for row in sorted(grouped[group], key=lambda item: unicode(item['name']).lower()):
                inside.Children.Add(self._make_link_button(row))
            expander.Content = inside
            panel.Children.Add(expander)
        if not visible:
            empty = TextBlock()
            empty.Text = u'Связи не найдены'
            empty.Foreground = Brushes.Gray
            panel.Children.Add(empty)

        self.render_configured()

    def render_configured(self):
        """Show link/view assignments currently selected for the active host view."""
        panel = self.root.FindName('configured_panel')
        if panel is None:
            return
        panel.Children.Clear()
        configured = [row for row in self.rows if row.get('target') is not None]
        count = self.root.FindName('configured_count_label')
        if count is not None:
            _set_text(count, u'{} шт.'.format(len(configured)))
        if not configured:
            empty = TextBlock()
            empty.Text = u'На виде пока ничего не настроено'
            empty.Style = self.root.FindResource('Hint')
            panel.Children.Add(empty)
            return
        for row in sorted(configured, key=lambda item: (
                unicode(item.get('name') or u'').lower(),
                link_views.display_view_name(item['target']).lower())):
            button = Button()
            button.Style = self.root.FindResource('Tile')
            button.HorizontalContentAlignment = HorizontalAlignment.Left
            button.Margin = Thickness(0, 1, 0, 1)
            content = StackPanel()
            title = TextBlock()
            title.Text = unicode(row.get('name') or row.get('model_name') or u'')
            title.FontWeight = FontWeights.SemiBold
            view_text = TextBlock()
            view_text.Text = link_views.display_view_name(row['target'])
            view_text.FontSize = 10
            view_text.Foreground = Brushes.Gray
            content.Children.Add(title)
            content.Children.Add(view_text)
            button.Content = content
            button.ToolTip = u'{}\n{}'.format(title.Text, view_text.Text)
            button.Background = (SolidColorBrush(Color.FromRgb(215, 236, 216))
                                 if row is self.selected else Brushes.Transparent)
            button.Click += lambda sender, args, item=row: self.select_link(item)
            panel.Children.Add(button)

    def select_link(self, row):
        self.selected = row
        self.render_links()
        self.render_views()
        self.render_color_tabs()
        self._select_color_tab(row['profile_key'])

    def _visible_views(self, row):
        query = unicode(self.root.FindName('view_search_box').Text or u'').strip()
        if not query:
            return row['views']
        return [view for view in row['views']
                if (text_search.matches(link_views.safe_name(view), query)
                    or text_search.matches(link_views.display_view_name(view), query))]

    def render_views(self):
        panel = self.root.FindName('views_panel')
        panel.Children.Clear()
        if self.selected is None:
            hint = TextBlock()
            hint.Text = u'Выберите связь слева, чтобы увидеть её координационные виды.'
            hint.Style = self.root.FindResource('Hint')
            panel.Children.Add(hint)
            _set_text(self.root.FindName('selected_link_label'), u'Выберите связь')
            return
        _set_text(self.root.FindName('selected_link_label'), self.selected['name'])
        views = self._visible_views(self.selected)
        if not views:
            empty = TextBlock()
            empty.Text = u'Виды не найдены по текущему фильтру.'
            empty.Foreground = Brushes.Gray
            panel.Children.Add(empty)
        for view in views:
            check = CheckBox()
            check.Content = link_views.display_view_name(view)
            check.Margin = Thickness(2, 3, 2, 3)
            check.IsChecked = self.selected.get('target') is view
            check.ToolTip = u'{}\nТип: {}'.format(link_views.display_view_name(view), view.ViewType)
            check.Checked += lambda sender, args, item=view: self._choose_view(item)
            check.Unchecked += lambda sender, args: self._clear_view(sender, args)
            panel.Children.Add(check)

    def _choose_view(self, view):
        if self.selected is None:
            return
        self.selected['target'] = view
        self.selected['manual'] = True
        self.selected['cleared'] = False
        key = self.selected['profile_key']
        if key not in self.color_order:
            self.color_order.append(key)
        self.render_views()
        self.render_links()
        self.render_color_tabs()
        self._select_color_tab(self.selected['profile_key'])

    def _choose_first(self, sender, args):
        views = self._visible_views(self.selected) if self.selected is not None else []
        if views:
            self._choose_view(views[0])

    def _clear_view(self, sender, args):
        if self.selected is not None:
            self.selected['target'] = None
            self.selected['manual'] = True
            self.selected['cleared'] = True
            key = self.selected['profile_key']
            if key in self.color_order:
                self.color_order.remove(key)
            self.render_views()
            self.render_links()
            self.render_color_tabs()
            if self.selected.get('target') is not None:
                self._select_color_tab(self.selected['profile_key'])

    def _selection_color(self, row):
        return self.colors.get(row['profile_key'])

    def render_color_tabs(self):
        tabs = self.root.FindName('color_tabs')
        tabs.Items.Clear()
        for row in [item for item in self.rows if item.get('target') is not None]:
            view = row['target']
            tab = TabItem()
            tab.Header = u'{} — {}'.format(row['model_name'], link_views.safe_name(view))
            content = StackPanel()
            model = TextBlock()
            model.Text = u'Модель: {}'.format(row['model_name'])
            model.Style = self.root.FindResource('H2')
            content.Children.Add(model)
            view_text = TextBlock()
            view_text.Text = u'Вид: {}'.format(link_views.safe_name(view))
            view_text.Style = self.root.FindResource('Hint')
            view_text.Margin = Thickness(0, 4, 0, 12)
            content.Children.Add(view_text)
            color_button = Button()
            color_button.Width = 160
            _button_color(color_button, self._selection_color(row))
            color_button.Click += lambda sender, args, item=row, control=color_button: self._pick_color(item, control)
            content.Children.Add(color_button)
            clear = Button()
            clear.Content = u'Убрать цвет'
            clear.Margin = Thickness(0, 6, 0, 0)
            clear.Click += lambda sender, args, item=row: self._clear_color(item)
            content.Children.Add(clear)
            note = TextBlock()
            note.Text = u'Цвет задаётся для связи на целевом виде. Заливка и линии будут окрашены при применении.'
            note.Style = self.root.FindResource('Hint')
            note.Margin = Thickness(0, 16, 0, 0)
            content.Children.Add(note)
            tab.Content = content
            tabs.Items.Add(tab)

    def _pick_color(self, row, control):
        picked = _choose_color(self._selection_color(row))
        if picked is CANCEL:
            return
        self.colors[row['profile_key']] = picked
        _button_color(control, picked)

    def _clear_color(self, row):
        self.colors.pop(row['profile_key'], None)
        self.render_color_tabs()

    # Re-render color tabs in a stable order.  The old implementation was
    # kept for compatibility with older pyRevit caches; this later method is
    # the one Python resolves and uses.
    def render_color_tabs(self):
        tabs = self.root.FindName('color_tabs')
        old_tag = tabs.SelectedItem.Tag if tabs.SelectedItem is not None else None
        tabs.Items.Clear()
        # Keep tabs in the same order as the left link list.  The order must
        # not depend on which link was clicked most recently.
        ordered = [row for row in self.rows if row.get('target') is not None]
        for row in ordered:
            view = row['target']
            tab = TabItem()
            tab.Tag = row['profile_key']
            header = Grid()
            header.ColumnDefinitions.Add(ColumnDefinition())
            header.ColumnDefinitions.Add(ColumnDefinition())
            header.ColumnDefinitions[1].Width = GridLength.Auto
            title = TextBlock()
            title.Text = u'{} - {}'.format(row['model_name'], link_views.safe_name(view))
            Grid.SetColumn(title, 0)
            header.Children.Add(title)
            swatch = Button()
            swatch.Width = 18
            swatch.Height = 18
            swatch.Padding = Thickness(0)
            swatch.Margin = Thickness(8, 0, 0, 0)
            rgb = self._selection_color(row)
            _swatch(swatch, rgb)
            swatch.Click += lambda sender, args, item=row, control=swatch: self._pick_color(item, control)
            Grid.SetColumn(swatch, 1)
            header.Children.Add(swatch)
            tab.Header = header
            tabs.Items.Add(tab)
        if old_tag is not None:
            for item in tabs.Items:
                if item.Tag == old_tag:
                    tabs.SelectedItem = item
                    break

    def _select_color_tab(self, key):
        tabs = self.root.FindName('color_tabs')
        if tabs is None:
            return
        for item in tabs.Items:
            if item.Tag == key:
                tabs.SelectedItem = item
                break

    def _pick_color(self, row, control):
        picked = _choose_color(self._selection_color(row))
        if picked is CANCEL:
            return
        self.colors[row['profile_key']] = picked
        self.render_color_tabs()

    def render_color_tabs(self):
        # Color overrides are intentionally disabled. Revit's element
        # override colors apply to the whole link instance, not just its
        # selected LinkedViewId.
        return

    def _on_filter(self, sender, args):
        if self._changing_groups:
            return
        self.render_groups()
        self.render_links()

    def _on_view_filter(self, sender, args):
        self.render_views()

    def _on_group(self, sender, args):
        if self.selected is None:
            forms.alert(u'Сначала выберите связь слева.', title=self.win.Title)
            return
        current = self.groups.get(self.selected['profile_key'], u'Без группы')
        value = forms.ask_for_string(default=current, prompt=u'Название группы для выбранной связи:',
                                     title=u'Группировка связей')
        if value is None:
            return
        self.groups[self.selected['profile_key']] = unicode(value).strip() or u'Без группы'
        self.render_groups()
        self.render_links()

    def _on_group(self, sender, args):
        from eomlab_coord import groups_dialog
        try:
            result = groups_dialog.show(self.rows, self.groups, self.win)
        except Exception as exc:
            forms.alert(u'Не удалось открыть редактор групп:\n{}'.format(exc), title=self.win.Title)
            return
        if result is None:
            return
        self.groups = result
        self.render_groups()
        self.render_links()

    def _expand_all(self, sender, args):
        for child in self.root.FindName('links_panel').Children:
            if isinstance(child, Expander):
                child.IsExpanded = True

    def _collapse_all(self, sender, args):
        for child in self.root.FindName('links_panel').Children:
            if isinstance(child, Expander):
                child.IsExpanded = False

    def _profile_data(self):
        links = {}
        groups = {}
        for row in self.rows:
            key = row['profile_key']
            groups[key] = self.groups.get(key, u'Не в группе')
            target = row.get('target')
            links[key] = {'model_name': row['model_name'],
                          'view_name': link_views.safe_name(target) if target is not None else None,
                          'view_type': unicode(target.ViewType) if target is not None else None,
                          'color': list(self._selection_color(row)) if self._selection_color(row) else None}
        return {'version': 2, 'links': links, 'groups': groups}

    def export_profile(self, sender, args):
        dialog = SaveFileDialog()
        dialog.Filter = u'Профиль координационных видов (*.json)|*.json|Все файлы (*.*)|*.*'
        dialog.FileName = u'coordination_views_profile.json'
        if dialog.ShowDialog() != True:
            return
        with io.open(dialog.FileName, 'w', encoding='utf-8') as stream:
            stream.write(json.dumps(self._profile_data(), ensure_ascii=False, indent=2))
        _set_text(self.root.FindName('status_label'), u'Профиль сохранён: {}'.format(dialog.FileName))

    def import_profile(self, sender, args):
        dialog = OpenFileDialog()
        dialog.Filter = u'Профиль координационных видов (*.json)|*.json|Все файлы (*.*)|*.*'
        if dialog.ShowDialog() != True:
            return
        try:
            with io.open(dialog.FileName, 'r', encoding='utf-8-sig') as stream:
                self._load_profile_data(json.load(stream))
            self.render_groups()
            self.render_links()
            self.render_views()
            self.render_color_tabs()
            _set_text(self.root.FindName('status_label'), u'Профиль загружен: {}'.format(dialog.FileName))
        except Exception as exc:
            forms.alert(u'Не удалось загрузить профиль:\n{}'.format(exc), title=self.win.Title)

    def apply(self, sender, args):
        chosen = [row for row in self.rows if row.get('target') is not None]
        clear_rows = [row for row in self.rows
                      if row.get('cleared') and row.get('target') is None]
        if not chosen and not clear_rows:
            forms.alert(u'Выберите хотя бы один координационный вид.', title=self.win.Title)
            return
        failed = []
        for row in chosen:
            row.pop('apply_color', None)
            try:
                backend.validate(row, backend.DEFAULT_WORKSET, False)
            except Exception as exc:
                failed.append(u'{}: {}'.format(row['name'], exc))
        if failed:
            forms.alert(u'Проверка настроек:\n\n' + u'\n'.join(failed[:12]), title=self.win.Title)
            return
        self.result = {'rows': chosen, 'clear_rows': clear_rows,
                       'workset': backend.DEFAULT_WORKSET}
        self._write_autosave()
        self.win.Close()

    def show(self):
        self.result = {'rows': None, 'clear_rows': [],
                       'workset': backend.DEFAULT_WORKSET}
        self.win.ShowDialog()
        return self.result


def show(rows, doc, host_view, owner_hwnd=None):
    return Controller(rows, doc, host_view, owner_hwnd).show()
