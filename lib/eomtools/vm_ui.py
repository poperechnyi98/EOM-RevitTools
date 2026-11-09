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
from System.Windows.Controls import ColumnDefinition, Grid
from System.Windows.Controls import (Border, Button, CheckBox, DockPanel, ScrollViewer,
                                     ScrollBarVisibility, StackPanel, TabItem, TextBlock, TextBox,
                                     WrapPanel, Dock, Orientation)
from System.Windows.Documents import LineBreak, Run
from System.Windows.Interop import WindowInteropHelper
from System.Windows.Markup import XamlReader
from System.Windows.Media import Brushes, Color, SolidColorBrush
from Microsoft.Win32 import OpenFileDialog, SaveFileDialog
from Autodesk.Revit.UI import ExternalEventRequest

from eomtools import vm_profile, vm_revit

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

# Подгруппы списка рабочих наборов по префиксу «N_» в имени
SECTION_TITLES = {u'0': u'0 — Связи', u'1': u'1 — Второстепенные', u'2': u'2 — ЭОМ'}
OTHER_SECTION = u'Прочие'


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


def section_key(name):
    m = re.match(r'^(\d+)_', name)
    return int(m.group(1)) if m else None


def section_title(key):
    if key is None:
        return OTHER_SECTION
    return SECTION_TITLES.get(unicode(key), u'{0}_…'.format(key))


def _load_xaml(name):
    with io.open(os.path.join(HERE, name), 'r', encoding='utf-8') as f:
        return XamlReader.Parse(f.read())


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
             'worksets_panel', 'content_panel',
             # категории
             'cat_all_on_button', 'cat_all_off_button', 'cat_setup_button', 'cat_content_panel',
             'model_header', 'model_panel', 'annotation_header', 'annotation_panel')

    def __init__(self, host):
        self.host = host
        self.uiapp = host.uiapp
        self.root = _load_xaml('vm_panel.xaml')
        for name in self.NAMES:
            setattr(self, name, self.root.FindName(name))
        self.tile_style = self.root.FindResource('Tile')
        self.widths = {'ws': TILE_MIN_WIDTH, 'cat': TILE_MIN_WIDTH}
        self.wraps = {'ws': [], 'cat': []}
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
            getattr(self, kind + '_panel').IsEnabled = self.can_edit(kind)
        any_cat = self.can_edit('model') or self.can_edit('annotation')
        self.cat_all_on_button.IsEnabled = any_cat
        self.cat_all_off_button.IsEnabled = any_cat
        for ctl in (self.groups_button, self.cat_setup_button, self.allow_template_box,
                    self.save_button, self.load_button):
            ctl.IsEnabled = not self.busy

    # ---------- отрисовка ----------
    def render(self):
        self.render_header()
        self.render_ws_area()
        self.render_categories()
        self.update_enabled()

    def current_scopes(self):
        if self.tabs.SelectedIndex == 1:
            return ('model', 'annotation')
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
            states = [vis[n] for n in group['worksets'] if n in vis]
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

    def render_worksets(self):
        panel = self.worksets_panel
        panel.Children.Clear()
        if not self.snap or not self.snap['worksets']:
            return
        query = self.search_box.Text
        sections = {}
        for item in self.snap['worksets']:
            if _matches(item['name'], query):
                sections.setdefault(section_key(item['name']), []).append(item)
        keys = sorted(k for k in sections if k is not None)
        if None in sections:
            keys.append(None)
        for key in keys:
            items = sections[key]
            on = sum(1 for i in items if i['visible'])
            header = _text(u'{0}   {1}/{2}'.format(section_title(key), on, len(items)))
            header.FontWeight = FontWeights.Bold
            header.Margin = Thickness(0, 4, 0, 1)
            panel.Children.Add(header)
            wrap = self.new_wrap('ws')
            naming = self.profile['workset_names']
            for item in items:
                state = 'on' if item['visible'] else 'off'
                short = vm_profile.short_workset_name(item['name'], naming)
                wrap.Children.Add(self.tile(short, state, item['name'],
                                            (item['name'], item['visible']), self.on_workset_tile))
            panel.Children.Add(wrap)

    def all_names(self):
        return [w['name'] for w in self.snap['worksets']] if self.snap else []

    def set_worksets(self, names, show, label):
        self.request('worksets', names=list(names), show=show, label=label)

    def on_all_on(self, sender, args):
        self.set_worksets(self.all_names(), True, u'все рабочие наборы — On')

    def on_all_off(self, sender, args):
        self.set_worksets(self.all_names(), False, u'все рабочие наборы — Off')

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
                             self.profile['workset_names'], self.uiapp.MainWindowHandle)
        result = editor.show()
        if result is None:
            return
        self.profile['groups'], self.profile['workset_names'] = result
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

    def _all_categories(self, show):
        keys = dict((kind, list(self.profile['categories'][kind])) for kind in KINDS
                    if self.can_edit(kind))
        if not any(keys.values()):
            self.status(u'Нет быстрых категорий, которые можно изменить на этом виде.')
            return
        self.set_categories(keys, show, u'все быстрые категории — {0}'.format(u'On' if show else u'Off'))

    def on_cat_all_on(self, sender, args):
        self._all_categories(True)

    def on_cat_all_off(self, sender, args):
        self._all_categories(False)

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
        dock = DockPanel()
        dock.Margin = Thickness(4)
        top = StackPanel()
        DockPanel.SetDock(top, Dock.Top)
        search = TextBox()
        search.Margin = Thickness(0, 0, 0, 4)
        search.ToolTip = u'Поиск категории'
        search.TextChanged += self._handler(self.render, kind)
        buttons = WrapPanel()
        buttons.Margin = Thickness(0, 0, 0, 4)
        for caption, value in ((u'Отметить найденные', True), (u'Снять с найденных', False)):
            b = Button()
            b.Content = caption
            b.Padding = Thickness(6, 2, 6, 2)
            b.Margin = Thickness(0, 0, 4, 0)
            b.Click += self._handler(self.set_shown, kind, value)
            buttons.Children.Add(b)
        count = _text(u'', gray=True)
        count.Margin = Thickness(8, 0, 0, 0)
        buttons.Children.Add(count)
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
        tab = TabItem()
        tab.Header = KIND_TITLES[kind]
        tab.Content = dock
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


class GroupEditor(object):
    """Модальное окно: список групп, название, состав рабочих наборов."""

    NAMES = ('groups_list', 'add_button', 'delete_button', 'up_button', 'down_button',
             'name_box', 'count_label', 'search_box', 'check_shown_button',
             'uncheck_shown_button', 'worksets_panel', 'ok_button',
             'rules_panel', 'preview_panel', 'add_rule_button')

    def __init__(self, model_names, groups, naming, owner_hwnd):
        self.win = _load_xaml('vm_groups.xaml')
        for name in self.NAMES:
            setattr(self, name, self.win.FindName(name))
        _owner_window(self.win, owner_hwnd)
        self.model_names = list(model_names)
        self.groups = copy.deepcopy(groups)
        self.naming = copy.deepcopy(naming)
        self.current = None
        self.loading = False
        self.result = None
        self.add_rule_button.Click += self.on_add_rule
        self.render_rules()
        self.groups_list.SelectionChanged += self.on_select
        self.name_box.TextChanged += self.on_name
        self.search_box.TextChanged += self.on_search
        self.add_button.Click += self.on_add
        self.delete_button.Click += self.on_delete
        self.up_button.Click += self.on_up
        self.down_button.Click += self.on_down
        self.check_shown_button.Click += self.on_check_shown
        self.uncheck_shown_button.Click += self.on_uncheck_shown
        self.ok_button.Click += self.on_ok
        self.fill_list(0 if self.groups else -1)

    def show(self):
        self.win.ShowDialog()
        return self.result

    # ---------- список групп ----------
    def fill_list(self, select):
        self.loading = True
        self.groups_list.Items.Clear()
        for group in self.groups:
            self.groups_list.Items.Add(group['name'])
        self.groups_list.SelectedIndex = select
        self.loading = False
        self.load_group()

    def on_select(self, sender, args):
        if not self.loading:
            self.load_group()

    def load_group(self):
        index = self.groups_list.SelectedIndex
        self.current = index if index >= 0 else None
        has = self.current is not None
        self.loading = True
        self.name_box.Text = self.groups[index]['name'] if has else u''
        self.loading = False
        for ctl in (self.name_box, self.search_box, self.check_shown_button,
                    self.uncheck_shown_button, self.worksets_panel, self.delete_button,
                    self.up_button, self.down_button):
            ctl.IsEnabled = has
        self.render_worksets()

    def unique_name(self):
        taken = set(g['name'] for g in self.groups)
        n = len(self.groups) + 1
        while u'Группа {0}'.format(n) in taken:
            n += 1
        return u'Группа {0}'.format(n)

    def on_add(self, sender, args):
        self.groups.append({'name': self.unique_name(), 'worksets': []})
        self.fill_list(len(self.groups) - 1)
        self.name_box.Focus()
        self.name_box.SelectAll()

    def on_delete(self, sender, args):
        if self.current is None:
            return
        name = self.groups[self.current]['name']
        answer = MessageBox.Show(self.win, u'Удалить группу «{0}»?'.format(name), u'View Manager',
                                 MessageBoxButton.YesNo, MessageBoxImage.Question)
        if answer != MessageBoxResult.Yes:
            return
        index = self.current
        del self.groups[index]
        self.fill_list(min(index, len(self.groups) - 1))

    def move(self, delta):
        i = self.current
        if i is None or not (0 <= i + delta < len(self.groups)):
            return
        self.groups[i], self.groups[i + delta] = self.groups[i + delta], self.groups[i]
        self.fill_list(i + delta)

    def on_up(self, sender, args):
        self.move(-1)

    def on_down(self, sender, args):
        self.move(1)

    def on_name(self, sender, args):
        if self.loading or self.current is None:
            return
        name = self.name_box.Text.strip() or u'Без названия'
        self.groups[self.current]['name'] = name
        index = self.current
        self.loading = True
        self.groups_list.Items.RemoveAt(index)
        self.groups_list.Items.Insert(index, name)
        self.groups_list.SelectedIndex = index
        self.loading = False

    # ---------- состав группы ----------
    def render_worksets(self):
        panel = self.worksets_panel
        panel.Children.Clear()
        if self.current is None:
            self.count_label.Text = u''
            return
        chosen = self.groups[self.current]['worksets']
        missing = [n for n in chosen if n not in self.model_names]
        query = self.search_box.Text
        for name in self.model_names + missing:
            if not _matches(name, query):
                continue
            cb = CheckBox()
            cb.Tag = name
            cb.IsChecked = name in chosen
            cb.Margin = Thickness(0, 2, 0, 2)
            if name in missing:
                cb.Content = _text(name + u'  — нет в этой модели', gray=True)
            else:
                cb.Content = _text(name)
            cb.Click += self.on_check
            panel.Children.Add(cb)
        self.update_count()

    def update_count(self):
        if self.current is not None:
            self.count_label.Text = u'выбрано: {0}'.format(len(self.groups[self.current]['worksets']))

    def set_chosen(self, name, on):
        chosen = self.groups[self.current]['worksets']
        if on and name not in chosen:
            chosen.append(name)
        elif not on and name in chosen:
            chosen.remove(name)

    def on_check(self, sender, args):
        if self.current is None:
            return
        self.set_chosen(sender.Tag, bool(sender.IsChecked))
        self.update_count()

    def set_shown(self, on):
        if self.current is None:
            return
        for cb in self.worksets_panel.Children:
            cb.IsChecked = on
            self.set_chosen(cb.Tag, on)
        self.update_count()

    def on_check_shown(self, sender, args):
        self.set_shown(True)

    def on_uncheck_shown(self, sender, args):
        self.set_shown(False)

    def on_search(self, sender, args):
        self.render_worksets()

    # ---------- наименование РБ ----------
    def render_rules(self):
        panel = self.rules_panel
        panel.Children.Clear()
        for index, rule in enumerate(self.naming['rules']):
            row = Grid()
            row.Margin = Thickness(0, 0, 0, 4)
            for width in (GridLength(1, GridUnitType.Star), GridLength(18),
                          GridLength(1, GridUnitType.Star), GridLength(30)):
                col = ColumnDefinition()
                col.Width = width
                row.ColumnDefinitions.Add(col)
            find = TextBox()
            find.Text = rule['find']
            find.TextChanged += self._rule_handler(index, 'find')
            arrow = _text(u'→')
            arrow.HorizontalAlignment = HorizontalAlignment.Center
            repl = TextBox()
            repl.Text = rule['replace']
            repl.TextChanged += self._rule_handler(index, 'replace')
            remove = Button()
            remove.Content = u'✕'
            remove.ToolTip = u'Удалить правило'
            remove.Margin = Thickness(4, 0, 0, 0)
            remove.Click += self._remove_handler(index)
            for col, ctl in enumerate((find, arrow, repl, remove)):
                Grid.SetColumn(ctl, col)
                row.Children.Add(ctl)
            panel.Children.Add(row)
        if not self.naming['rules']:
            panel.Children.Add(_text(u'Правил нет. Пример: найти «Связи » → заменить на пусто.',
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
            row.Margin = Thickness(0, 1, 0, 1)
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
        # порядок наборов в группе — как в модели, отсутствующие в конце
        order = dict((n, i) for i, n in enumerate(self.model_names))
        for group in self.groups:
            group['worksets'].sort(key=lambda n: (order.get(n, len(order)), n.lower()))
        self.naming['rules'] = [r for r in self.naming['rules'] if r['find']]
        self.result = (self.groups, self.naming)
        self.win.DialogResult = True
