# -*- coding: utf-8 -*-
"""View Manager: интерфейс закреплённой панели и окна настройки.

WPF только ставит запросы в очередь; чтение и изменение модели выполняются
в ExternalEvent (``execute``) или в Idling (``on_idle``) — это контекст Revit API.
"""
try:
    unicode
except NameError:
    unicode = str

import copy
import io
import os
import re

import clr
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')
from System.Windows import (CornerRadius, FontWeights, MessageBox, MessageBoxButton,
                            MessageBoxImage, MessageBoxResult, Thickness, VerticalAlignment,
                            TextTrimming, TextWrapping)
from System.Windows import GridLength, GridUnitType, HorizontalAlignment
from System.Windows import (DataObject, DragDrop, DragDropEffects, DragDropKeyStates, FontStyles,
                            SystemParameters, Visibility)
from System.Windows.Controls import ColumnDefinition, Grid
from System.Windows.Controls import Slider
from System.Windows.Controls import (Border, Button, CheckBox, DockPanel, Expander, ListBox,
                                     ListBoxItem, ScrollViewer, ScrollBarVisibility, SelectionMode,
                                     StackPanel, TabItem, TextBlock, TextBox,
                                     WrapPanel, Dock, Orientation)
from System.Windows.Documents import LineBreak, Run
from System.Windows.Input import Cursors, Keyboard, ModifierKeys, MouseButtonState
from System.Windows.Interop import WindowInteropHelper
from System.Windows.Markup import XamlReader
from System.Windows.Media import Brushes, Color, SolidColorBrush, VisualTreeHelper
from Microsoft.Win32 import OpenFileDialog, SaveFileDialog
from Autodesk.Revit.UI import ExternalEventRequest

from eomtools import vm_colors, vm_profile, vm_revit, vm_templates

HERE = os.path.dirname(os.path.abspath(__file__))

TILE_MIN_WIDTH = 125.0
KINDS = ('model', 'annotation')
KIND_TITLES = {'model': u'Элементы', 'annotation': u'Аннотации'}


def _brush(hex_color):
    h = hex_color.lstrip('#')
    brush = SolidColorBrush(Color.FromRgb(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)))
    brush.Freeze()
    return brush


# состояние -> (фон, текст, рамка)
COLORS = {
    'on': (_brush('#2E7D32'), Brushes.White, _brush('#1B5E20')),
    'off': (_brush('#E3E6E9'), _brush('#455A64'), _brush('#B0BEC5')),
    'part': (_brush('#F9A825'), _brush('#212121'), _brush('#C68400')),
    'none': (_brush('#F5F5F5'), _brush('#9E9E9E'), _brush('#D6D6D6')),
}
LEGEND = (('on', u'включено'), ('off', u'выключено'), ('part', u'частично'))
TEXT_GRAY = Brushes.Gray
TEXT_RED = Brushes.Firebrick
TEXT_ORANGE = Brushes.DarkOrange

OTHER_SECTION = vm_profile.OTHER_SECTION


# Сокращения названий категорий на плитках (полное имя — в подсказке).
# Порядок важен: сначала длинные сочетания, потом отдельные слова.
CATEGORY_ABBR = (
    (u'Соединительные детали', u'Соед. дет.'),
    (u'соединительные детали', u'соед. дет.'),
    (u'Соединительная деталь', u'Соед. дет.'),
    (u'Осветительные приборы', u'Освет. приборы'),
    (u'Электрооборудование', u'Электрообор.'),
    (u'Электрические приборы', u'Эл. приборы'),
    (u'Электрические цепи', u'Эл. цепи'),
    (u'Механическое оборудование', u'Мех. оборуд.'),
    (u'Оборудование', u'Оборуд.'),
    (u'оборудования', u'оборуд.'),
    (u'Арматура', u'Арм.'),
    (u'арматуры', u'арм.'),
    (u'Пожарная сигнализация', u'Пож. сигнализация'),
    (u'Устройства связи', u'Устр. связи'),
    (u'Предохранительные устройства', u'Предохр. устр.'),
    (u'Обобщенные модели', u'Обобщ. модели'),
    (u'Обобщённые модели', u'Обобщ. модели'),
    (u'Кабельные лотки', u'Каб. лотки'),
    (u'кабельных лотков', u'каб. лотков'),
    (u'кабельного лотка', u'каб. лотка'),
    (u'воздуховодов', u'возд.'),
    (u'воздуховода', u'возд.'),
    (u'трубопроводов', u'труб.'),
    (u'трубопровода', u'труб.'),
    (u'Воздухораспределители', u'Воздухораспр.'),
    (u'по осевой', u'(ось)'),
    (u'изоляции', u'изол.'),
    (u'Изоляция', u'Изол.'),
    (u'Несущие колонны', u'Нес. колонны'),
    (u'Каркас несущий', u'Карк. несущ.'),
    (u'Обозначения', u'Обозн.'),
    (u'Границы 3D вида', u'Границы 3D'),
)


def short_category_name(name):
    text = name
    for long_, short in CATEGORY_ABBR:
        text = text.replace(long_, short)
    return u' '.join(text.split())


# «None» у перечислений .NET — ключевое слово Python, обращаемся через getattr
DDE_NONE = getattr(DragDropEffects, 'None')
MOD_NONE = getattr(ModifierKeys, 'None')
STYLE_SLOT = u'__VM_STYLE__'


def _ctrl(args):
    """Нажат ли Ctrl во время перетаскивания."""
    return (args.KeyStates & DragDropKeyStates.ControlKey) == DragDropKeyStates.ControlKey


def _read(name):
    with io.open(os.path.join(HERE, name), 'r', encoding='utf-8') as f:
        return f.read()


def _load_xaml(name):
    """Загрузить XAML; окна настройки получают общий стиль vm_style.xaml на место
    метки __VM_STYLE__."""
    text = _read(name)
    if STYLE_SLOT in text:
        text = text.replace(STYLE_SLOT, _read('vm_style.xaml'))
    return XamlReader.Parse(text)


def _matches(text, query):
    words = (query or u'').lower().split()
    low = text.lower()
    return all(w in low for w in words)


def _id_value(eid):
    value = getattr(eid, 'Value', None)
    return eid.IntegerValue if value is None else value


def _text(value, gray=False):
    """Текст как TextBlock: в подписи кнопки/галочки WPF съедает первый «_»."""
    tb = TextBlock()
    tb.Text = value
    tb.VerticalAlignment = VerticalAlignment.Center
    tb.TextTrimming = TextTrimming.CharacterEllipsis
    if gray:
        tb.Foreground = TEXT_GRAY
    return tb


# Длинные имена на плитках: чем длиннее, тем мельче шрифт; самые длинные — в две строки.
# (символов, размер шрифта, перенос на 2 строки)
FONT_STEPS = ((20, 12.0, False), (26, 11.0, False), (34, 10.0, False))
FONT_LONG = 9.5


def _fit_font(label, text):
    n = len(text)
    for limit, size, _ in FONT_STEPS:
        if n <= limit:
            label.FontSize = size
            return
    label.FontSize = FONT_LONG
    label.TextWrapping = TextWrapping.Wrap
    label.LineHeight = FONT_LONG * 1.2
    label.MaxHeight = FONT_LONG * 1.2 * 2 + 1   # не больше двух строк, дальше «…»


def _owner_window(win, hwnd):
    try:
        WindowInteropHelper(win).Owner = hwnd
    except Exception:
        pass


class Controller(object):
    """Логика и интерфейс панели. Заменяется целиком при обновлении кода."""

    NAMES = ('tabs', 'view_label', 'target_label', 'legend_panel', 'allow_template_box',
             'save_button', 'load_button', 'status_label',
             # рабочие наборы
             'all_on_button', 'all_off_button', 'groups_button', 'groups_panel', 'search_box',
             'worksets_panel', 'content_panel', 'hidden_sections_label',
             # категории
             'cat_all_on_button', 'cat_all_off_button', 'cat_setup_button', 'cat_content_panel',
             'model_on_button', 'model_off_button', 'annotation_on_button', 'annotation_off_button',
             'model_header', 'model_panel', 'annotation_header', 'annotation_panel',
             # цвета
             'colors_apply_button', 'colors_clear_button', 'colors_setup_button',
             'colors_take_button',
             # шаблоны вида
             'tpl_folder_button', 'tpl_folder_label', 'tpl_search_box', 'tpl_model_list',
             'tpl_export_button', 'tpl_export_current_button', 'tpl_library_list',
             'tpl_import_button', 'tpl_open_button', 'tpl_rescan_button',
             'colors_state_label', 'colors_panel')

    def __init__(self, host):
        self.host = host
        self.uiapp = host.uiapp
        self.root = _load_xaml('vm_panel.xaml')
        for name in self.NAMES:
            setattr(self, name, self.root.FindName(name))
        self.tile_style = self.root.FindResource('Tile')
        self.widths = {'ws': TILE_MIN_WIDTH, 'wsx': TILE_MIN_WIDTH, 'cat': TILE_MIN_WIDTH}
        self.wraps = {'ws': [], 'wsx': [], 'cat': []}
        self.profile = vm_profile.load()
        self.snap = None
        self.doc = None
        self.view_id = None
        self.pending = None
        self.dirty = True
        self.busy = False
        self.detached = False
        self.build_legend()
        self.allow_template_box.IsChecked = self.profile['allow_template']
        self.content_panel.SizeChanged += self.on_ws_resize
        self.cat_content_panel.SizeChanged += self.on_cat_resize
        self.tabs.SelectionChanged += self.on_tab
        self.all_on_button.Click += self.on_all_on
        self.all_off_button.Click += self.on_all_off
        self.groups_button.Click += self.on_edit_groups
        self.search_box.TextChanged += self.on_search
        self.cat_all_on_button.Click += self.on_cat_all_on
        self.cat_all_off_button.Click += self.on_cat_all_off
        self.cat_setup_button.Click += self.on_cat_setup
        for kind in KINDS:
            getattr(self, kind + '_on_button').Click += self._kind_handler(kind, True)
            getattr(self, kind + '_off_button').Click += self._kind_handler(kind, False)
        self.colors_apply_button.Click += self.on_colors_apply
        self.colors_clear_button.Click += self.on_colors_clear
        self.colors_setup_button.Click += self.on_colors_setup
        self.colors_take_button.Click += self.on_colors_take
        self.templates = []
        self.view_template_id = None
        self.tpl_folder_button.Click += self.on_tpl_folder
        self.tpl_search_box.TextChanged += self.on_tpl_search
        self.tpl_export_button.Click += self.on_tpl_export
        self.tpl_export_current_button.Click += self.on_tpl_export_current
        self.tpl_import_button.Click += self.on_tpl_import
        self.tpl_open_button.Click += self.on_tpl_open
        self.tpl_rescan_button.Click += self.on_tpl_rescan
        self.color_info = None
        self.colors_applied = None
        self.allow_template_box.Click += self.on_allow_template
        self.save_button.Click += self.on_save_profile
        self.load_button.Click += self.on_load_profile
        self.root.IsVisibleChanged += self.on_visible
        self.render()

    # ---------- жизненный цикл ----------
    def detach(self):
        self.detached = True

    def on_visible(self, sender, args):
        self.dirty = True

    def on_idle(self, uiapp):
        if self.detached or not self.dirty or self.pending is not None:
            return
        if not self.root.IsVisible:
            return
        try:
            self.refresh(uiapp)
        except Exception as exc:
            self.dirty = False
            self.status(u'Ошибка: {0}'.format(unicode(exc)))

    def refresh(self, uiapp):
        self.dirty = False
        uidoc = uiapp.ActiveUIDocument
        if uidoc is None:
            self.snap, self.doc, self.view_id = None, None, None
        else:
            view = uidoc.ActiveView
            self.doc = uidoc.Document
            self.view_id = view.Id if view is not None else None
            self.snap = vm_revit.snapshot(self.doc, view, self.profile)
            self.color_info, self.colors_applied = None, None
            self.templates, self.view_template_id = [], None
            if not self.doc.IsFamilyDocument:
                try:
                    self.templates = vm_templates.view_templates(self.doc)
                    if view is not None and _id_value(view.ViewTemplateId) > 0:
                        self.view_template_id = _id_value(view.ViewTemplateId)
                except Exception:
                    pass
            if not self.doc.IsFamilyDocument:
                try:
                    self.color_info = vm_colors.scan(self.doc)
                    if self.snap['supported']:
                        self.colors_applied = vm_colors.view_filters(
                            self.doc, view, set(self.color_info['own']))
                except Exception:
                    pass
        self.render()

    # ---------- запросы к Revit ----------
    def request(self, kind, **payload):
        if self.pending is not None or self.detached:
            return
        self.pending = (kind, self.doc, self.view_id, payload)
        self.set_busy(True)
        try:
            result = self.host.event.Raise()
        except Exception as exc:
            self.pending = None
            self.set_busy(False)
            self.status(u'Ошибка: {0}'.format(unicode(exc)))
            return
        if result not in (ExternalEventRequest.Accepted, ExternalEventRequest.Pending):
            self.pending = None
            self.set_busy(False)
            self.status(u'Revit занят. Повторите действие.')

    def execute(self, uiapp):
        req, self.pending = self.pending, None
        try:
            if req is None:
                return
            kind, doc, view_id, p = req
            if kind == 'refresh':
                self.refresh(uiapp)
                return
            uidoc = uiapp.ActiveUIDocument
            if (uidoc is None or doc is None or not uidoc.Document.Equals(doc)
                    or view_id is None or _id_value(uidoc.ActiveView.Id) != _id_value(view_id)):
                self.refresh(uiapp)
                self.status(u'Вид изменился. Повторите действие на нужном виде.')
                return
            allow = self.profile['allow_template']
            if kind == 'colors_apply':
                applied, skipped = vm_colors.apply(doc, uidoc.ActiveView, self.profile['colors'],
                                                   allow, u'цвета по рабочим наборам')
                self.refresh(uiapp)
                msg = u'Цвета применены: фильтров схемы на виде {0}.'.format(applied)
                if skipped:
                    msg += u' Пропущено РБ, которых нет в модели: {0}.'.format(skipped)
                self.status(msg)
                return
            if kind in ('colors_toggle', 'colors_update'):
                item = self.profile['colors']['items'][p['index']]
                if kind == 'colors_toggle':
                    done = vm_colors.set_enabled(
                        doc, uidoc.ActiveView, p['fname'], p['on'], allow,
                        u'{0} — {1}'.format(item['name'], u'On' if p['on'] else u'Off'))
                else:
                    done = vm_colors.update_one(doc, uidoc.ActiveView, p['fname'], item, allow,
                                                u'цвет {0}'.format(item['name']))
                self.refresh(uiapp)
                if not done:
                    self.status(u'Фильтра «{0}» на виде ещё нет — изменение сохранено в схеме, '
                                u'на вид попадёт по «Применить».'.format(item['name']))
                else:
                    self.status(u'Готово: {0}.'.format(item['name']))
                return
            if kind == 'tpl_export':
                saved = vm_templates.export(uiapp, doc, p['ids'], self.profile['templates_folder'])
                self.render_templates()
                self.status(u'Экспортировано шаблонов: {0} → {1}'.format(
                    len(saved), self.profile['templates_folder']))
                return
            if kind == 'tpl_import':
                report = vm_templates.import_files(uiapp, doc, p['paths'], p['mode'],
                                                   u'импорт шаблонов вида')
                self.refresh(uiapp)
                self.status(u'Импортировано: ' + u'; '.join(
                    u'{0} ({1})'.format(n, how) for n, how in report))
                return
            if kind == 'colors_clear':
                removed = vm_colors.clear(doc, uidoc.ActiveView, allow, u'снять цвета по РБ')
                self.refresh(uiapp)
                self.status(u'Снято фильтров: {0}.'.format(removed) if removed
                            else u'Фильтров 3_ВСЕ_РБ_ на виде нет.')
                return
            if kind == 'worksets':
                changed, missing = vm_revit.set_worksets(
                    doc, uidoc.ActiveView, p['names'], p['show'], allow, p['label'])
                extra = u' Нет в модели: {0}.'.format(missing) if missing else u''
            else:
                changed, skipped = vm_revit.set_categories(
                    doc, uidoc.ActiveView, p['keys'], p['show'], allow, p['label'])
                extra = u' Недоступно на этом виде: {0}.'.format(skipped) if skipped else u''
            self.refresh(uiapp)
            if changed:
                msg = (u'Включено' if p['show'] else u'Выключено') + u': {0}.'.format(changed)
            else:
                msg = u'Без изменений.'
            self.status(msg + extra)
        except vm_revit.ViewManagerError as exc:
            self._safe_refresh(uiapp)
            self.status(unicode(exc))
        except Exception as exc:
            self._safe_refresh(uiapp)
            self.status(u'Ошибка: {0}'.format(unicode(exc)))
        finally:
            self.set_busy(False)

    def _safe_refresh(self, uiapp):
        try:
            self.refresh(uiapp)
        except Exception:
            pass

    # ---------- состояние ----------
    def status(self, text):
        self.status_label.Text = text

    def can_edit(self, scope):
        s = self.snap
        if not s or not s['supported'] or self.busy:
            return False
        if scope == 'worksets' and not s['workshared']:
            return False
        return not s['blocked'].get(scope, False)

    def set_busy(self, busy):
        self.busy = busy
        self.update_enabled()

    def update_enabled(self):
        ws = self.can_edit('worksets')
        for ctl in (self.all_on_button, self.all_off_button, self.groups_panel, self.worksets_panel):
            ctl.IsEnabled = ws
        for kind in KINDS:
            ok = self.can_edit(kind)
            for suffix in ('_panel', '_on_button', '_off_button'):
                getattr(self, kind + suffix).IsEnabled = ok
        any_cat = self.can_edit('model') or self.can_edit('annotation')
        self.cat_all_on_button.IsEnabled = any_cat
        self.cat_all_off_button.IsEnabled = any_cat
        colors_ok = self.can_edit('filters')
        self.colors_apply_button.IsEnabled = colors_ok
        self.colors_clear_button.IsEnabled = colors_ok
        self.colors_take_button.IsEnabled = not self.busy
        for ctl in (self.groups_button, self.cat_setup_button, self.colors_setup_button,
                    self.allow_template_box, self.save_button, self.load_button):
            ctl.IsEnabled = not self.busy
        # кнопки вкладки «Шаблоны» тоже зависят от «занято» — иначе после действия
        # они оставались серыми (включались до снятия флага)
        self.update_tpl_buttons()

    # ---------- отрисовка ----------
    def render(self):
        self.render_header()
        self.render_ws_area()
        self.render_categories()
        self.render_colors()
        self.render_templates()
        self.update_enabled()

    def current_scopes(self):
        if self.tabs.SelectedIndex == 1:
            return ('model', 'annotation')
        if self.tabs.SelectedIndex == 2:
            return ('filters',)
        return ('worksets',)

    def render_header(self):
        s = self.snap
        inlines = self.target_label.Inlines
        inlines.Clear()
        info = self.view_label.Inlines
        info.Clear()
        if s is None:
            self._field(u'Модель', u'не открыта')
            return
        self._field(u'Модель', s['doc_title'])
        self._field(u'Вид', s['view_name'])
        t = (s['targets'] or {}).get('worksets') if s['supported'] else None
        if t and t['has_template']:
            self._field(u'Шаблон', u'{0} (видов: {1})'.format(t['name'], t['uses']))
        elif s['supported']:
            self._field(u'Шаблон', u'нет')
        if not s['supported']:
            self._line(u'Откройте план, разрез, фасад или 3D-вид проекта.', TEXT_GRAY)
            return
        scopes = self.current_scopes()
        if scopes == ('worksets',) and not s['workshared']:
            self._line(u'В модели нет рабочих наборов.', TEXT_GRAY)
            return
        text, brush = self.target_text(scopes)
        self._line(text, brush)

    def _field(self, caption, value):
        """Строка «Подпись: значение» в блоке информации внизу панели."""
        info = self.view_label.Inlines
        if info.Count:
            info.Add(LineBreak())
        label = Run(caption + u': ')
        label.Foreground = TEXT_GRAY
        info.Add(label)
        text = Run(value)
        text.FontWeight = FontWeights.SemiBold
        info.Add(text)

    def _line(self, text, brush):
        inlines = self.target_label.Inlines
        if inlines.Count:
            inlines.Add(LineBreak())
        run = Run(text)
        run.Foreground = brush
        inlines.Add(run)

    def target_text(self, scopes):
        """Одна строка: куда пишутся изменения на текущей вкладке."""
        infos = [self.snap['targets'][s] for s in scopes]
        first = infos[0]
        if not first['has_template']:
            return u'Изменения вносятся в вид.', TEXT_GRAY
        controlled = [i for i in infos if i['controlled']]
        if not controlled:
            return u'Шаблон этим не управляет — изменения вносятся в вид.', TEXT_GRAY
        if any(self.snap['blocked'][s] for s in scopes):
            return (u'Шаблон управляет этим. Чтобы менять, поставьте галочку '
                    u'«Разрешаю менять шаблон вида».'), TEXT_RED
        return u'Изменения вносятся в шаблон — коснутся всех его видов.', TEXT_ORANGE

    # ---------- плитки ----------
    def build_legend(self):
        for state, caption in LEGEND:
            bg, _, border = COLORS[state]
            swatch = Border()
            swatch.Width = swatch.Height = 11
            swatch.Background = bg
            swatch.BorderBrush = border
            swatch.BorderThickness = Thickness(1)
            swatch.CornerRadius = CornerRadius(2)
            swatch.Margin = Thickness(0, 0, 4, 0)
            swatch.VerticalAlignment = VerticalAlignment.Center
            label = _text(caption, gray=True)
            label.FontSize = 11
            pair = StackPanel()
            pair.Orientation = Orientation.Horizontal
            pair.Margin = Thickness(0, 0, 10, 2)
            pair.Children.Add(swatch)
            pair.Children.Add(label)
            self.legend_panel.Children.Add(pair)   # пара «цвет + подпись» не разрывается

    def on_ws_resize(self, sender, args):
        self._resize('ws', sender.ActualWidth)
        self._resize('wsx', sender.ActualWidth - 22)   # подгруппы — внутри Expander

    def on_cat_resize(self, sender, args):
        self._resize('cat', sender.ActualWidth - 22)   # отступ содержимого Expander

    def _resize(self, area, width):
        if width <= 0:
            return
        columns = max(1, int(width // TILE_MIN_WIDTH))
        self.widths[area] = width / columns
        for wrap in self.wraps[area]:
            wrap.ItemWidth = self.widths[area]

    def new_wrap(self, area, wrap=None):
        if wrap is None:
            wrap = WrapPanel()
        wrap.ItemWidth = self.widths[area]
        self.wraps[area].append(wrap)
        return wrap

    def tile(self, text, state, tooltip, tag, handler):
        bg, fg, border = COLORS[state]
        b = Button()
        b.Style = self.tile_style
        b.Background = bg
        b.BorderBrush = border
        label = _text(text)
        label.Foreground = fg
        _fit_font(label, text)
        b.Content = label
        b.ToolTip = tooltip
        b.Tag = tag
        b.Click += handler
        return b

    # ---------- вкладка «Рабочие наборы» ----------
    def visibility(self):
        if not self.snap:
            return {}
        return dict((w['name'], w['visible']) for w in self.snap['worksets'])

    def render_ws_area(self):
        self.wraps['ws'] = []
        self.wraps['wsx'] = []
        self.render_groups()
        self.render_worksets()

    def render_groups(self):
        panel = self.groups_panel
        panel.Children.Clear()
        groups = self.profile['groups']
        if not groups:
            panel.ItemWidth = float('nan')
            panel.Children.Add(_text(u'Групп пока нет — нажмите ⚙ справа.', gray=True))
            return
        self.new_wrap('ws', panel)
        vis = self.visibility()
        for index, group in enumerate(groups):
            # None — вид не поддерживается (лист и т.п.): видимость неизвестна
            states = [vis[n] for n in group['worksets'] if vis.get(n) is not None]
            if not states:
                state = 'none'
            elif all(states):
                state = 'on'
            elif not any(states):
                state = 'off'
            else:
                state = 'part'
            lines = [(n if n in vis else n + u'  — нет в модели') for n in group['worksets']]
            tip = u'\n'.join(lines) if lines else u'В группе нет рабочих наборов.'
            text = u'{0}  ({1})'.format(group['name'], len(group['worksets']))
            panel.Children.Add(self.tile(text, state, tip, (index, state), self.on_group_tile))

    def ws_sections(self):
        """[(подгруппа или None, скрыта ли, [имена РБ])] по настройке «Подгруппы»."""
        sections = self.profile['sections']
        names = [w['name'] for w in self.snap['worksets']] if self.snap else []
        out = []
        for index, part in vm_profile.split_sections(names, sections):
            section = sections[index] if index is not None else None
            out.append((section, bool(section and section['hidden']), part))
        return out

    def render_worksets(self):
        panel = self.worksets_panel
        panel.Children.Clear()
        self.hidden_sections_label.Text = u''
        if not self.snap or not self.snap['worksets']:
            return
        query = self.search_box.Text
        by_name = dict((w['name'], w) for w in self.snap['worksets'])
        naming = self.profile['workset_names']
        hidden = []
        for section, is_hidden, names in self.ws_sections():
            title = section['name'] if section else OTHER_SECTION
            if is_hidden:
                hidden.append(u'{0} ({1})'.format(title, len(names)))
                continue
            items = [by_name[n] for n in names if _matches(n, query)]
            if not items:
                continue
            known = [i for i in items if i['visible'] is not None]
            if known:
                count = u'{0}/{1}'.format(sum(1 for i in known if i['visible']), len(items))
            else:
                count = unicode(len(items))
            wrap = self.new_wrap('wsx')
            panel.Children.Add(self.section_expander(title, count, names, wrap, bool(query)))
            for item in items:
                if item['visible'] is None:
                    state = 'none'
                    tip = u'{0}\nОткройте план, разрез, фасад или 3D-вид, чтобы переключать.'.format(
                        item['name'])
                else:
                    state = 'on' if item['visible'] else 'off'
                    tip = item['name']
                short = vm_profile.short_workset_name(item['name'], naming)
                wrap.Children.Add(self.tile(short, state, tip,
                                            (item['name'], item['visible']), self.on_workset_tile))
        if hidden:
            self.hidden_sections_label.Text = (u'Скрытые подгруппы: {0}. Показать — ⚙ → «Подгруппы».'
                                               .format(u', '.join(hidden)))

    def section_expander(self, title, count, names, wrap, searching):
        """Подгруппа РБ на панели: сворачивается стрелкой (как разделы «Категорий»),
        в заголовке — On/Off для всех её РБ. Свёрнутость запоминается в профиле."""
        exp = Expander()
        exp.Margin = Thickness(0, 2, 0, 4)
        collapsed = title in self.profile['collapsed']
        exp.IsExpanded = searching or not collapsed   # при поиске показываем найденное
        head = StackPanel()
        head.Orientation = Orientation.Horizontal
        label = _text(u'{0}   {1}'.format(title, count))
        label.FontWeight = FontWeights.Bold
        label.Margin = Thickness(0, 0, 10, 0)
        head.Children.Add(label)
        for caption, show in ((u'On', True), (u'Off', False)):
            b = Button()
            b.Content = caption
            b.Padding = Thickness(7, 0, 7, 0)
            b.FontSize = 11
            b.Margin = Thickness(0, 0, 3, 0)
            b.ToolTip = u'{0} все РБ подгруппы «{1}»'.format(
                u'Показать' if show else u'Скрыть', title)
            b.Click += self._section_handler(title, names, show)
            head.Children.Add(b)
        exp.Header = head
        wrap.Margin = Thickness(0, 2, 0, 0)
        exp.Content = wrap
        exp.Expanded += self._collapse_handler(title, False)
        exp.Collapsed += self._collapse_handler(title, True)
        return exp

    def _section_handler(self, title, names, show):
        def handler(sender, args):
            args.Handled = True   # не сворачивать подгруппу
            self.set_worksets(names, show, u'{0} — {1}'.format(title, u'On' if show else u'Off'))
        return handler

    def _collapse_handler(self, title, collapsed):
        def handler(sender, args):
            if not sender.Equals(args.OriginalSource) or self.search_box.Text:
                return
            names = self.profile['collapsed']
            if collapsed and title not in names:
                names.append(title)
            elif not collapsed and title in names:
                names.remove(title)
            else:
                return
            self.save_profile()
        return handler

    def all_names(self):
        return [w['name'] for w in self.snap['worksets']] if self.snap else []

    def shown_names(self):
        """РБ видимых подгрупп — на них действуют All On / All Off."""
        out = []
        for _, is_hidden, names in self.ws_sections():
            if not is_hidden:
                out.extend(names)
        return out

    def set_worksets(self, names, show, label):
        self.request('worksets', names=list(names), show=show, label=label)

    def on_all_on(self, sender, args):
        self.set_worksets(self.shown_names(), True, u'все рабочие наборы — On')

    def on_all_off(self, sender, args):
        self.set_worksets(self.shown_names(), False, u'все рабочие наборы — Off')

    def on_group_tile(self, sender, args):
        index, state = sender.Tag
        group = self.profile['groups'][index]
        if state == 'none':
            self.status(u'Рабочих наборов группы «{0}» нет в этой модели.'.format(group['name']))
            return
        show = state != 'on'   # частично или выключено -> включить; включено -> выключить
        self.set_worksets(group['worksets'], show,
                          u'{0} — {1}'.format(group['name'], u'On' if show else u'Off'))

    def on_workset_tile(self, sender, args):
        name, visible = sender.Tag
        show = not visible
        self.set_worksets([name], show, u'{0} — {1}'.format(name, u'On' if show else u'Off'))

    def on_search(self, sender, args):
        self.render_ws_area()
        self.update_enabled()

    def on_edit_groups(self, sender, args):
        editor = GroupEditor(self.all_names(), self.profile['groups'],
                             self.profile['workset_names'], self.profile['sections'],
                             self.uiapp.MainWindowHandle)
        result = editor.show()
        if result is None:
            return
        self.profile['groups'], self.profile['workset_names'], self.profile['sections'] = result
        self.save_profile()
        self.render_ws_area()
        self.update_enabled()
        self.status(u'Настройки рабочих наборов сохранены.')

    # ---------- вкладка «Категории» ----------
    def render_categories(self):
        self.wraps['cat'] = []
        for kind in KINDS:
            panel = getattr(self, kind + '_panel')
            header = getattr(self, kind + '_header')
            panel.Children.Clear()
            items = self.snap['categories'][kind] if self.snap else []
            keys = self.profile['categories'][kind]
            on = sum(1 for i in items if i['visible'])
            header.Text = u'{0}   {1}/{2}'.format(KIND_TITLES[kind], on, len(keys))
            if not keys:
                panel.ItemWidth = float('nan')
                panel.Children.Add(_text(u'Не выбрано — нажмите ⚙ вверху справа.', gray=True))
                continue
            self.new_wrap('cat', panel)
            if not items:   # нет модели / неподходящий вид — показываем по профилю
                names = dict(self.snap['catalog'][kind]) if self.snap else {}
                items = [{'key': k, 'name': names.get(k, k), 'exists': True, 'available': False,
                          'visible': False} for k in keys]
            for item in items:
                if not item['exists']:
                    state, tip = 'none', u'{0}\nНет такой категории в этой модели.'.format(item['name'])
                elif not item['available']:
                    state, tip = 'none', u'{0}\nКатегорию нельзя скрыть на этом виде.'.format(item['name'])
                else:
                    state, tip = ('on' if item['visible'] else 'off'), item['name']
                panel.Children.Add(self.tile(short_category_name(item['name']), state, tip,
                                             (kind, item['key'], item['visible'], state, item['name']),
                                             self.on_category_tile))

    def set_categories(self, keys_by_kind, show, label):
        self.request('categories', keys=keys_by_kind, show=show, label=label)

    def on_category_tile(self, sender, args):
        kind, key, visible, state, name = sender.Tag
        if state == 'none':
            self.status(sender.ToolTip)
            return
        show = not visible
        self.set_categories({kind: [key]}, show, u'категория «{0}» — {1}'.format(
            name, u'On' if show else u'Off'))

    def _all_categories(self, show, kinds=KINDS):
        keys = dict((kind, list(self.profile['categories'][kind])) for kind in kinds
                    if self.can_edit(kind))
        if not any(keys.values()):
            self.status(u'Нет быстрых категорий, которые можно изменить на этом виде.')
            return
        what = (u'все быстрые категории' if len(kinds) > 1
                else u'быстрые категории: {0}'.format(KIND_TITLES[kinds[0]].lower()))
        self.set_categories(keys, show, u'{0} — {1}'.format(what, u'On' if show else u'Off'))

    def on_cat_all_on(self, sender, args):
        self._all_categories(True)

    def on_cat_all_off(self, sender, args):
        self._all_categories(False)

    def _kind_handler(self, kind, show):
        """On/Off в заголовке раздела «Элементы» или «Аннотации»."""
        def handler(sender, args):
            args.Handled = True   # не сворачивать раздел
            self._all_categories(show, (kind,))
        return handler

    def on_cat_setup(self, sender, args):
        catalog = self.snap['catalog'] if self.snap else {'model': [], 'annotation': []}
        if not catalog['model'] and not catalog['annotation']:
            self.status(u'Откройте модель проекта, чтобы выбрать категории.')
            return
        editor = CategoryEditor(catalog, self.profile['categories'], self.uiapp.MainWindowHandle)
        result = editor.show()
        if result is None:
            return
        self.profile['categories'] = result
        self.save_profile()
        self.status(u'Быстрые категории сохранены.')
        self.request('refresh')

    # ---------- вкладка «Цвета» ----------
    def color_rows(self):
        """Строки вкладки: сначала фильтры, которые реально стоят на виде (их настройки —
        правда), потом РБ из схемы, которых на виде ещё нет."""
        info = self.color_info
        shared = vm_colors.shared_names(info) if info else set()
        present = vm_colors.present_names(info) if info else None
        rows, seen = [], set()
        for f in (self.colors_applied or []):
            rows.append(dict(f, on_view=True))
            seen.add(f['filter'])
        for it in self.profile['colors']['items']:
            if not it['enabled']:
                continue
            if present is not None and it['name'] not in present[it['scope']]:
                continue   # РБ нет в этой модели — показываем отдельной строкой внизу
            fname = vm_colors.filter_name(it, shared)
            if fname in seen:
                continue
            rows.append({'filter': fname, 'name': it['name'], 'scope': it['scope'],
                         'enabled': False, 'color': it['color'],
                         'transparency': it['transparency'], 'on_view': False})
        return rows

    def render_colors(self):
        panel = self.colors_panel
        panel.Children.Clear()
        info = self.color_info
        own = set(info['own']) if info else set()
        linked = set()
        if info:
            for link in info['links']:
                linked.update(link['worksets'])
        rows = self.color_rows()
        on_view = sum(1 for r in rows if r['on_view'])
        self.colors_state_label.Text = (
            u'На виде фильтров 3_ВСЕ_РБ_: {0}, ещё в схеме: {1}. Плитка — показать/скрыть '
            u'элементы, квадрат — цвет, ползунок — прозрачность.'.format(on_view, len(rows) - on_view))
        if not rows:
            panel.Children.Add(_text(u'На виде нет фильтров 3_ВСЕ_РБ_, схема пуста — нажмите ⚙.',
                                     gray=True))
        for scope, title, present in (('own', u'Наша модель', own), ('link', u'В связях', linked)):
            part = [r for r in rows if r['scope'] == scope]
            if not part:
                continue
            header = _text(title)
            header.FontWeight = FontWeights.Bold
            header.Margin = Thickness(0, 4, 0, 2)
            panel.Children.Add(header)
            for row in part:
                panel.Children.Add(self.color_row(row, present))
        self.render_missing_colors()

    def missing_colors(self):
        if not self.color_info:
            return []
        items = [it for it in self.profile['colors']['items'] if it['enabled']]
        return vm_colors.missing_items(self.color_info, items)

    def render_missing_colors(self):
        missing = self.missing_colors()
        if not missing:
            return
        box = StackPanel()
        box.Margin = Thickness(0, 10, 0, 0)
        names = [u'{0}{1}'.format(it['name'], u'' if it['scope'] == 'own' else u' (связи)')
                 for it in missing]
        note = _text(u'Нет в этой модели, не применяются: {0}.'.format(u', '.join(names)), gray=True)
        note.TextWrapping = TextWrapping.Wrap
        note.TextTrimming = getattr(TextTrimming, 'None')   # .None — синтаксическая ошибка в IronPython
        note.FontSize = 11
        box.Children.Add(note)
        button = Button()
        button.Content = u'Убрать их из схемы'
        button.HorizontalAlignment = HorizontalAlignment.Left
        button.Margin = Thickness(0, 4, 0, 0)
        button.ToolTip = u'Удалить эти РБ из вашей схемы цветов (схема общая для всех проектов)'
        button.Click += self.on_colors_drop_missing
        box.Children.Add(button)
        self.colors_panel.Children.Add(box)

    def on_colors_drop_missing(self, sender, args):
        missing = self.missing_colors()
        if not missing:
            return
        answer = MessageBox.Show(
            u'Убрать из схемы цветов: {0}?\n\nСхема общая для всех проектов — в других моделях, '
            u'где эти РБ есть, их придётся настроить заново.'.format(
                u', '.join(it['name'] for it in missing)),
            u'View Manager', MessageBoxButton.YesNo, MessageBoxImage.Question)
        if answer != MessageBoxResult.Yes:
            return
        drop = set((it['scope'], it['name']) for it in missing)
        self.profile['colors']['items'] = [it for it in self.profile['colors']['items']
                                           if (it['scope'], it['name']) not in drop]
        self.save_profile()
        self.render_colors()
        self.status(u'Убрано из схемы: {0}.'.format(len(drop)))

    def color_row(self, row, present):
        grid = Grid()
        grid.Margin = Thickness(0, 1, 0, 1)
        for width in (GridLength(1, GridUnitType.Star), GridLength(30), GridLength(70),
                      GridLength(38)):
            col = ColumnDefinition()
            col.Width = width
            grid.ColumnDefinitions.Add(col)
        caption = vm_profile.short_workset_name(row['name'], self.profile['workset_names'])
        if not row['on_view']:
            state = 'none'
            tip = u'{0}\nФильтра на виде нет — нажмите «Применить».'.format(row['name'])
            if self.color_info and row['name'] not in present:
                tip = u'{0}\nТакого РБ нет в этой модели.'.format(row['name'])
        else:
            state = 'on' if row['enabled'] else 'off'
            tip = u'{0}\nЭлементы {1}. Нажмите, чтобы {2}. Фильтр: {3}.'.format(
                row['name'], u'видны' if row['enabled'] else u'скрыты',
                u'скрыть' if row['enabled'] else u'показать', row['filter'])
        tile = self.tile(caption, state, tip, row, self.on_color_tile)
        color = row['color'] or [200, 200, 200]
        swatch = Button()
        swatch.Style = self.tile_style
        swatch.Background = SolidColorBrush(Color.FromRgb(*color))
        swatch.BorderBrush = Brushes.Gray
        swatch.Margin = Thickness(4, 2, 2, 2)
        swatch.ToolTip = u'Цвет — нажмите, чтобы изменить'
        swatch.Tag = row
        swatch.Click += self.on_color_swatch
        slider = Slider()
        slider.Minimum = 0
        slider.Maximum = 100
        slider.TickFrequency = 5
        slider.IsSnapToTickEnabled = True
        slider.Value = row['transparency']
        slider.VerticalAlignment = VerticalAlignment.Center
        slider.ToolTip = u'Прозрачность'
        slider.Tag = row
        value = _text(u'{0}%'.format(row['transparency']))
        value.FontSize = 11
        value.Margin = Thickness(4, 0, 0, 0)
        slider.ValueChanged += self._slider_label(value)
        slider.PreviewMouseUp += self.on_color_slider
        for col, ctl in enumerate((tile, swatch, slider, value)):
            Grid.SetColumn(ctl, col)
            grid.Children.Add(ctl)
        return grid

    @staticmethod
    def _slider_label(label):
        def handler(sender, args):
            label.Text = u'{0}%'.format(int(round(sender.Value)))
        return handler

    def ensure_item(self, row):
        """Найти (или завести) запись схемы для строки; настройки берутся из строки,
        то есть с вида — вид главнее. Возвращает индекс записи в профиле."""
        items = self.profile['colors']['items']
        index = None
        for i, it in enumerate(items):
            if it['scope'] == row['scope'] and it['name'] == row['name']:
                index = i
                break
        if index is None:
            items.append({'name': row['name'], 'scope': row['scope'], 'enabled': True,
                          'color': [128, 128, 128], 'transparency': 0})
            index = len(items) - 1
        it = items[index]
        it['enabled'] = True
        if row['color']:
            it['color'] = list(row['color'])
        it['transparency'] = row['transparency']
        return index

    def on_color_tile(self, sender, args):
        row = sender.Tag
        if not row['on_view']:
            self.status(unicode(sender.ToolTip).replace(u'\n', u' '))
            return
        index = self.ensure_item(row)
        self.save_profile()
        self.request('colors_toggle', index=index, fname=row['filter'], on=not row['enabled'])

    def on_color_swatch(self, sender, args):
        row = sender.Tag
        rgb = pick_color(row['color'] or [200, 200, 200])
        if rgb is None:
            return
        index = self.ensure_item(row)
        self.profile['colors']['items'][index]['color'] = rgb
        self.save_profile()
        self.render_colors()
        self.request('colors_update', index=index, fname=row['filter'])

    def on_color_slider(self, sender, args):
        row = sender.Tag
        value = int(round(sender.Value))
        if value == row['transparency']:
            return
        index = self.ensure_item(row)
        self.profile['colors']['items'][index]['transparency'] = value
        self.save_profile()
        self.request('colors_update', index=index, fname=row['filter'])

    def on_colors_take(self, sender, args):
        rows = [r for r in self.color_rows() if r['on_view']]
        if not rows:
            self.status(u'На виде нет фильтров 3_ВСЕ_РБ_ — забирать нечего.')
            return
        for row in rows:
            self.ensure_item(row)
        self.save_profile()
        self.render_colors()
        self.status(u'В схему взято с вида: {0} РБ. Теперь их можно применить в другой модели.'
                    .format(len(rows)))

    def on_colors_setup(self, sender, args):
        info = self.color_info
        if not info or (not info['own'] and not info['links']):
            self.status(u'Откройте модель с рабочими наборами или связями.')
            return
        editor = ColorEditor(info, self.profile['colors'], self.uiapp.MainWindowHandle)
        result = editor.show()
        if result is None:
            return
        self.profile['colors'] = result
        self.save_profile()
        self.render_colors()
        self.status(u'Схема цветов сохранена. Нажмите «Применить», чтобы перенести её на вид.')

    def on_colors_apply(self, sender, args):
        if not any(i['enabled'] for i in self.profile['colors']['items']):
            self.status(u'Схема пуста — сначала настройте цвета (⚙).')
            return
        enabled = [i for i in self.profile['colors']['items'] if i['enabled']]
        if self.color_info and len(self.missing_colors()) == len(enabled):
            self.status(u'Ни одного РБ из схемы нет в этой модели — настройте цвета (⚙).')
            return
        self.request('colors_apply')

    def on_colors_clear(self, sender, args):
        self.request('colors_clear')

    # ---------- вкладка «Шаблоны» ----------
    def render_templates(self):
        folder = self.profile['templates_folder']
        self.tpl_folder_label.Text = folder or u'Папка не выбрана — нажмите «Папка…»'
        query = self.tpl_search_box.Text
        self.tpl_model_list.Items.Clear()
        for t in self.templates:
            if not _matches(t['name'], query):
                continue
            item = _text(t['name'])
            item.Tag = t['id']
            item.ToolTip = u'{0}\nТип вида: {1}'.format(t['name'], t['type'])
            if t['id'] == self.view_template_id:
                item.FontWeight = FontWeights.Bold
                item.ToolTip += u'\nШаблон текущего вида'
            self.tpl_model_list.Items.Add(item)
        self.tpl_library_list.Items.Clear()
        in_model = set(t['name'] for t in self.templates)
        for f in vm_templates.library(folder):
            item = _text(f['name'] + (u'   · есть в модели' if f['name'] in in_model else u''))
            item.Tag = f['path']
            item.ToolTip = f['path']
            self.tpl_library_list.Items.Add(item)
        self.update_tpl_buttons()

    def update_tpl_buttons(self):
        has_folder = bool(self.profile['templates_folder'])
        self.tpl_export_current_button.IsEnabled = self.view_template_id is not None and not self.busy
        for ctl in (self.tpl_export_button, self.tpl_import_button, self.tpl_open_button,
                    self.tpl_rescan_button):
            ctl.IsEnabled = has_folder and not self.busy
        self.tpl_folder_button.IsEnabled = not self.busy

    def on_tpl_search(self, sender, args):
        self.render_templates()

    def on_tpl_rescan(self, sender, args):
        self.render_templates()

    def on_tpl_folder(self, sender, args):
        clr.AddReference('System.Windows.Forms')
        from System.Windows.Forms import DialogResult, FolderBrowserDialog
        dlg = FolderBrowserDialog()
        dlg.Description = u'Папка для шаблонов вида'
        if self.profile['templates_folder']:
            dlg.SelectedPath = self.profile['templates_folder']
        if dlg.ShowDialog() != DialogResult.OK:
            return
        self.profile['templates_folder'] = dlg.SelectedPath
        self.save_profile()
        self.render_templates()
        self.status(u'Папка шаблонов: {0}'.format(dlg.SelectedPath))

    def on_tpl_open(self, sender, args):
        folder = self.profile['templates_folder']
        if folder and os.path.isdir(folder):
            os.startfile(folder)

    def _need_folder(self):
        folder = self.profile['templates_folder']
        if not folder or not os.path.isdir(folder):
            self.status(u'Сначала выберите папку для шаблонов («Папка…»).')
            return False
        return True

    def _confirm_overwrite(self, names):
        folder = self.profile['templates_folder']
        exist = [n for n in names if os.path.exists(os.path.join(folder, vm_templates.file_name(n)))]
        if not exist:
            return True
        answer = MessageBox.Show(
            u'В папке уже есть: {0}.\nПерезаписать?'.format(u', '.join(exist)), u'View Manager',
            MessageBoxButton.YesNo, MessageBoxImage.Question)
        return answer == MessageBoxResult.Yes

    def on_tpl_export(self, sender, args):
        if not self._need_folder():
            return
        chosen = list(self.tpl_model_list.SelectedItems)
        if not chosen:
            self.status(u'Выберите шаблоны в списке «В модели».')
            return
        if not self._confirm_overwrite([c.Text for c in chosen]):
            return
        self.request('tpl_export', ids=[c.Tag for c in chosen])

    def on_tpl_export_current(self, sender, args):
        if not self._need_folder() or self.view_template_id is None:
            return
        name = [t['name'] for t in self.templates if t['id'] == self.view_template_id]
        if name and not self._confirm_overwrite(name):
            return
        self.request('tpl_export', ids=[self.view_template_id])

    def on_tpl_import(self, sender, args):
        chosen = list(self.tpl_library_list.SelectedItems)
        if not chosen:
            self.status(u'Выберите шаблоны в списке «В папке».')
            return
        in_model = set(t['name'] for t in self.templates)
        clash = [os.path.splitext(os.path.basename(c.Tag))[0] for c in chosen]
        clash = [n for n in clash if n in in_model]
        mode = 'copy'
        if clash:
            answer = MessageBox.Show(
                u'В модели уже есть: {0}.\n\nДа — заменить (виды с этим шаблоном переключатся на '
                u'новый).\nНет — добавить копией «… (импорт)».\nОтмена — ничего не делать.'
                .format(u', '.join(clash)), u'View Manager',
                MessageBoxButton.YesNoCancel, MessageBoxImage.Question)
            if answer == MessageBoxResult.Cancel:
                return
            mode = 'replace' if answer == MessageBoxResult.Yes else 'copy'
        self.request('tpl_import', paths=[c.Tag for c in chosen], mode=mode)

    # ---------- общее ----------
    def on_tab(self, sender, args):
        if sender.Equals(args.OriginalSource):
            self.render_header()

    def on_allow_template(self, sender, args):
        self.profile['allow_template'] = bool(self.allow_template_box.IsChecked)
        self.save_profile()
        self.request('refresh')

    def save_profile(self):
        try:
            vm_profile.save(self.profile)
        except Exception as exc:
            self.status(u'Не удалось сохранить настройки: {0}'.format(unicode(exc)))

    def on_save_profile(self, sender, args):
        dlg = SaveFileDialog()
        dlg.Title = u'Сохранить профиль View Manager'
        dlg.Filter = u'Профиль View Manager (*.json)|*.json'
        dlg.FileName = u'ViewManager_профиль.json'
        if dlg.ShowDialog() != True:
            return
        try:
            vm_profile.write_file(dlg.FileName, self.profile)
            self.status(u'Профиль сохранён: {0}'.format(dlg.FileName))
        except Exception as exc:
            self.status(u'Не удалось сохранить профиль: {0}'.format(unicode(exc)))

    def on_load_profile(self, sender, args):
        dlg = OpenFileDialog()
        dlg.Title = u'Загрузить профиль View Manager'
        dlg.Filter = u'Профиль View Manager (*.json)|*.json|Все файлы (*.*)|*.*'
        if dlg.ShowDialog() != True:
            return
        try:
            self.profile = vm_profile.read_file(dlg.FileName)
        except Exception as exc:
            self.status(u'Не удалось загрузить профиль: {0}'.format(unicode(exc)))
            return
        self.save_profile()
        self.allow_template_box.IsChecked = self.profile['allow_template']
        self.status(u'Профиль загружен: {0}'.format(dlg.FileName))
        self.request('refresh')


class CategoryEditor(object):
    """Модальное окно выбора быстрых категорий: вкладки «Элементы» и «Аннотации»."""

    def __init__(self, catalog, chosen, owner_hwnd):
        self.win = _load_xaml('vm_categories.xaml')
        self.tabs = self.win.FindName('tabs')
        self.win.FindName('ok_button').Click += self.on_ok
        _owner_window(self.win, owner_hwnd)
        self.catalog = catalog
        self.chosen = dict((kind, list(chosen.get(kind, []))) for kind in KINDS)
        self.result = None
        self.parts = {}
        for kind in KINDS:
            self.tabs.Items.Add(self.build_tab(kind))
            self.render(kind)

    @staticmethod
    def _handler(func, *bound):
        """Обработчик события WPF (sender, args), вызывающий func(*bound)."""
        def handler(sender, args):
            func(*bound)
        return handler

    def build_tab(self, kind):
        card = Border()
        card.Style = self.win.FindResource('Card')
        dock = DockPanel()
        top = StackPanel()
        DockPanel.SetDock(top, Dock.Top)
        search = TextBox()
        search.Margin = Thickness(0, 0, 0, 8)
        search.ToolTip = u'Поиск категории'
        search.TextChanged += self._handler(self.render, kind)
        buttons = DockPanel()
        buttons.Margin = Thickness(0, 0, 0, 8)
        count = _text(u'', gray=True)
        DockPanel.SetDock(count, Dock.Right)
        buttons.Children.Add(count)
        row = StackPanel()
        row.Orientation = Orientation.Horizontal
        for caption, value in ((u'Отметить найденные', True), (u'Снять с найденных', False)):
            b = Button()
            b.Content = caption
            b.Margin = Thickness(0, 0, 6, 0)
            b.Click += self._handler(self.set_shown, kind, value)
            row.Children.Add(b)
        buttons.Children.Add(row)
        top.Children.Add(search)
        top.Children.Add(buttons)
        dock.Children.Add(top)
        scroll = ScrollViewer()
        scroll.VerticalScrollBarVisibility = ScrollBarVisibility.Auto
        scroll.HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled
        listing = StackPanel()
        listing.Margin = Thickness(2)
        scroll.Content = listing
        dock.Children.Add(scroll)
        card.Child = dock
        tab = TabItem()
        tab.Header = KIND_TITLES[kind]
        tab.Content = card
        self.parts[kind] = {'search': search, 'list': listing, 'count': count}
        return tab

    def render(self, kind):
        part = self.parts[kind]
        listing = part['list']
        listing.Children.Clear()
        chosen = self.chosen[kind]
        known = [k for k, _ in self.catalog[kind]]
        rows = list(self.catalog[kind]) + [(k, k) for k in chosen if k not in known]
        query = part['search'].Text
        for key, name in rows:
            if not _matches(name, query):
                continue
            cb = CheckBox()
            cb.Tag = (kind, key)
            cb.IsChecked = key in chosen
            cb.Margin = Thickness(0, 2, 0, 2)
            cb.Content = _text(name if key in known else name + u'  — нет в этой модели',
                               gray=key not in known)
            cb.Click += self.on_check
            listing.Children.Add(cb)
        self.update_count(kind)

    def update_count(self, kind):
        self.parts[kind]['count'].Text = u'выбрано: {0}'.format(len(self.chosen[kind]))

    def set_one(self, kind, key, on):
        chosen = self.chosen[kind]
        if on and key not in chosen:
            chosen.append(key)
        elif not on and key in chosen:
            chosen.remove(key)

    def on_check(self, sender, args):
        kind, key = sender.Tag
        self.set_one(kind, key, bool(sender.IsChecked))
        self.update_count(kind)

    def set_shown(self, kind, on):
        for cb in self.parts[kind]['list'].Children:
            cb.IsChecked = on
            self.set_one(kind, cb.Tag[1], on)
        self.update_count(kind)

    def show(self):
        self.win.ShowDialog()
        return self.result

    def on_ok(self, sender, args):
        for kind in KINDS:
            order = dict((k, i) for i, (k, _) in enumerate(self.catalog[kind]))
            self.chosen[kind].sort(key=lambda k: (order.get(k, len(order)), k))
        self.result = self.chosen
        self.win.DialogResult = True


PALETTE = ((231, 76, 60), (52, 152, 219), (46, 204, 113), (243, 156, 18), (155, 89, 182),
           (26, 188, 156), (233, 30, 99), (121, 85, 72), (0, 150, 136), (63, 81, 181),
           (205, 220, 57), (255, 87, 34))


def pick_color(rgb):
    """Стандартное окно выбора цвета Windows. Возвращает [r, g, b] или None."""
    clr.AddReference('System.Windows.Forms')
    clr.AddReference('System.Drawing')
    from System.Windows.Forms import ColorDialog, DialogResult
    from System.Drawing import Color as DColor
    dlg = ColorDialog()
    dlg.FullOpen = True
    dlg.Color = DColor.FromArgb(*rgb)
    if dlg.ShowDialog() != DialogResult.OK:
        return None
    return [int(dlg.Color.R), int(dlg.Color.G), int(dlg.Color.B)]


class ColorEditor(object):
    """Окно «Цвета по рабочим наборам»: модели слева, РБ выбранной модели справа."""

    NAMES = ('sources_list', 'source_title', 'source_hint', 'search_box', 'all_on_button',
             'all_off_button', 'rows_panel', 'param_box', 'ok_button', 'show_all_box')
    PREFIX = u'2_'   # красим только рабочие наборы раздела (2_…), 0_ и 1_ — связи и служебные

    def __init__(self, info, colors, owner_hwnd):
        self.win = _load_xaml('vm_colors.xaml')
        for name in self.NAMES:
            setattr(self, name, self.win.FindName(name))
        _owner_window(self.win, owner_hwnd)
        self.info = info
        self.shared = vm_colors.shared_names(info)
        self.items = dict(((it['scope'], it['name']), copy.deepcopy(it)) for it in colors['items'])
        self.original = set(self.items.keys())
        self.param_box.Text = colors['param']
        self.result = None
        self.sources = [(u'Эта модель', 'own', info['own'])]
        for link in info['links']:
            self.sources.append((link['title'], 'link', link['worksets']))
        for _ in self.sources:
            self.sources_list.Items.Add(u'')
        self.update_source_counts()
        self.sources_list.SelectionChanged += self.on_source
        self.show_all_box.Click += self.on_show_all
        self.search_box.TextChanged += self.on_search
        self.all_on_button.Click += self._bulk(True)
        self.all_off_button.Click += self._bulk(False)
        self.ok_button.Click += self.on_ok
        self.sources_list.SelectedIndex = 0 if self.sources else -1

    def show(self):
        self.win.ShowDialog()
        return self.result

    def visible_names(self, names):
        if self.show_all_box.IsChecked:
            return names
        return [n for n in names if n.startswith(self.PREFIX)]

    def on_show_all(self, sender, args):
        self.update_source_counts()
        self.render()

    def current(self):
        i = self.sources_list.SelectedIndex
        if i < 0:
            return None
        title, scope, names = self.sources[i]
        return title, scope, self.visible_names(names)

    def item(self, scope, name, create=False):
        key = (scope, name)
        if key not in self.items and create:
            color = PALETTE[len(self.items) % len(PALETTE)]
            self.items[key] = {'name': name, 'scope': scope, 'enabled': False,
                               'color': list(color), 'transparency': 0}
        return self.items.get(key)

    def update_source_counts(self):
        index = self.sources_list.SelectedIndex
        for i, (title, scope, names) in enumerate(self.sources):
            names = self.visible_names(names)
            on = sum(1 for n in names if (self.items.get((scope, n)) or {}).get('enabled'))
            text = u'{0}   ({1}/{2})'.format(title, on, len(names))
            if self.sources_list.Items[i] != text:
                self.sources_list.SelectionChanged -= self.on_source
                self.sources_list.Items.RemoveAt(i)
                self.sources_list.Items.Insert(i, text)
                self.sources_list.SelectionChanged += self.on_source
        self.sources_list.SelectedIndex = index

    def on_source(self, sender, args):
        self.render()

    def on_search(self, sender, args):
        self.render()

    def render(self):
        self.rows_panel.Children.Clear()
        src = self.current()
        if src is None:
            return
        title, scope, names = src
        self.source_title.Text = title
        if scope == 'own':
            self.source_hint.Text = u'Рабочие наборы нашей модели.'
        else:
            self.source_hint.Text = (u'Рабочие наборы внутри связи. Цвет РБ общий для всех связей, '
                                     u'где есть РБ с таким именем.')
        if not names:
            self.rows_panel.Children.Add(_text(
                u'Нет рабочих наборов с префиксом 2_. Отметьте «Показать все РБ», если нужны другие.',
                gray=True))
            return
        for name in names:
            if _matches(name, self.search_box.Text):
                self.rows_panel.Children.Add(self.row(scope, name))

    def row(self, scope, name):
        it = self.item(scope, name, create=True)
        grid = Grid()
        grid.Margin = Thickness(0, 2, 0, 2)
        for width in (GridLength(1, GridUnitType.Star), GridLength(44), GridLength(130),
                      GridLength(40)):
            col = ColumnDefinition()
            col.Width = width
            grid.ColumnDefinitions.Add(col)
        cb = CheckBox()
        cb.VerticalAlignment = VerticalAlignment.Center
        label = StackPanel()
        label.Children.Add(_text(name))
        if name in self.shared:
            hint = _text(u'есть и у нас, и в связях — различается по «{0}»'.format(
                self.param_box.Text), gray=True)
            hint.FontSize = 10.5
            hint.Foreground = TEXT_ORANGE
            label.Children.Add(hint)
        cb.Content = label
        cb.IsChecked = it['enabled']
        cb.Click += self._toggle(it)
        swatch = Button()
        swatch.Width = 34
        swatch.Height = 20
        swatch.ToolTip = u'Выбрать цвет'
        swatch.Background = SolidColorBrush(Color.FromRgb(*it['color']))
        swatch.Click += self._pick(it, swatch)
        slider = Slider()
        slider.Minimum = 0
        slider.Maximum = 100
        slider.TickFrequency = 5
        slider.IsSnapToTickEnabled = True
        slider.Value = it['transparency']
        slider.VerticalAlignment = VerticalAlignment.Center
        value = _text(u'{0} %'.format(it['transparency']))
        value.Margin = Thickness(6, 0, 0, 0)
        slider.ValueChanged += self._slide(it, value)
        for col, ctl in enumerate((cb, swatch, slider, value)):
            Grid.SetColumn(ctl, col)
            grid.Children.Add(ctl)
        return grid

    def _toggle(self, it):
        def handler(sender, args):
            it['enabled'] = bool(sender.IsChecked)
            self.update_source_counts()
        return handler

    def _pick(self, it, swatch):
        def handler(sender, args):
            rgb = pick_color(it['color'])
            if rgb is not None:
                it['color'] = rgb
                swatch.Background = SolidColorBrush(Color.FromRgb(*it['color']))
                if not it['enabled']:
                    it['enabled'] = True
                    self.render()
                    self.update_source_counts()
        return handler

    def _slide(self, it, label):
        def handler(sender, args):
            it['transparency'] = int(round(sender.Value))
            label.Text = u'{0} %'.format(it['transparency'])
        return handler

    def _bulk(self, on):
        def handler(sender, args):
            src = self.current()
            if src is None:
                return
            _, scope, names = src
            for name in names:
                if _matches(name, self.search_box.Text):
                    self.item(scope, name, create=True)['enabled'] = on
            self.render()
            self.update_source_counts()
        return handler

    def on_ok(self, sender, args):
        param = (self.param_box.Text or u'').strip() or u'SMNX_Фильтр'
        items = sorted(self.items.values(), key=lambda i: (i['scope'] != 'own', i['name'].lower()))
        # храним включённые и те, что уже были в профиле (цвет не теряется при выключении);
        # строки, которые лишь показались в окне, не сохраняем
        keep = [i for i in items if i['enabled'] or (i['scope'], i['name']) in self.original]
        self.result = {'param': param, 'items': keep}
        self.win.DialogResult = True


def _find_parent(element, cls):
    """Ближайший предок нужного типа в визуальном дереве (или None)."""
    while element is not None:
        if isinstance(element, cls):
            return element
        try:
            element = VisualTreeHelper.GetParent(element)
        except Exception:
            return None
    return None


def _icon_button(glyph, tooltip, handler, style):
    b = Button()
    b.Style = style
    b.Content = glyph
    b.ToolTip = tooltip
    b.Click += handler
    return b


CHIP_COLORS = {
    # (фон, рамка, текст)
    'manual': (_brush('#E8F5E9'), _brush('#A5D6A7'), _brush('#1B5E20')),
    'auto': (_brush('#F7F9FA'), _brush('#CFD8DC'), _brush('#607D8B')),
    'missing': (_brush('#F5F5F5'), _brush('#E0E0E0'), _brush('#9E9E9E')),
}
CARD_BORDER = _brush('#D5DBE1')
CARD_SELECTED = _brush('#2E7D32')
CARD_DROP_BG = _brush('#F1F8F1')
ZONE_BG = _brush('#FAFBFC')
ZONE_BORDER = _brush('#B0BEC5')
DRAG_FORMAT = 'EOM.ViewManager.Worksets'


class BucketBoard(object):
    """Вкладка «Группы» или «Подгруппы»: слева все РБ модели, справа карточки.

    РБ перетаскиваются из списка в карточку (в списке остаются — один РБ может быть
    в нескольких карточках), между карточками (перенос; с Ctrl — копия) и обратно
    в список (убрать из карточки). Подгруппы дополнительно могут собирать РБ
    автоматически — по маске имени или «всё остальное»."""

    def __init__(self, editor, kind):
        self.ed = editor
        self.kind = kind                       # 'groups' | 'sections'
        self.sections = kind == 'sections'
        self.buckets = editor.sections if self.sections else editor.groups
        self.word = u'подгруппа' if self.sections else u'группа'
        self.selected = 0 if self.buckets else None
        self.cards = []
        self.left = {}                         # имя РБ -> (ListBoxItem, подпись)
        self.drag_start = None
        self.drag_item = None
        self.pending_select = None
        self.root = self.build()
        self.fill_left()
        self.render()

    # ---------- построение ----------
    def build(self):
        grid = Grid()
        for width in (GridLength(340), GridLength(12), GridLength(1, GridUnitType.Star)):
            col = ColumnDefinition()
            col.Width = width
            grid.ColumnDefinitions.Add(col)

        left = Border()
        left.Style = self.ed.style('Card')
        dock = DockPanel()
        top = StackPanel()
        DockPanel.SetDock(top, Dock.Top)
        title = DockPanel()
        self.left_count = TextBlock()
        self.left_count.Style = self.ed.style('Hint')
        self.left_count.VerticalAlignment = VerticalAlignment.Center
        DockPanel.SetDock(self.left_count, Dock.Right)
        title.Children.Add(self.left_count)
        h = TextBlock()
        h.Text = u'Рабочие наборы модели'
        h.Style = self.ed.style('H2')
        title.Children.Add(h)
        top.Children.Add(title)
        self.search = TextBox()
        self.search.Margin = Thickness(0, 8, 0, 6)
        self.search.ToolTip = u'Поиск по имени рабочего набора'
        self.search.TextChanged += self.on_filter
        top.Children.Add(self.search)
        self.only_free = CheckBox()
        self.only_free.Margin = Thickness(0, 0, 0, 6)
        self.only_free.Content = _text(u'Только не разложенные вручную' if self.sections
                                       else u'Только без группы')
        self.only_free.Click += self.on_filter
        top.Children.Add(self.only_free)
        dock.Children.Add(top)
        hint = TextBlock()
        hint.Style = self.ed.style('Hint')
        hint.Margin = Thickness(0, 8, 0, 0)
        hint.Text = (u'Перетащите РБ в карточку справа — в списке он останется. Ctrl или Shift — '
                     u'выбрать несколько. Двойной щелчок — добавить в выделенную карточку.')
        DockPanel.SetDock(hint, Dock.Bottom)
        dock.Children.Add(hint)
        self.listbox = ListBox()
        self.listbox.SelectionMode = SelectionMode.Extended
        self.listbox.AllowDrop = True
        self.listbox.PreviewMouseLeftButtonDown += self.on_list_down
        self.listbox.PreviewMouseLeftButtonUp += self.on_list_up
        self.listbox.PreviewMouseMove += self.on_list_move
        self.listbox.MouseDoubleClick += self.on_list_double
        self.listbox.DragOver += self.on_list_drag_over
        self.listbox.Drop += self.on_list_drop
        dock.Children.Add(self.listbox)
        left.Child = dock
        Grid.SetColumn(left, 0)
        grid.Children.Add(left)

        right = DockPanel()
        bar = DockPanel()
        bar.LastChildFill = False
        bar.Margin = Thickness(0, 0, 0, 8)
        DockPanel.SetDock(bar, Dock.Top)
        add = Button()
        add.Content = u'+  Новая ' + self.word
        add.Click += self.on_add
        DockPanel.SetDock(add, Dock.Left)
        bar.Children.Add(add)
        if self.sections:
            reset = Button()
            reset.Content = u'По умолчанию'
            reset.ToolTip = u'Вернуть подгруппы 0 — Связи, 1 — Второстепенные, 2 — ЭОМ, Прочие'
            reset.Click += self.on_reset
            DockPanel.SetDock(reset, Dock.Right)
            bar.Children.Add(reset)
        right.Children.Add(bar)
        scroll = ScrollViewer()
        scroll.VerticalScrollBarVisibility = ScrollBarVisibility.Auto
        scroll.HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled
        self.cards_panel = StackPanel()
        self.cards_panel.Margin = Thickness(0, 0, 4, 0)
        scroll.Content = self.cards_panel
        right.Children.Add(scroll)
        Grid.SetColumn(right, 2)
        grid.Children.Add(right)
        return grid

    # ---------- список РБ слева ----------
    def short(self, name):
        return vm_profile.short_workset_name(name, self.ed.naming)

    def fill_left(self):
        self.listbox.Items.Clear()
        self.left = {}
        for name in self.ed.model_names:
            item = ListBoxItem()
            item.Tag = name
            item.ToolTip = name
            box = StackPanel()
            label = _text(self.short(name))
            tags = _text(u'', gray=True)
            tags.FontSize = 10.5
            box.Children.Add(label)
            box.Children.Add(tags)
            item.Content = box
            self.listbox.Items.Add(item)
            self.left[name] = (item, tags)
        if not self.ed.model_names:
            note = ListBoxItem()
            note.IsEnabled = False
            note.Content = _text(u'Откройте модель с рабочими наборами.', gray=True)
            self.listbox.Items.Add(note)
        self.update_left()

    def members(self):
        """{индекс карточки: [(имя, 'manual'|'auto'|'missing')]}."""
        names = self.ed.model_names
        known = set(names)
        if self.sections:
            raw = vm_profile.section_members(names, self.buckets)
        else:
            raw = {}
            for i, g in enumerate(self.buckets):
                chosen = set(g['worksets'])
                raw[i] = [(n, 'manual') for n in names if n in chosen]
        result = {}
        for i, b in enumerate(self.buckets):
            part = list(raw.get(i, []))
            part += [(n, 'missing') for n in b['worksets'] if n not in known]
            result[i] = part
        if None in raw:
            result[None] = raw[None]
        return result

    def update_left(self):
        manual, auto = {}, {}
        for i, part in self.members().items():
            title = self.buckets[i]['name'] if i is not None else OTHER_SECTION
            for name, how in part:
                (manual if how == 'manual' else auto).setdefault(name, []).append(title)
        query = self.search.Text
        free_only = bool(self.only_free.IsChecked)
        shown = 0
        for name, (item, tags) in self.left.items():
            if name in manual:
                tags.Text = (u'вручную: ' if self.sections else u'в: ') + u', '.join(manual[name])
            elif self.sections and name in auto:
                tags.Text = u'авто: ' + u', '.join(auto[name])
            else:
                tags.Text = u'без группы'
            visible = _matches(name, query) and not (free_only and name in manual)
            item.Visibility = Visibility.Visible if visible else Visibility.Collapsed
            if not visible:
                item.IsSelected = False
            shown += 1 if visible else 0
        self.left_count.Text = u'{0} из {1}'.format(shown, len(self.left))

    def on_filter(self, sender, args):
        self.update_left()

    def selected_names(self):
        return [i.Tag for i in self.listbox.SelectedItems if i.Tag]

    def on_list_down(self, sender, args):
        item = _find_parent(args.OriginalSource, ListBoxItem)
        # нажатие мимо РБ (полоса прокрутки и т.п.) перетаскивание не начинает
        self.drag_start = args.GetPosition(None) if item is not None else None
        self.drag_item = item
        self.pending_select = None
        # нажатие на уже выделенный РБ при нескольких выделенных не должно сбрасывать
        # выделение — иначе нельзя перетащить сразу несколько
        if (item is not None and item.IsSelected and self.listbox.SelectedItems.Count > 1
                and Keyboard.Modifiers == MOD_NONE):
            self.pending_select = item
            args.Handled = True

    def on_list_up(self, sender, args):
        if self.pending_select is not None:
            self.listbox.SelectedItems.Clear()
            self.pending_select.IsSelected = True
        self.pending_select = None
        self.drag_start = None

    def on_list_move(self, sender, args):
        if self.drag_start is None or args.LeftButton != MouseButtonState.Pressed:
            return
        pos = args.GetPosition(None)
        if (abs(pos.X - self.drag_start.X) < SystemParameters.MinimumHorizontalDragDistance and
                abs(pos.Y - self.drag_start.Y) < SystemParameters.MinimumVerticalDragDistance):
            return
        names = self.selected_names()
        if not names and self.drag_item is not None and self.drag_item.Tag:
            names = [self.drag_item.Tag]
        self.drag_start = None
        self.pending_select = None
        if names:
            self.start_drag(self.listbox, names, None, False)

    def on_list_double(self, sender, args):
        if _find_parent(args.OriginalSource, ListBoxItem) is None:
            return
        names = self.selected_names()
        if not names:
            return
        if self.selected is None or self.selected >= len(self.buckets):
            self.create(names)
            return
        self.add_to(self.selected, names)

    def on_list_drag_over(self, sender, args):
        d = self.ed.drag
        ok = d is not None and d['source'] is not None and d['manual']
        args.Effects = DragDropEffects.Move if ok else DDE_NONE
        args.Handled = True

    def on_list_drop(self, sender, args):
        d = self.ed.drag
        if d is None or d['source'] is None or not d['manual']:
            return
        self.remove_from(d['source'], d['names'])
        args.Handled = True

    # ---------- перетаскивание ----------
    def start_drag(self, element, names, source, manual):
        self.ed.drag = {'names': list(names), 'source': source, 'manual': manual}
        try:
            DragDrop.DoDragDrop(element, DataObject(DRAG_FORMAT, u'\n'.join(names)),
                                DragDropEffects.Copy | DragDropEffects.Move)
        finally:
            self.ed.drag = None

    def effect(self, args, target):
        d = self.ed.drag
        if d is None:
            return DDE_NONE
        if d['source'] is None or d['source'] == target:
            return DragDropEffects.Copy if d['source'] is None else DDE_NONE
        if d['manual'] and not _ctrl(args):
            return DragDropEffects.Move
        return DragDropEffects.Copy

    def dropped(self, args, target):
        """Положить перетаскиваемые РБ в карточку target (None — новая карточка)."""
        d = self.ed.drag
        if d is None:
            return
        move = (d['source'] is not None and d['source'] != target and d['manual']
                and not _ctrl(args))
        names, source = d['names'], d['source']
        if target is None:
            target = self.create(names, render=False)
            if source is not None and source >= target:
                source += 1   # новая карточка вставлена выше источника
        else:
            self.add_to(target, names, render=False)
        if move:
            self.remove_from(source, names, render=False)
        self.selected = target
        self.refresh()

    # ---------- изменения ----------
    def add_to(self, index, names, render=True):
        chosen = self.buckets[index]['worksets']
        for n in names:
            if n not in chosen:
                chosen.append(n)
        self.selected = index
        if render:
            self.refresh()

    def remove_from(self, index, names, render=True):
        chosen = self.buckets[index]['worksets']
        for n in names:
            if n in chosen:
                chosen.remove(n)
        if render:
            self.refresh()

    def unique_name(self):
        taken = set(b['name'] for b in self.buckets)
        base = self.word.capitalize()
        n = len(self.buckets) + 1
        while u'{0} {1}'.format(base, n) in taken:
            n += 1
        return u'{0} {1}'.format(base, n)

    def create(self, names=None, render=True):
        if self.sections:
            bucket = vm_profile.new_section(self.unique_name(), names)
            # новую подгруппу ставим перед «всем остальным»
            pos = len(self.buckets)
            for i, b in enumerate(self.buckets):
                if b.get('rest'):
                    pos = i
                    break
        else:
            bucket = {'name': self.unique_name(), 'worksets': list(names or [])}
            pos = len(self.buckets)
        self.buckets.insert(pos, bucket)
        self.selected = pos
        self.focus_name = True
        if render:
            self.refresh()
        return pos

    def on_add(self, sender, args):
        self.create()

    def on_reset(self, sender, args):
        answer = MessageBox.Show(self.ed.win, u'Вернуть подгруппы по умолчанию? Ваши подгруппы '
                                 u'удалятся.', u'View Manager', MessageBoxButton.YesNo,
                                 MessageBoxImage.Question)
        if answer != MessageBoxResult.Yes:
            return
        self.buckets[:] = vm_profile.default_sections()
        self.selected = 0
        self.refresh()

    def refresh(self):
        self.render()
        self.update_left()

    # ---------- карточки ----------
    def render(self):
        panel = self.cards_panel
        panel.Children.Clear()
        self.cards = []
        members = self.members()
        focus = getattr(self, 'focus_name', False)
        self.focus_name = False
        for index, bucket in enumerate(self.buckets):
            card, name_box = self.card(index, bucket, members.get(index, []))
            panel.Children.Add(card)
            self.cards.append(card)
            if focus and index == self.selected:
                name_box.Loaded += self._focus_handler(name_box)
        if None in members and members[None]:
            other = {'name': OTHER_SECTION, 'worksets': [], 'patterns': [], 'hidden': False,
                     'rest': True}
            card, _ = self.card(None, other, members[None])
            panel.Children.Add(card)
        panel.Children.Add(self.new_zone())
        self.paint_selection()

    @staticmethod
    def _focus_handler(box):
        def handler(sender, args):
            box.Focus()
            box.SelectAll()
        return handler

    def paint_selection(self):
        for i, card in enumerate(self.cards):
            card.BorderBrush = CARD_SELECTED if i == self.selected else CARD_BORDER

    def card(self, index, bucket, part):
        card = Border()
        card.Style = self.ed.style('Card')
        card.Margin = Thickness(0, 0, 0, 8)
        card.Tag = index
        hidden = self.sections and bucket.get('hidden')
        if hidden:
            card.Opacity = 0.6
        box = StackPanel()
        header = DockPanel()
        name_box = None
        if index is not None:
            tools = StackPanel()
            tools.Orientation = Orientation.Horizontal
            DockPanel.SetDock(tools, Dock.Right)
            icon = self.ed.style('Icon')
            if self.sections:
                tools.Children.Add(_icon_button(
                    u'' if hidden else u'',
                    u'Показать на панели' if hidden else u'Скрыть на панели',
                    self._bucket_handler(self.toggle_hidden, index), icon))
            up = _icon_button(u'', u'Выше', self._bucket_handler(self.move, index, -1), icon)
            up.IsEnabled = index > 0
            down = _icon_button(u'', u'Ниже', self._bucket_handler(self.move, index, 1), icon)
            down.IsEnabled = index < len(self.buckets) - 1
            tools.Children.Add(up)
            tools.Children.Add(down)
            tools.Children.Add(_icon_button(u'', u'Удалить',
                                            self._bucket_handler(self.delete, index), icon))
            header.Children.Add(tools)
        count = TextBlock()
        count.Style = self.ed.style('Hint')
        count.VerticalAlignment = VerticalAlignment.Center
        count.Margin = Thickness(8, 0, 8, 0)
        present = sum(1 for _, how in part if how != 'missing')
        count.Text = u'{0} РБ'.format(present) + (u' · скрыта на панели' if hidden else u'')
        DockPanel.SetDock(count, Dock.Right)
        header.Children.Add(count)
        if index is None:
            title = _text(bucket['name'])
            title.FontWeight = FontWeights.SemiBold
            title.FontSize = 13
            header.Children.Add(title)
        else:
            name_box = TextBox()
            name_box.Text = bucket['name']
            name_box.FontWeight = FontWeights.SemiBold
            name_box.FontSize = 13
            name_box.BorderThickness = Thickness(0, 0, 0, 1)
            name_box.Background = Brushes.Transparent
            name_box.ToolTip = u'Название — можно править'
            name_box.TextChanged += self._name_handler(index)
            name_box.LostFocus += self._lost_focus_left
            header.Children.Add(name_box)
        box.Children.Add(header)
        if self.sections:
            box.Children.Add(self.auto_row(index, bucket))
        chips = WrapPanel()
        chips.Margin = Thickness(0, 8, 0, 0)
        for name, how in part:
            chips.Children.Add(self.chip(index, name, how))
        if not part:
            empty = _text(u'Перетащите сюда рабочие наборы из списка слева', gray=True)
            empty.FontSize = 11
            chips.Children.Add(empty)
        box.Children.Add(chips)
        card.Child = box
        if index is not None:
            card.AllowDrop = True
            card.PreviewMouseLeftButtonDown += self._bucket_handler(self.select, index)
            card.DragEnter += self._drag_over(index, card)
            card.DragOver += self._drag_over(index, card)
            card.DragLeave += self._drag_leave(card)
            card.Drop += self._drop(index, card)
        return card, name_box

    def auto_row(self, index, bucket):
        if bucket.get('rest'):
            note = TextBlock()
            note.Style = self.ed.style('Hint')
            note.Margin = Thickness(0, 4, 0, 0)
            note.Text = u'Сюда сами попадают РБ, которые не попали ни в одну другую подгруппу.'
            return note
        row = DockPanel()
        row.Margin = Thickness(0, 4, 0, 0)
        label = TextBlock()
        label.Style = self.ed.style('Hint')
        label.Text = u'Ещё автоматически, по началу имени:'
        label.VerticalAlignment = VerticalAlignment.Center
        label.Margin = Thickness(0, 0, 6, 0)
        DockPanel.SetDock(label, Dock.Left)
        row.Children.Add(label)
        box = TextBox()
        box.FontSize = 11
        box.Padding = Thickness(3, 1, 3, 1)
        box.Text = u'; '.join(bucket['patterns'])
        box.ToolTip = (u'Необязательно. Например 0_* — все РБ, имя которых начинается с «0_». '
                       u'Несколько — через «;». РБ, перетащенные вручную, сюда не попадают.')
        box.LostFocus += self._patterns_handler(index)
        row.Children.Add(box)
        return row

    def chip(self, index, name, how):
        bg, border, fg = CHIP_COLORS[how]
        chip = Border()
        chip.Background = bg
        chip.BorderBrush = border
        chip.BorderThickness = Thickness(1)
        chip.CornerRadius = CornerRadius(3)
        chip.Padding = Thickness(7, 2, 2 if how != 'auto' else 7, 2)
        chip.Margin = Thickness(0, 0, 4, 4)
        chip.Cursor = Cursors.SizeAll
        row = StackPanel()
        row.Orientation = Orientation.Horizontal
        label = _text(self.short(name) + (u'  — нет в модели' if how == 'missing' else u''))
        label.Foreground = fg
        label.FontSize = 11.5
        if how == 'auto':
            label.FontStyle = FontStyles.Italic
        row.Children.Add(label)
        if how == 'auto':
            chip.ToolTip = (u'{0}\nПопал автоматически. Перетащите в другую подгруппу, '
                            u'чтобы перенести.'.format(name))
        else:
            chip.ToolTip = name
            remove = Button()
            remove.Style = self.ed.style('Icon')
            remove.Width = remove.Height = 18
            remove.FontSize = 8
            remove.Margin = Thickness(4, 0, 0, 0)
            remove.Content = u''
            remove.ToolTip = u'Убрать из карточки'
            remove.Click += self._chip_remove(index, name)
            row.Children.Add(remove)
        chip.Child = row
        if index is not None and how != 'missing':
            chip.PreviewMouseLeftButtonDown += self._chip_down
            chip.PreviewMouseMove += self._chip_move(index, name, how == 'manual')
        elif index is None:
            chip.PreviewMouseLeftButtonDown += self._chip_down
            chip.PreviewMouseMove += self._chip_move(None, name, False)
        return chip

    def new_zone(self):
        zone = Border()
        zone.Tag = 'zone'
        zone.BorderBrush = ZONE_BORDER
        zone.BorderThickness = Thickness(1)
        zone.CornerRadius = CornerRadius(6)
        zone.Background = ZONE_BG
        zone.Padding = Thickness(14)
        zone.Margin = Thickness(0, 0, 0, 8)
        zone.Cursor = Cursors.Hand
        zone.AllowDrop = True
        text = _text(u'+  Перетащите сюда — будет новая ' + self.word, gray=True)
        text.HorizontalAlignment = HorizontalAlignment.Center
        zone.Child = text
        zone.MouseLeftButtonUp += self._new_zone_click
        zone.DragEnter += self._drag_over(None, zone)
        zone.DragOver += self._drag_over(None, zone)
        zone.DragLeave += self._drag_leave(zone)
        zone.Drop += self._drop(None, zone)
        return zone

    # ---------- обработчики карточек ----------
    def _bucket_handler(self, func, *bound):
        def handler(sender, args):
            func(*bound)
        return handler

    def select(self, index):
        if self.selected != index:
            self.selected = index
            self.paint_selection()

    def toggle_hidden(self, index):
        b = self.buckets[index]
        b['hidden'] = not b.get('hidden')
        self.render()

    def move(self, index, delta):
        j = index + delta
        if not (0 <= j < len(self.buckets)):
            return
        self.buckets[index], self.buckets[j] = self.buckets[j], self.buckets[index]
        self.selected = j
        self.refresh()

    def delete(self, index):
        name = self.buckets[index]['name']
        answer = MessageBox.Show(self.ed.win, u'Удалить {0} «{1}»? Рабочие наборы останутся в '
                                 u'модели.'.format(self.word, name), u'View Manager',
                                 MessageBoxButton.YesNo, MessageBoxImage.Question)
        if answer != MessageBoxResult.Yes:
            return
        del self.buckets[index]
        self.selected = min(index, len(self.buckets) - 1) if self.buckets else None
        self.refresh()

    def _name_handler(self, index):
        def handler(sender, args):
            if index < len(self.buckets):
                self.buckets[index]['name'] = sender.Text
        return handler

    def _lost_focus_left(self, sender, args):
        self.update_left()

    def _patterns_handler(self, index):
        def handler(sender, args):
            parts = [p.strip() for p in re.split(u'[;\n]', sender.Text or u'')]
            patterns = []
            for p in parts:
                if p and p not in patterns:
                    patterns.append(p)
            if patterns != self.buckets[index]['patterns']:
                self.buckets[index]['patterns'] = patterns
                self.refresh()
        return handler

    def _chip_remove(self, index, name):
        def handler(sender, args):
            self.remove_from(index, [name])
        return handler

    def _chip_down(self, sender, args):
        self.drag_start = args.GetPosition(None)

    def _chip_move(self, index, name, manual):
        def handler(sender, args):
            if self.drag_start is None or args.LeftButton != MouseButtonState.Pressed:
                return
            pos = args.GetPosition(None)
            if (abs(pos.X - self.drag_start.X) < SystemParameters.MinimumHorizontalDragDistance
                    and abs(pos.Y - self.drag_start.Y) < SystemParameters.MinimumVerticalDragDistance):
                return
            self.drag_start = None
            self.start_drag(sender, [name], index, manual)
        return handler

    def _drag_over(self, index, element):
        def handler(sender, args):
            effect = self.effect(args, index)
            args.Effects = effect
            if effect != DDE_NONE:
                element.Background = CARD_DROP_BG
                if index is not None:
                    element.BorderBrush = CARD_SELECTED
            args.Handled = True
        return handler

    def _drag_leave(self, element):
        def handler(sender, args):
            self.unpaint(element)
        return handler

    def unpaint(self, element):
        element.Background = ZONE_BG if element.Tag == 'zone' else Brushes.White
        if element.Tag == 'zone':
            element.BorderBrush = ZONE_BORDER
        self.paint_selection()

    def _drop(self, index, element):
        def handler(sender, args):
            args.Handled = True
            if self.effect(args, index) == DDE_NONE:
                self.unpaint(element)
                return
            self.dropped(args, index)
        return handler

    def _new_zone_click(self, sender, args):
        self.create()


class GroupEditor(object):
    """Окно настройки рабочих наборов: группы, подгруппы (перетаскиванием) и наименование."""

    NAMES = ('tabs', 'groups_tab', 'sections_tab', 'ok_button', 'footer_hint',
             'rules_panel', 'preview_panel', 'add_rule_button')

    def __init__(self, model_names, groups, naming, sections, owner_hwnd, tab=0):
        self.win = _load_xaml('vm_groups.xaml')
        for name in self.NAMES:
            setattr(self, name, self.win.FindName(name))
        _owner_window(self.win, owner_hwnd)
        self.model_names = list(model_names)
        self.groups = copy.deepcopy(groups)
        self.naming = copy.deepcopy(naming)
        self.sections = copy.deepcopy(sections)
        self.drag = None
        self.result = None
        self.footer_hint.Text = (u'Группы и подгруппы хранятся по именам РБ в вашем профиле — '
                                 u'в других проектах с такими же РБ они тоже сработают.')
        self.groups_board = BucketBoard(self, 'groups')
        self.groups_tab.Content = self.groups_board.root
        self.sections_board = BucketBoard(self, 'sections')
        self.sections_tab.Content = self.sections_board.root
        self.add_rule_button.Click += self.on_add_rule
        self.render_rules()
        self.ok_button.Click += self.on_ok
        self.tabs.SelectedIndex = tab

    def style(self, key):
        return self.win.FindResource(key)

    def show(self):
        self.win.ShowDialog()
        return self.result

    # ---------- наименование РБ ----------
    def render_rules(self):
        panel = self.rules_panel
        panel.Children.Clear()
        for index, rule in enumerate(self.naming['rules']):
            row = Grid()
            row.Margin = Thickness(0, 0, 0, 6)
            for width in (GridLength(1, GridUnitType.Star), GridLength(22),
                          GridLength(1, GridUnitType.Star), GridLength(30)):
                col = ColumnDefinition()
                col.Width = width
                row.ColumnDefinitions.Add(col)
            find = TextBox()
            find.Text = rule['find']
            find.TextChanged += self._rule_handler(index, 'find')
            arrow = _text(u'→', gray=True)
            arrow.HorizontalAlignment = HorizontalAlignment.Center
            repl = TextBox()
            repl.Text = rule['replace']
            repl.TextChanged += self._rule_handler(index, 'replace')
            remove = _icon_button(u'', u'Удалить правило', self._remove_handler(index),
                                  self.style('Icon'))
            remove.Margin = Thickness(4, 0, 0, 0)
            for col, ctl in enumerate((find, arrow, repl, remove)):
                Grid.SetColumn(ctl, col)
                row.Children.Add(ctl)
            panel.Children.Add(row)
        if not self.naming['rules']:
            panel.Children.Add(_text(u'Правил нет. Пример: найти «0_Связи » → заменить на пусто.',
                                     gray=True))
        self.render_preview()

    def _rule_handler(self, index, field):
        def handler(sender, args):
            self.naming['rules'][index][field] = sender.Text
            self.render_preview()
        return handler

    def _remove_handler(self, index):
        def handler(sender, args):
            del self.naming['rules'][index]
            self.render_rules()
        return handler

    def on_add_rule(self, sender, args):
        self.naming['rules'].append({'find': u'', 'replace': u''})
        self.render_rules()
        last = self.rules_panel.Children[len(self.naming['rules']) - 1]
        last.Children[0].Focus()

    def render_preview(self):
        panel = self.preview_panel
        panel.Children.Clear()
        naming = {'rules': [r for r in self.naming['rules'] if r['find']]}
        for name in self.model_names:
            short = vm_profile.short_workset_name(name, naming)
            row = TextBlock()
            row.Margin = Thickness(0, 2, 0, 2)
            row.TextTrimming = TextTrimming.CharacterEllipsis
            old = Run(name + u'  →  ')
            old.Foreground = TEXT_GRAY
            new = Run(short)
            new.FontWeight = FontWeights.SemiBold
            row.Inlines.Add(old)
            row.Inlines.Add(new)
            panel.Children.Add(row)
        if not self.model_names:
            panel.Children.Add(_text(u'Откройте модель с рабочими наборами, чтобы увидеть пример.',
                                     gray=True))

    def on_ok(self, sender, args):
        # порядок наборов — как в модели, отсутствующие в конце
        order = dict((n, i) for i, n in enumerate(self.model_names))
        for bucket in self.groups + self.sections:
            bucket['name'] = (bucket['name'] or u'').strip() or u'Без названия'
            bucket['worksets'].sort(key=lambda n: (order.get(n, len(order)), n.lower()))
        self.naming['rules'] = [r for r in self.naming['rules'] if r['find']]
        self.result = (self.groups, self.naming, self.sections)
        self.win.DialogResult = True
