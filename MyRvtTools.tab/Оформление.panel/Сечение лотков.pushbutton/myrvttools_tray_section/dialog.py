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


def _read(filename):
    with io.open(os.path.join(_HERE, filename), 'r', encoding='utf-8-sig') as stream:
        return stream.read()


def mm(value):
    return u'{:.2f}'.format(value).rstrip(u'0').rstrip(u'.').replace(u'.', u',')


class TraySectionDialog(object):
    def __init__(self, view_name, owner, resolve_resources):
        self.window = XamlReader.Parse(_read('tray_section.xaml').replace(
            '__VM_STYLE__', _read('vm_style.xaml')))
        if owner:
            WindowInteropHelper(self.window).Owner = owner
        for name in ('section_view', 'browse', 'file_path', 'sheets', 'use_group_names',
                     'summary', 'status', 'build'):
            setattr(self, name, self.window.FindName(name))
        self.section_view.Text = view_name
        self.default_status_color = self.status.Foreground
        self.resolve_resources = resolve_resources
        self.sources = []
        self.section = None
        self.result = None
        self.browse.Click += self._browse
        self.sheets.SelectionChanged += self._select
        self.build.Click += self._build
        self.window.Loaded += self._focus

    def _focus(self, sender, args):
        self.browse.Focus()

    def _status(self, message, error=False):
        self.status.Text = message
        self.status.Foreground = Brushes.Firebrick if error else self.default_status_color

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
        self._status(u'После нажатия «Построить» укажите наружный нижний левый угол лотка.')
        self.build.IsEnabled = True

    def _build(self, sender, args):
        if self.section is None or self.section.errors:
            return
        try:
            resources = self.resolve_resources()
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
