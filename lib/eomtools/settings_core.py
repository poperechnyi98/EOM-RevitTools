# -*- coding: utf-8 -*-
"""
Общий модуль настроек кнопок: хранение в JSON-файле + типовое окно из
текстовых полей.

Хранилище (JsonStore)
---------------------
Каждая кнопка/дисциплина хранит свои настройки одним JSON-файлом в
%APPDATA%\\pyRevit\\<file_name> — обычный файл, намеренно НЕ
pyrevit.script.get_config() (тот не гарантированно делил секцию конфига
между разными script.py, см. CLAUDE.md). Раньше путь/чтение/запись были
скопированы в ~20 модулей *_settings.py; теперь они берут JsonStore:

    _STORE = settings_core.JsonStore("EOMXxx_settings.json", u"СКС")
    _settings_file_path = _STORE.path
    _read_all = _STORE.read
    _write_all = _STORE.write

Формат файла не изменился — уже сохранённые настройки читаются как раньше.

Общие значения между кнопками
-----------------------------
У многих кнопок одни и те же настройки — имена параметров проекта
(«wire_mark_param», «device_address_param», …): ключи в разных файлах
названы одинаково. Что пользователь ввёл в одной кнопке, подставляется
по умолчанию в остальные:

- JsonStore.write запоминает в общем файле EOMShared_settings.json
  новые/изменённые непустые строки по ключам имён параметров (is_shared_key:
  оканчиваются на «_param»/«_param_name», кроме SHARED_EXCLUDED_KEYS —
  одинаковых имён с разным смыслом).
- JsonStore.read возвращает SavedSettings — обычный dict, у которого
  get(key, default) для такого ключа, не заполненного в своём файле (нет
  или пусто), и при пустом default берёт запомненное значение. Непустой
  default модуля (ключевые слова, коэффициенты) не перекрывается, свой
  файл кнопки — тоже; в сам файл подставленное значение попадает, только
  когда пользователь сохранит настройки этой кнопки.

Типовое окно (TextSettings)
---------------------------
Для кнопок, у которых настройки — просто несколько текстовых/числовых
полей (имена параметров, маски, префиксы, отступы), всё остальное —
загрузка с умолчаниями, require(), окно с проверкой чисел, «Выгрузить/
Загрузить настройки…», Shift+клик/тихий режим — делает TextSettings по
описанию полей (TextField, NumberField, ChoiceField):

    SETTINGS = settings_core.TextSettings(
        file_name="EOMRoomLots_settings.json",
        button_name=u"Двухуровневые лоты",
        heading=u"Параметры имени лота и номера секции",
        transfer_label=u"двухуровневых лотов",
        fields=[
            settings_core.TextField(
                "lot_param_name", u"① Имя лота",
                u"Параметр помещения (в связи АР)",
                hint=u"...", required=True),
        ],
    )

Нестандартные окна (таблицы, выбор типоразмеров, мнемосхемы) пока
остаются в своих модулях и берут отсюда только JsonStore.

WPF и pyrevit импортируются лениво (внутри функций), чтобы модуль
импортировался вне Revit — его хранилище и логика полей покрыты тестами
(tests/test_settings_core.py).
"""

import io
from eomtools.i18n import tr
import json
import os

try:
    _text = unicode  # noqa: F821 — IronPython/Python 2
    _string_types = basestring  # noqa: F821
except NameError:  # Python 3 (тесты)
    _text = str
    _string_types = str


SETTINGS_FOLDER_NAME = "pyRevit"


def _alert(message, **kwargs):
    """forms.alert, если pyrevit доступен; вне Revit — молча ничего."""
    try:
        from pyrevit import forms
    except Exception:
        return None
    return forms.alert(message, **kwargs)


def settings_folder():
    """%APPDATA%\\pyRevit (создаётся при необходимости)."""
    appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
    folder = os.path.join(appdata, SETTINGS_FOLDER_NAME)

    if not os.path.isdir(folder):
        try:
            os.makedirs(folder)
        except Exception:
            pass

    return folder


# --- общие значения между кнопками -------------------------------------------

SHARED_FILE_NAME = "EOMShared_settings.json"

SHARED_KEY_SUFFIXES = ("_param", "_param_name")

# Ключи имён параметров, которые в разных кнопках значат разное (или
# участвуют в миграции старых настроек) — их не делим.
# (Одинаковые по смыслу параметры под разными ключами переименованы в один
# ключ — см. JsonStore(renames=...); бывшие target_param_name/
# section_param_name с двумя смыслами стали floor_param_name/room_param_name
# и room_section_param, поэтому исключений почти не осталось.)
SHARED_EXCLUDED_KEYS = frozenset([
    "room_number_param_name",  # старый ключ, переносится в room_mask
])


def is_shared_key(key):
    """Делится ли значение ключа между кнопками (имя параметра проекта)."""
    return (isinstance(key, _string_types) and key not in SHARED_EXCLUDED_KEYS
            and key.endswith(SHARED_KEY_SUFFIXES))


def _blank(value):
    if value is None:
        return True
    if isinstance(value, _string_types):
        return not value.strip()
    return isinstance(value, (list, tuple, dict)) and not value


class SavedSettings(dict):
    """
    Словарь из файла настроек (JsonStore.read). Отличается от dict только
    get(): незаполненный в файле общий ключ (is_shared_key) при пустом
    default берётся из того, что пользователь вводил в других кнопках.
    """

    def __init__(self, data=None, shared=None):
        dict.__init__(self, data or {})
        self.shared = shared or {}

    def shared_value(self, key):
        """Запомненное в других кнопках значение, если оно здесь подставится; иначе None."""
        if not is_shared_key(key) or not _blank(dict.get(self, key)):
            return None
        value = self.shared.get(key)
        if isinstance(value, _string_types) and not _blank(value):
            return value
        return None

    def get(self, key, default=None):
        if _blank(default):
            value = self.shared_value(key)
            if value is not None:
                return value
        return dict.get(self, key, default)


def _read_file(path):
    """Словарь из JSON-файла; {} — файла нет, он пуст, битый или не объект."""
    if not os.path.isfile(path):
        return {}

    try:
        with io.open(path, "r", encoding="utf-8") as f:
            text = f.read()
        if not text.strip():
            return {}
        data = json.loads(text)
    except Exception:
        return {}

    return data if isinstance(data, dict) else {}


def _write_file(path, data):
    """Перезаписывает файл целиком. True — записано."""
    try:
        with io.open(path, "w", encoding="utf-8") as f:
            f.write(_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)))
        return True
    except Exception:
        return False


def shared_settings_path():
    return os.path.join(settings_folder(), SHARED_FILE_NAME)


def read_shared():
    """{ключ: последнее введённое значение} — общая память всех кнопок."""
    return _read_file(shared_settings_path())


def remember_shared(old, new):
    """
    Запоминает в общем файле общие ключи, которые в new непустые и
    отличаются от old (что было в файле кнопки до записи). Неизменённые
    значения не трогаем: иначе перезапись файла одной кнопки (например,
    после выбора типоразмеров) вернула бы в общую память её старое
    значение поверх более нового из другой кнопки.
    """
    changed = {}
    for key, value in new.items():
        if (is_shared_key(key) and isinstance(value, _string_types)
                and not _blank(value) and value != old.get(key)):
            changed[key] = value

    if not changed:
        return

    shared = read_shared()
    if all(shared.get(key) == value for key, value in changed.items()):
        return
    shared.update(changed)
    _write_file(shared_settings_path(), shared)


def rename_keys(data, renames):
    """
    Переносит значения старых ключей в новые (renames: {старый: новый}) —
    на месте, возвращает data. Непустой новый ключ важнее старого; старый
    ключ удаляется в любом случае.
    """
    for old, new in renames.items():
        if old not in data:
            continue
        value = data.pop(old)
        if _blank(data.get(new)) and not _blank(value):
            data[new] = value
    return data


class JsonStore(object):
    """
    Один JSON-файл настроек в %APPDATA%\\pyRevit.

    file_name — имя файла или функция без аргументов, возвращающая имя
                (fire_alarm_settings: файл зависит от текущей системы).
    label     — что вставить в сообщение об ошибке записи («СКС»,
                «зон обзора», …); строка или функция. Пусто — общее
                «Не удалось сохранить настройки в файл».
    share     — участвовать в общих значениях между кнопками (см. начало
                модуля): read подставляет, write запоминает.
    renames   — {старый ключ: новый} — ключи, переименованные, чтобы одна
                роль называлась одинаково во всех кнопках: read и write
                переносят значение старого ключа в новый (если новый пуст)
                и убирают старый, так что сохранённые раньше настройки и
                выгруженные файлы читаются как прежде.
    """

    def __init__(self, file_name, label=None, share=True, renames=None):
        self._file_name = file_name
        self._label = label
        self._share = share
        self._renames = dict(renames or {})

    def _resolve(self, value):
        return value() if callable(value) else value

    def path(self):
        return os.path.join(settings_folder(), self._resolve(self._file_name))

    def read(self):
        """
        Весь словарь из файла (SavedSettings); пустой — если файла нет, он
        пуст или битый. get() незаполненных общих ключей подставляет
        значения из других кнопок.
        """
        data = rename_keys(_read_file(self.path()), self._renames)
        return SavedSettings(data, read_shared() if self._share else None)

    def write(self, data, notify=True):
        """Write settings; notify=False keeps background failures silent."""
        path = self.path()
        data = rename_keys(dict(data), self._renames)
        old = rename_keys(_read_file(path), self._renames) if self._share else None

        if not _write_file(path, data):
            if notify:
                label = self._resolve(self._label)
                what = tr(u"настройки {}").format(tr(label)) if label else tr(u"настройки")
                _alert(tr(u"Не удалось сохранить {} в файл:\n{}").format(what, path))
            return False

        if self._share:
            try:
                remember_shared(old, data)
            except Exception:
                pass  # общая память — удобство, запись своих настроек важнее
        return True

    def update(self, values):
        """Дописывает/заменяет переданные ключи, остальные оставляет как есть."""
        data = self.read()
        data.update(values)
        return self.write(data)


# --- типовое окно из текстовых полей ------------------------------------------

class TextField(object):
    """
    Одно текстовое поле окна настроек.

    key      — ключ в JSON-файле.
    section  — жирный заголовок раздела над полем («① Имя лота»); пусто —
               поле продолжает предыдущий раздел.
    label    — подпись поля; у обязательных к ней добавляется « *». Пусто —
               подписью служит section (одно поле — один заголовок).
    hint     — серое пояснение под полем.
    default  — значение, пока пользователь ничего не сохранил (и для кнопки
               «Сбросить»).
    required — показывать « *»; сама проверка — require(settings, keys),
               ключи передаёт кнопка (у одной настройки разные кнопки могут
               требовать разное).

    param_spec — подсказка под полем (см. param_autocomplete): "auto" — по
               имени ключа (rule_for_key: *_param, *_param_name — имя
               параметра; *_params — список имён; room_mask, имена видов
               и т. п. — своё правило), None — без подсказки, иначе — spec
               имён параметров для token_span.
    """

    param_spec = "auto"

    def __init__(self, key, section, label, hint=u"", default=u"", required=False):
        self.key = key
        self.section = section
        self.label = label
        self.hint = hint
        self.default = default
        self.required = required

    def title(self):
        """Как поле называется в сообщениях (require, ошибки ввода)."""
        return tr(self.label or self.section) or self.key

    def display(self, value):
        """Значение → текст в поле ввода."""
        return u"" if value is None else _text(value)

    def parse(self, text):
        """Текст из поля ввода → (значение, None) или (None, текст ошибки)."""
        return text, None

    def coerce(self, value):
        """Сохранённое значение → значение для кнопки (битое — умолчание)."""
        return value


class NumberField(TextField):
    """
    Числовое поле: в окне — текст (запятая или точка), в файле и в
    настройках кнопки — число. Нечисло или меньше minimum — окно не
    закроется по «Сохранить», а битое сохранённое значение при загрузке
    заменяется умолчанием.

    integer — целое (int) вместо дробного (float).
    """

    param_spec = None

    def __init__(self, key, section, label, hint=u"", default=0.0, required=False,
                 minimum=None, integer=False):
        TextField.__init__(self, key, section, label, hint, default, required)
        self.minimum = minimum
        self.integer = integer

    def _number(self, value):
        try:
            text = _text(value).strip().replace(u",", u".")
            number = int(text) if self.integer else float(text)
        except Exception:
            return None
        if self.minimum is not None and number < self.minimum:
            return None
        return number

    def parse(self, text):
        number = self._number(text)
        if number is None:
            what = tr(u"целое число") if self.integer else tr(u"число")
            if self.minimum is not None:
                what += tr(u" не меньше {}").format(self.minimum)
            return None, tr(u"{} — нужно {}").format(self.title(), what)
        return number, None

    def coerce(self, value):
        number = self._number(value)
        return self.default if number is None else number


class ChoiceField(TextField):
    """
    Выбор одного варианта из списка: в окне — выпадающий список с
    подписями, в файле и в настройках кнопки — значение варианта.

    choices — [(значение, подпись), ...] в порядке показа. Сохранённое
    значение, которого нет среди вариантов, при загрузке проходит через
    legacy (словарь {старое значение: новое}, например {1: "header"}), а
    иначе заменяется умолчанием.
    """

    param_spec = None

    def __init__(self, key, section, label, choices, hint=u"", default=None,
                 required=False, legacy=None):
        if default is None:
            default = choices[0][0]
        TextField.__init__(self, key, section, label, hint, default, required)
        self.choices = list(choices)
        self.legacy = dict(legacy or {})

    def labels(self):
        return [tr(label) for _value, label in self.choices]

    def display(self, value):
        value = self.coerce(value)
        for v, label in self.choices:
            if v == value:
                return tr(label)
        return tr(self.choices[0][1])

    def parse(self, text):
        for v, label in self.choices:
            if tr(label) == text or label == text:
                return v, None
        return None, tr(u"{} — выберите вариант из списка").format(self.title())

    def coerce(self, value):
        values = [v for v, _label in self.choices]
        if value in values:
            return value
        for old in (value, _text(value).strip()):
            try:
                if old in self.legacy:
                    return self.legacy[old]
            except TypeError:
                pass
        return self.default


class TextSettings(object):
    """
    Настройки кнопки из простых полей (TextField/NumberField/ChoiceField —
    выпадающий список): хранение + окно.

    file_name      — имя JSON-файла в %APPDATA%\\pyRevit.
    button_name    — название кнопки на ленте (заголовок окна, подсказка
                     «Shift+клик по кнопке …» в require).
    heading        — крупный заголовок в окне.
    transfer_label — для «Выгрузить/Загрузить настройки…» и сообщения
                     require: «настройки <transfer_label>».
    fields         — список полей, в порядке показа.
    renames        — {старый ключ: новый} для JsonStore (переименованные ключи).
    migrate        — необязательная функция (saved, values) → None, правит
                     values на месте после подстановки умолчаний: перенос
                     старых ключей в новые.
    intro          — серый текст под заголовком; None — стандартный
                     «Значения сохраняются…».
    reset_button   — кнопка «Сбросить»: вернуть в окне умолчания всех полей
                     (сохраняется только по «Сохранить»).
    width, height  — размер окна.
    """

    def __init__(self, file_name, button_name, heading, transfer_label, fields,
                 migrate=None, intro=None, reset_button=False, width=720, height=440,
                 renames=None):
        self.store = JsonStore(file_name, renames=renames)
        self.button_name = button_name
        self.heading = heading
        self.transfer_label = transfer_label
        self.fields = list(fields)
        self.migrate = migrate
        self.intro = intro
        self.reset_button = reset_button
        self.width = width
        self.height = height

    # --- данные -----------------------------------------------------------

    def labels(self):
        return dict((f.key, f.title()) for f in self.fields)

    def load_saved_values(self):
        """Значения: из файла, иначе — умолчания полей (числа — числами)."""
        saved = self.store.read()
        values = dict((f.key, f.coerce(saved.get(f.key, f.default))) for f in self.fields)
        if self.migrate is not None:
            self.migrate(saved, values)
        return values

    def save_values(self, values):
        self.store.update(values)

    def parse_form(self, texts):
        """
        {ключ: текст из окна} → (значения, ошибки). Ошибки — список строк
        для сообщения; пока он не пуст, окно не закрывается.
        """
        values = {}
        errors = []
        for field in self.fields:
            if field.key not in texts:
                continue
            value, error = field.parse(texts[field.key])
            if error:
                errors.append(error)
            else:
                values[field.key] = value
        return values, errors

    def missing(self, settings, keys):
        """Подписи незаполненных полей из keys (в порядке keys)."""
        labels = self.labels()
        result = []
        for key in keys:
            value = settings.get(key)
            if value is None or not _text(value).strip():
                result.append(labels.get(key, key))
        return result

    def require(self, settings, keys):
        """
        Проверяет, что перечисленные ключи заполнены. Останавливает скрипт
        через forms.alert(exitscript=True), если чего-то не хватает.
        """
        missing = self.missing(settings, keys)
        if missing:
            _alert(
                tr(u"Не заполнены обязательные настройки {}:\n\n{}\n\n"
                u"Откройте настройки: Shift+клик по кнопке «{}».").format(
                    tr(self.transfer_label), u"\n".join(missing), tr(self.button_name)
                ),
                exitscript=True
            )

    # --- окно -------------------------------------------------------------

    def show_settings_form(self, values):
        """
        Модальное окно редактирования. Возвращает словарь значений (числа
        у NumberField уже разобраны), settings_transfer.RELOAD (настройки
        загружены из файла — открыть заново) или None, если пользователь
        отменил. «Сохранить» с неверным числом окно не закрывает.
        """
        import clr
        clr.AddReference('PresentationFramework')
        clr.AddReference('PresentationCore')

        from System.Windows import (
            Window, WindowStartupLocation, Thickness,
            FontWeights, HorizontalAlignment, TextWrapping
        )
        from System.Windows.Controls import (
            StackPanel, TextBlock, TextBox, Button, Orientation, DockPanel, Dock,
            ScrollViewer, ScrollBarVisibility, ComboBox
        )
        from System.Windows.Media import Brushes

        from pyrevit import forms

        from eomtools import param_autocomplete, settings_transfer

        result = {"values": None}
        param_names = param_autocomplete.NameSource()

        win = Window()
        win.Title = tr(u"Настройки: {}").format(tr(self.button_name))
        win.Width = self.width
        win.Height = self.height
        win.WindowStartupLocation = WindowStartupLocation.CenterScreen

        outer = DockPanel()
        outer.LastChildFill = True

        root = StackPanel()
        root.Margin = Thickness(16)

        title = TextBlock()
        title.Text = tr(self.heading)
        title.FontSize = 16
        title.FontWeight = FontWeights.Bold
        title.Margin = Thickness(0, 0, 0, 4)
        root.Children.Add(title)

        hint = TextBlock()
        hint.Text = (tr(self.intro) if self.intro is not None else
                     tr(u"Значения сохраняются и подставляются при следующих запусках."))
        hint.FontSize = 11
        hint.TextWrapping = TextWrapping.Wrap
        hint.Foreground = Brushes.Gray
        hint.Margin = Thickness(0, 0, 0, 10)
        root.Children.Add(hint)

        boxes = {}    # текстовые поля (к ним цепляются подсказки)
        combos = {}   # ChoiceField — выпадающие списки

        for field in self.fields:
            star = u" *" if field.required else u""

            if field.section:
                section = TextBlock()
                section.Text = tr(field.section) + (u"" if field.label else star)
                section.FontWeight = FontWeights.Bold
                section.Margin = Thickness(0, 16, 0, 2)
                section.TextWrapping = TextWrapping.Wrap
                root.Children.Add(section)

            if field.label:
                label = TextBlock()
                label.Text = tr(field.label) + star
                # без заголовка раздела — отступ от предыдущего поля побольше
                label.Margin = Thickness(0, 2 if field.section else 8, 0, 2)
                label.TextWrapping = TextWrapping.Wrap
                root.Children.Add(label)

            if isinstance(field, ChoiceField):
                combo = ComboBox()
                for label_text in field.labels():
                    combo.Items.Add(label_text)
                combo.SelectedIndex = field.labels().index(
                    field.display(values.get(field.key)))
                combo.Padding = Thickness(4)
                root.Children.Add(combo)
                combos[field.key] = (field, combo)
            else:
                box = TextBox()
                box.Text = field.display(values.get(field.key))
                box.Padding = Thickness(4)
                root.Children.Add(box)
                boxes[field.key] = box

            if field.hint:
                field_hint = TextBlock()
                field_hint.Text = tr(field.hint)
                field_hint.FontSize = 11
                field_hint.Foreground = Brushes.Gray
                field_hint.TextWrapping = TextWrapping.Wrap
                field_hint.Margin = Thickness(0, 2, 0, 0)
                root.Children.Add(field_hint)

        # подсказки: имена параметров, виды, значения… — по ключу поля
        # (param_autocomplete.rule_for_key) или по param_spec поля
        param_autocomplete.attach_by_keys(
            boxes, param_names,
            overrides=dict((f.key, f.param_spec) for f in self.fields
                           if f.param_spec != "auto"),
            values=values,
        )

        if any(f.required for f in self.fields):
            required_hint = TextBlock()
            required_hint.Text = tr(u"* обязательные поля")
            required_hint.FontSize = 11
            required_hint.Foreground = Brushes.Gray
            required_hint.Margin = Thickness(0, 10, 0, 0)
            root.Children.Add(required_hint)

        buttons = StackPanel()
        buttons.Orientation = Orientation.Horizontal
        buttons.HorizontalAlignment = HorizontalAlignment.Right
        buttons.Margin = Thickness(16, 8, 16, 12)
        DockPanel.SetDock(buttons, Dock.Bottom)

        cancel_btn = Button()
        cancel_btn.Content = tr(u"Отмена")
        cancel_btn.Padding = Thickness(10, 4, 10, 4)
        cancel_btn.Margin = Thickness(0, 0, 8, 0)

        ok_btn = Button()
        ok_btn.Content = tr(u"Сохранить")
        ok_btn.Padding = Thickness(10, 4, 10, 4)
        ok_btn.FontWeight = FontWeights.Bold

        def on_ok(sender, args):
            texts = dict((key, box.Text) for key, box in boxes.items())
            for key, (_field, combo) in combos.items():
                texts[key] = combo.SelectedItem or u""
            parsed, errors = self.parse_form(texts)
            if errors:
                forms.alert(tr(u"Проверьте значения:\n\n") + u"\n".join(errors))
                return
            result["values"] = parsed
            win.Close()

        def on_cancel(sender, args):
            win.Close()

        ok_btn.Click += on_ok
        cancel_btn.Click += on_cancel

        if self.reset_button:
            reset_btn = Button()
            reset_btn.Content = tr(u"Сбросить")
            reset_btn.Padding = Thickness(10, 4, 10, 4)
            reset_btn.Margin = Thickness(0, 0, 8, 0)

            def on_reset(sender, args):
                for field in self.fields:
                    if field.key in combos:
                        combo = combos[field.key][1]
                        combo.SelectedIndex = field.labels().index(
                            field.display(field.default))
                    else:
                        boxes[field.key].Text = field.display(field.default)

            reset_btn.Click += on_reset
            buttons.Children.Add(reset_btn)

        buttons.Children.Add(cancel_btn)
        buttons.Children.Add(ok_btn)

        def _on_settings_imported():
            result["values"] = settings_transfer.RELOAD
            win.Close()

        settings_transfer.add_transfer_buttons(
            buttons, self.store.read, self.store.write,
            self.transfer_label, _on_settings_imported
        )

        # Длинные пояснения не должны обрезаться при маленькой высоте окна.
        scroll = ScrollViewer()
        scroll.VerticalScrollBarVisibility = ScrollBarVisibility.Auto
        scroll.Content = root

        outer.Children.Add(buttons)
        outer.Children.Add(scroll)

        win.Content = outer
        win.ShowDialog()

        return result["values"]

    def get_settings_interactive(self):
        """
        Показывает окно настроек, сохраняет введённые значения и возвращает
        настройки (как get_settings_silent). None — пользователь нажал
        «Отмена». Для Shift+клика по кнопке.
        """
        from eomtools import settings_transfer

        while True:
            saved = self.load_saved_values()
            edited = self.show_settings_form(saved)

            if edited == settings_transfer.RELOAD:
                # настройки загружены из файла и уже записаны — открываем заново
                continue

            if edited is None:
                return None

            self.save_values(edited)
            return self.load_saved_values()

    def get_settings_silent(self):
        """Уже сохранённые значения без окна (или умолчания полей)."""
        return self.load_saved_values()
