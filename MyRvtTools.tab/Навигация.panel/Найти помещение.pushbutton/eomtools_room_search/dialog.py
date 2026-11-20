# -*- coding: utf-8 -*-
"""View Manager-style room search and parameter settings."""
import io
import os
from collections import Counter

import clr
clr.AddReference('WindowsBase')
clr.AddReference('PresentationCore')
clr.AddReference('PresentationFramework')
clr.AddReference('System.Data')

from System import Array, String, TimeSpan
from System.Data import DataTable
from System.Windows import Visibility
from System.Windows.Controls import ItemsControl
from System.Windows.Input import Key
from System.Windows.Interop import WindowInteropHelper
from System.Windows.Markup import XamlReader
from System.Windows.Threading import DispatcherTimer
from pyrevit import forms

from eomtools_room_search import rooms, search

TITLE = u'Найти помещение'
HERE = os.path.dirname(os.path.abspath(__file__))


def _read(name):
    with io.open(os.path.join(HERE, name), 'r', encoding='utf-8') as stream:
        return stream.read()


class RoomSearch(object):
    def __init__(self, sources, config, save_config, model_title, owner):
        self.sources = sources
        self.config = config
        self.save_config = save_config
        self.result = None
        self.visible = []
        self.entries = []
        self.window = XamlReader.Parse(_read('room_search.xaml').replace(
            '__VM_STYLE__', _read('vm_style.xaml')))
        if owner:
            WindowInteropHelper(self.window).Owner = owner
        for name in ('context', 'gear', 'settings', 'first', 'second', 'save', 'cancel_settings',
                     'field_hint', 'query', 'source', 'results', 'status', 'show_result'):
            setattr(self, name, self.window.FindName(name))
        self.context.Text = model_title
        self.source.ItemsSource = Array[String]([u'Все модели'] + [item.title for item in sources])
        self.source.SelectedIndex = 0
        self.labels = {}
        self.field_sources = {}
        for source in sources:
            for key, label in source.fields.items():
                self.labels.setdefault(key, label)
                self.field_sources.setdefault(key, set()).add(source.title)
        saved = config.get_option('search_fields', [])
        self.fields = search.select_fields(saved if isinstance(saved, list) else [],
                                           self.labels, rooms.DEFAULT_FIELDS)

        self.timer = DispatcherTimer()
        self.timer.Interval = TimeSpan.FromMilliseconds(160)
        self.timer.Tick += self._refresh_timer
        self.window.Closed += self._stop_timer
        self.window.Loaded += self._focus_query
        self.gear.Click += self._toggle_settings
        self.first.SelectionChanged += self._update_second
        self.save.Click += self._save_settings
        self.cancel_settings.Click += self._close_settings
        self.source.SelectionChanged += self._source_changed
        self.query.TextChanged += self._schedule_refresh
        self.query.KeyDown += self._enter
        self.results.KeyDown += self._enter
        self.results.SelectionChanged += self._selection_changed
        self.results.MouseDoubleClick += self._open_row
        self.show_result.Click += self._open
        self._update_fields()

    def _selected_sources(self):
        index = self.source.SelectedIndex
        return self.sources[index - 1:index] if index > 0 else self.sources

    def _update_fields(self):
        self.entries = []
        self.field_hint.Text = u'Поиск по полям · ' + u' / '.join(self.labels[key] for key in self.fields)
        self._refresh()

    def _index_rooms(self):
        for source in self._selected_sources():
            for room in source.rooms:
                values = room.search_values(self.fields, source.parameter_ids)
                self.entries.append((room, tuple(search.normalize(value) for value in values),
                                     values, source.placements))

    def _focus_query(self, sender, args):
        self.query.Focus()

    def _source_changed(self, sender, args):
        self._update_fields()
        if self.settings.Visibility == Visibility.Visible:
            self._populate_settings()

    def _toggle_settings(self, sender, args):
        if self.settings.Visibility == Visibility.Visible:
            self._close_settings(sender, args)
        else:
            self._populate_settings()
            self.settings.Visibility = Visibility.Visible
            self.first.Focus()

    def _populate_settings(self):
        options = set(self.fields)
        for source in self._selected_sources():
            options.update(source.fields)
        self.first_keys = sorted(options, key=lambda key: (key not in rooms.DEFAULT_FIELDS,
                                                           self.labels[key].lower(), key))
        frequency = Counter(self.labels[key] for key in options)
        self.options = {}
        for key in options:
            label = self.labels[key]
            if frequency[label] > 1:
                sources = u', '.join(sorted(self.field_sources[key]))
                label = u'{} — {} [{}]'.format(label, sources, key.split(':', 1)[1][-8:])
            self.options[key] = label
        self.first.ItemsSource = Array[String]([self.options[key] for key in self.first_keys])
        self.first.SelectedIndex = self.first_keys.index(self.fields[0])
        self._fill_second(self.fields[1] if len(self.fields) == 2 else None)

    def _chosen(self, combo, keys):
        return keys[combo.SelectedIndex] if combo.SelectedIndex >= 0 else None

    def _update_second(self, sender, args):
        previous = self._chosen(self.second, self.second_keys) if hasattr(self, 'second_keys') else None
        self._fill_second(previous)

    def _fill_second(self, desired):
        first = self._chosen(self.first, self.first_keys)
        self.second_keys = [None] + [key for key in self.first_keys if key != first]
        labels = [u'Без второго параметра'] + [self.options[key] for key in self.second_keys[1:]]
        self.second.ItemsSource = Array[String](labels)
        self.second.SelectedIndex = self.second_keys.index(desired) if desired in self.second_keys else 0

    def _close_settings(self, sender, args):
        self.settings.Visibility = Visibility.Collapsed
        self.query.Focus()

    def _save_settings(self, sender, args):
        self.fields = [key for key in (self._chosen(self.first, self.first_keys),
                                       self._chosen(self.second, self.second_keys)) if key is not None]
        self.config.search_fields = self.fields
        try:
            self.save_config()
        except (IOError, OSError) as exc:
            forms.alert(u'Параметры применены для этого запуска. Сохранить настройки не удалось.\n{}'.format(exc),
                        title=TITLE)
        self._update_fields()
        self._close_settings(sender, args)

    def _schedule_refresh(self, sender, args):
        self.timer.Stop()
        self.timer.Start()

    def _stop_timer(self, sender, args):
        self.timer.Stop()

    def _refresh_timer(self, sender, args):
        self._refresh()

    def _refresh(self):
        self.timer.Stop()
        query = self.query.Text
        if search.normalize(query) and not self.entries:
            self._index_rooms()
        table = DataTable()
        for name in ('Room', 'Values', 'Level', 'Source'):
            table.Columns.Add(name)
        self.visible = []
        table.BeginLoadData()
        for room, normalized, values, placements in search.matches(self.entries, query):
            for link, source_label in placements:
                row = table.NewRow()
                row['Room'] = u'{}  ·  {}'.format(room.number or u'—', room.name or u'Без имени')
                row['Values'] = u' / '.join(value for value in values if value)
                row['Level'] = room.level
                row['Source'] = source_label
                table.Rows.Add(row)
                self.visible.append((room, link))
        table.EndLoadData()
        self.results.ItemsSource = table.DefaultView
        self.results.SelectedIndex = 0 if self.visible else -1
        if not search.normalize(query):
            self.status.Text = u'Введите значение параметра.'
        elif self.visible:
            self.status.Text = u'Найдено: {}'.format(len(self.visible))
        else:
            self.status.Text = u'Совпадений нет.'
        self._selection_changed(None, None)

    def _selection_changed(self, sender, args):
        self.show_result.IsEnabled = self.results.SelectedIndex >= 0

    def _open_row(self, sender, args):
        if ItemsControl.ContainerFromElement(self.results, args.OriginalSource) is not None:
            self._open(sender, args)

    def _enter(self, sender, args):
        if args.Key == Key.Enter:
            self._open(sender, args)
            args.Handled = True

    def _open(self, sender, args):
        if self.timer.IsEnabled:
            self._refresh()
        index = self.results.SelectedIndex
        if 0 <= index < len(self.visible):
            self.result = self.visible[index]
            self.window.Close()

    def show(self):
        self.window.ShowDialog()
        return self.result
