# -*- coding: utf-8 -*-
"""View Manager-style file and calculation sheet selection."""
import io
import os

import clr
clr.AddReference('WindowsBase')
clr.AddReference('PresentationCore')
clr.AddReference('PresentationFramework')

from Microsoft.Win32 import OpenFileDialog
from System import Array, String
from System.Windows.Interop import WindowInteropHelper
from System.Windows.Markup import XamlReader
from System.Windows.Media import Brushes

from .layout import fill_label, group_legend, plan_layout
from .section_data import find_sources, parse_section
from .workbook import read_sheets


_HERE = os.path.dirname(os.path.abspath(__file__))
_TEXT_TYPE = u'SMNX_2.2'
_REGION_TYPE = u'ADSK_У_Сплошная_Серая'
_PICK_INSTRUCTION = u'После нажатия «Построить» укажите наружный нижний левый угол лотка.'


def _read(filename):
    with io.open(os.path.join(_HERE, filename), 'r', encoding='utf-8-sig') as stream:
        return stream.read()


def _load_window(filename):
    return XamlReader.Parse(_read(filename).replace('__VM_STYLE__', _read('vm_style.xaml')))


def _preferred_type(types, saved, default):
    if saved in types:
        return saved
    return default if default in types else None


def mm(value):
    return u'{:.2f}'.format(value).rstrip(u'0').rstrip(u'.').replace(u'.', u',')


class TraySectionDialog(object):
    def __init__(self, view_name, owner, resolve_symbol, style_types, config, save_config):
        self.window = _load_window('tray_section.xaml')
        if owner:
            WindowInteropHelper(self.window).Owner = owner
        for name in ('section_view', 'gear', 'browse', 'file_path', 'sheets', 'use_group_names',
                     'summary', 'status', 'build'):
            setattr(self, name, self.window.FindName(name))
        self.section_view.Text = view_name
        self.default_status_color = self.status.Foreground
        self.resolve_symbol = resolve_symbol
        self.text_types, self.region_types = style_types
        self.config = config
        self.save_config = save_config
        self.text_type = _preferred_type(
            self.text_types, config.get_option('text_type', _TEXT_TYPE), _TEXT_TYPE)
        self.region_type = _preferred_type(
            self.region_types, config.get_option('region_type', _REGION_TYPE), _REGION_TYPE)
        self.sources = []
        self.section = None
        self.result = None
        self.browse.Click += self._browse
        self.gear.Click += self._settings
        self.sheets.SelectionChanged += self._select
        self.build.Click += self._build
        self.window.Loaded += self._focus

    def _focus(self, sender, args):
        self.browse.Focus()

    def _status(self, message, error=False):
        self.status.Text = message
        self.status.Foreground = Brushes.Firebrick if error else self.default_status_color

    def _edit_styles(self):
        window = _load_window('tray_styles.xaml')
        window.Owner = self.window
        text = window.FindName('text_types')
        region = window.FindName('region_types')
        save = window.FindName('save')
        for combo, types, selected in ((text, self.text_types, self.text_type),
                                       (region, self.region_types, self.region_type)):
            names = sorted(types, key=lambda name: name.lower())
            combo.ItemsSource = Array[String](names)
            combo.SelectedIndex = names.index(selected) if selected in types else -1
            combo.IsEnabled = bool(names)

        missing = []
        if not self.text_types:
            missing.append(u'текстовых примечаний')
        if not self.region_types:
            missing.append(u'цветовых областей')
        if missing:
            window.FindName('message').Text = u'Добавьте в проект типы {}.'.format(
                u' и '.join(missing))

        def refresh(sender, args):
            save.IsEnabled = text.SelectedIndex >= 0 and region.SelectedIndex >= 0

        def accept(sender, args):
            if save.IsEnabled:
                window.DialogResult = True

        text.SelectionChanged += refresh
        region.SelectionChanged += refresh
        save.Click += accept
        first = region if self.text_type and not self.region_type else text
        window.Loaded += lambda sender, args: first.Focus()
        refresh(None, None)
        if window.ShowDialog() != True:
            return False
        self.text_type, self.region_type = text.SelectedItem, region.SelectedItem
        self.config.text_type, self.config.region_type = self.text_type, self.region_type
        self.save_config()
        return True

    def _settings(self, sender, args):
        try:
            if self._edit_styles() and self.section and not self.section.errors:
                self._status(_PICK_INSTRUCTION)
        except Exception as error:
            self._status(u'{}'.format(error), True)

    def _browse(self, sender, args):
        picker = OpenFileDialog()
        picker.Title = u'Выберите расчёт лотков'
        picker.Filter = u'Книги Excel (*.xlsx;*.xls)|*.xlsx;*.xls'
        picker.CheckFileExists = True
        if picker.ShowDialog(self.window) != True:
            return
        self.file_path.Text = picker.FileName
        self.file_path.ToolTip = picker.FileName
        self.sources = []
        self.sheets.ItemsSource = None
        self.sheets.IsEnabled = False
        self.section = None
        self.build.IsEnabled = False
        self.summary.Text = u''
        try:
            self.sources = find_sources(read_sheets(picker.FileName))
        except Exception as error:
            self._status(u'Не удалось прочитать книгу Excel. {}'.format(error), True)
            return
        if not self.sources:
            self._status(u'Расчётные листы с размерами лотка и таблицей кабелей не найдены.', True)
            return
        self.sheets.ItemsSource = Array[String]([source.sheet.name for source in self.sources])
        self.sheets.IsEnabled = True
        self.sheets.SelectedIndex = 0
        self.sheets.Focus()

    def _select(self, sender, args):
        self.section = None
        self.build.IsEnabled = False
        self.summary.Text = u''
        index = self.sheets.SelectedIndex
        if index < 0 or index >= len(self.sources):
            return
        section = parse_section(self.sources[index])
        self.section = section
        dimensions = u''
        if section.width is not None and section.height is not None:
            dimensions = u'Лоток {} × {} мм'.format(mm(section.width), mm(section.height))
        if section.errors:
            self.summary.Text = dimensions
            self._status(u'Лист «{}»\n{}'.format(section.name, u'\n'.join(section.errors)), True)
            return
        self.summary.Text = u'{}   ·   Кабелей: {}   ·   Заполнение: {}'.format(
            dimensions, section.count, fill_label(section.fill_percent))
        self._status(_PICK_INSTRUCTION)
        self.build.IsEnabled = True

    def _build(self, sender, args):
        if self.section is None or self.section.errors:
            return
        try:
            if self.text_type is None or self.region_type is None:
                if not self._edit_styles():
                    return
            resources = (self.resolve_symbol(), self.region_types[self.region_type],
                         self.text_types[self.text_type])
            layout = plan_layout(self.section)
            legend = group_legend(layout.cables, self.use_group_names.IsChecked == True)
        except Exception as error:
            self._status(u'{}'.format(error), True)
            return
        self.result = self.section, layout, resources, legend
        self.window.DialogResult = True

    def show(self):
        self.window.ShowDialog()
        return self.result
