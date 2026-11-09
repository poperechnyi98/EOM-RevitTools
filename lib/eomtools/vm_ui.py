# -*- coding: utf-8 -*-
"""View Manager: интерфейс закреплённой панели и окно редактирования групп.

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
from System.Windows import (CornerRadius, FontWeights, MessageBox, MessageBoxButton, MessageBoxImage,
                            MessageBoxResult, Thickness, VerticalAlignment, TextTrimming)
from System.Windows.Controls import Border, Button, CheckBox, TextBlock, WrapPanel
from System.Windows.Interop import WindowInteropHelper
from System.Windows.Markup import XamlReader
from System.Windows.Media import Brushes, Color, SolidColorBrush
from Microsoft.Win32 import OpenFileDialog, SaveFileDialog
from Autodesk.Revit.UI import ExternalEventRequest

from eomtools import vm_profile, vm_revit

HERE = os.path.dirname(os.path.abspath(__file__))

TILE_MIN_WIDTH = 125.0


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

# Подгруппы списка рабочих наборов по префиксу «N_» в имени
SECTION_TITLES = {u'0': u'0 — Связи', u'1': u'1 — Второстепенные', u'2': u'2 — ЭОМ'}
OTHER_SECTION = u'Прочие'


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
        tb.Foreground = Brushes.Gray
    return tb


class Controller(object):
    """Логика и интерфейс панели. Заменяется целиком при обновлении кода."""

    NAMES = ('view_label', 'target_label', 'all_on_button', 'all_off_button', 'groups_button',
             'groups_panel', 'search_box', 'worksets_panel', 'content_panel', 'legend_panel',
             'allow_template_box', 'save_button', 'load_button', 'status_label')

    def __init__(self, host):
        self.host = host
        self.uiapp = host.uiapp
        self.root = _load_xaml('vm_panel.xaml')
        for name in self.NAMES:
            setattr(self, name, self.root.FindName(name))
        self.tile_style = self.root.FindResource('Tile')
        self.item_width = TILE_MIN_WIDTH
        self.wraps = []
        self.content_panel.SizeChanged += self.on_resize
        self.build_legend()
        self.profile = vm_profile.load()
        self.snap = None
        self.doc = None
        self.view_id = None
        self.pending = None
        self.dirty = True
        self.busy = False
        self.detached = False
        self.allow_template_box.IsChecked = self.profile['allow_template']
        self.all_on_button.Click += self.on_all_on
        self.all_off_button.Click += self.on_all_off
        self.groups_button.Click += self.on_edit_groups
        self.search_box.TextChanged += self.on_search
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
            self.snap = vm_revit.snapshot(self.doc, view, self.profile['allow_template'])
        self.render()

    # ---------- запросы к Revit ----------
    def request(self, kind, names=None, show=None, label=u''):
        if self.pending is not None or self.detached:
            return
        self.pending = (kind, self.doc, self.view_id, list(names or []), show, label)
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
            kind, doc, view_id, names, show, label = req
            if kind == 'refresh':
                self.refresh(uiapp)
                return
            uidoc = uiapp.ActiveUIDocument
            if (uidoc is None or doc is None or not uidoc.Document.Equals(doc)
                    or view_id is None or _id_value(uidoc.ActiveView.Id) != _id_value(view_id)):
                self.refresh(uiapp)
                self.status(u'Вид изменился. Повторите действие на нужном виде.')
                return
            changed, missing = vm_revit.set_worksets(
                doc, uidoc.ActiveView, names, show, self.profile['allow_template'], label)
            self.refresh(uiapp)
            if changed:
                msg = (u'Включено' if show else u'Выключено') + u': {0}.'.format(changed)
            else:
                msg = u'Без изменений.'
            if missing:
                msg += u' Нет в модели: {0}.'.format(missing)
            self.status(msg)
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

    # ---------- отрисовка ----------
    def status(self, text):
        self.status_label.Text = text

    def visibility(self):
        if not self.snap:
            return {}
        return dict((w['name'], w['visible']) for w in self.snap['worksets'])

    def can_edit(self):
        s = self.snap
        return bool(s and s['supported'] and s['workshared'] and not s['blocked'] and not self.busy)

    def set_busy(self, busy):
        self.busy = busy
        self.update_enabled()

    def update_enabled(self):
        ok = self.can_edit()
        for ctl in (self.all_on_button, self.all_off_button, self.groups_panel, self.worksets_panel):
            ctl.IsEnabled = ok
        for ctl in (self.groups_button, self.allow_template_box, self.save_button, self.load_button):
            ctl.IsEnabled = not self.busy

    def render(self):
        self.render_header()
        self.render_groups()
        self.render_worksets()
        self.update_enabled()

    def render_header(self):
        s = self.snap
        if s is None:
            self.view_label.Text = u'Откройте модель Revit.'
            self.target_label.Text = u''
            return
        self.view_label.Text = u'{0} — {1}'.format(s['doc_title'], s['view_name'])
        self.target_label.Foreground = Brushes.Gray
        if not s['supported']:
            self.target_label.Text = u'Откройте план, разрез, фасад или 3D-вид проекта.'
        elif not s['workshared']:
            self.target_label.Text = u'В модели нет рабочих наборов.'
        elif s['template'] is None:
            self.target_label.Text = u'Изменения вносятся в вид.'
        elif not s['template']['controlled']:
            self.target_label.Text = (u'Шаблон «{0}» не управляет рабочими наборами. '
                                      u'Изменения вносятся в вид.').format(s['template']['name'])
        elif s['blocked']:
            self.target_label.Foreground = Brushes.Firebrick
            self.target_label.Text = (u'Шаблон «{0}» управляет рабочими наборами. Чтобы менять их, '
                                      u'поставьте галочку «Разрешаю менять шаблон вида».'
                                      ).format(s['template']['name'])
        else:
            self.target_label.Foreground = Brushes.DarkOrange
            self.target_label.Text = (u'Изменения вносятся в шаблон «{0}» (видов с ним: {1}).'
                                      ).format(s['template']['name'], s['template']['uses'])

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
            label.Margin = Thickness(0, 0, 12, 0)
            self.legend_panel.Children.Add(swatch)
            self.legend_panel.Children.Add(label)

    def on_resize(self, sender, args):
        width = self.content_panel.ActualWidth
        if width <= 0:
            return
        columns = max(1, int(width // TILE_MIN_WIDTH))
        self.item_width = width / columns
        for wrap in self.wraps:
            wrap.ItemWidth = self.item_width

    def new_wrap(self, wrap=None):
        if wrap is None:
            wrap = WrapPanel()
        wrap.ItemWidth = self.item_width
        self.wraps.append(wrap)
        return wrap

    def tile(self, text, state, tooltip, tag, handler):
        bg, fg, border = COLORS[state]
        b = Button()
        b.Style = self.tile_style
        b.Background = bg
        b.BorderBrush = border
        label = _text(text)
        label.Foreground = fg
        b.Content = label
        b.ToolTip = tooltip
        b.Tag = tag
        b.Click += handler
        return b

    def render_groups(self):
        panel = self.groups_panel
        panel.Children.Clear()
        self.wraps = [w for w in self.wraps if w is not panel]
        self.new_wrap(panel)
        groups = self.profile['groups']
        if not groups:
            panel.ItemWidth = float('nan')
            panel.Children.Add(_text(u'Групп пока нет — нажмите «Группы…».', gray=True))
            return
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
        self.wraps = [w for w in self.wraps if w is self.groups_panel]
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
            wrap = self.new_wrap()
            for item in items:
                state = 'on' if item['visible'] else 'off'
                wrap.Children.Add(self.tile(item['name'], state, item['name'],
                                            (item['name'], item['visible']), self.on_workset_tile))
            panel.Children.Add(wrap)

    # ---------- обработчики ----------
    def all_names(self):
        return [w['name'] for w in self.snap['worksets']] if self.snap else []

    def on_all_on(self, sender, args):
        self.request('set', self.all_names(), True, u'все рабочие наборы — On')

    def on_all_off(self, sender, args):
        self.request('set', self.all_names(), False, u'все рабочие наборы — Off')

    def on_group_tile(self, sender, args):
        index, state = sender.Tag
        group = self.profile['groups'][index]
        if state == 'none':
            self.status(u'Рабочих наборов группы «{0}» нет в этой модели.'.format(group['name']))
            return
        show = state != 'on'   # частично или выключено -> включить; включено -> выключить
        self.request('set', group['worksets'], show,
                     u'{0} — {1}'.format(group['name'], u'On' if show else u'Off'))

    def on_workset_tile(self, sender, args):
        name, visible = sender.Tag
        show = not visible
        self.request('set', [name], show, u'{0} — {1}'.format(name, u'On' if show else u'Off'))

    def on_search(self, sender, args):
        self.render_worksets()
        self.update_enabled()

    def on_allow_template(self, sender, args):
        self.profile['allow_template'] = bool(self.allow_template_box.IsChecked)
        self.save_profile()
        self.request('refresh')

    def save_profile(self):
        try:
            vm_profile.save(self.profile)
        except Exception as exc:
            self.status(u'Не удалось сохранить настройки: {0}'.format(unicode(exc)))

    def on_edit_groups(self, sender, args):
        editor = GroupEditor(self.all_names(), self.profile['groups'], self.uiapp.MainWindowHandle)
        result = editor.show()
        if result is None:
            return
        self.profile['groups'] = result
        self.save_profile()
        self.render_groups()
        self.update_enabled()
        self.status(u'Группы сохранены.')

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


class GroupEditor(object):
    """Модальное окно: список групп, название, состав рабочих наборов."""

    NAMES = ('groups_list', 'add_button', 'delete_button', 'up_button', 'down_button',
             'name_box', 'count_label', 'search_box', 'check_shown_button',
             'uncheck_shown_button', 'worksets_panel', 'ok_button')

    def __init__(self, model_names, groups, owner_hwnd):
        self.win = _load_xaml('vm_groups.xaml')
        for name in self.NAMES:
            setattr(self, name, self.win.FindName(name))
        try:
            WindowInteropHelper(self.win).Owner = owner_hwnd
        except Exception:
            pass
        self.model_names = list(model_names)
        self.groups = copy.deepcopy(groups)
        self.current = None
        self.loading = False
        self.result = None
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

    def on_ok(self, sender, args):
        # порядок наборов в группе — как в модели, отсутствующие в конце
        order = dict((n, i) for i, n in enumerate(self.model_names))
        for group in self.groups:
            group['worksets'].sort(key=lambda n: (order.get(n, len(order)), n.lower()))
        self.result = self.groups
        self.win.DialogResult = True
