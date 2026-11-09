# -*- coding: utf-8 -*-
"""View Manager: постоянная «оболочка» закреплённой панели.

Revit позволяет зарегистрировать закреплённую панель только один раз за сеанс,
поэтому здесь лишь минимум, который не меняется: регистрация панели, внешнее
событие (ExternalEvent) и подписки на события Revit. Вся логика и интерфейс —
в ``vm_ui.Controller``; его можно заменить на свежий без перезапуска Revit
(``bind``), что делают startup.py при pyRevit Reload и кнопка View Manager.

Ссылка на оболочку хранится в AppDomain, чтобы она переживала перезагрузки pyRevit.
"""
import uuid

import clr
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')
from System import AppDomain, Guid
from System.Windows.Controls import Page, TextBlock
from System.Windows import Thickness, TextWrapping
from Autodesk.Revit.UI import (DockablePane, DockablePaneId, DockablePaneState, DockPosition,
                               DockablePanes, IDockablePaneProvider, ExternalEvent,
                               IExternalEventHandler)

PANE_GUID = '3f6d2c1a-8b47-4e0f-9a5d-7c2e91b4d6a8'
PANE_TITLE = u'View Manager'
STATE_KEY = 'EOM.ViewManager.Host.v1'
_NAMESPACE = 'EOM.ViewManager.N' + uuid.uuid4().hex


def pane_id():
    return DockablePaneId(Guid(PANE_GUID))


class Handler(IExternalEventHandler):
    __namespace__ = _NAMESPACE

    def __init__(self, host):
        self.host = host

    def GetName(self):
        return 'EOM View Manager'

    def Execute(self, uiapp):
        controller = self.host.controller
        if controller is not None:
            controller.execute(uiapp)


class Host(IDockablePaneProvider):
    __namespace__ = _NAMESPACE

    def __init__(self, uiapp):
        self.uiapp = uiapp
        self.controller = None
        self.page = Page()
        hint = TextBlock()
        hint.Text = u'Нажмите кнопку View Manager на вкладке MyRvtTools.'
        hint.TextWrapping = TextWrapping.Wrap
        hint.Margin = Thickness(10)
        self.page.Content = hint
        self.handler = Handler(self)
        self.event = ExternalEvent.Create(self.handler)

    def SetupDockablePane(self, data):
        data.FrameworkElement = self.page
        data.VisibleByDefault = False
        state = DockablePaneState()
        state.DockPosition = DockPosition.Tabbed
        state.TabBehind = DockablePanes.BuiltInDockablePanes.ProjectBrowser
        data.InitialState = state

    def subscribe(self):
        self.uiapp.ViewActivated += self.on_changed
        self.uiapp.Application.DocumentChanged += self.on_changed
        self.uiapp.Application.DocumentClosed += self.on_changed
        self.uiapp.Idling += self.on_idle

    def on_changed(self, sender, args):
        controller = self.controller
        if controller is not None:
            controller.dirty = True

    def on_idle(self, sender, args):
        controller = self.controller
        if controller is not None:
            controller.on_idle(sender)

    def bind(self, controller):
        """Подключить новую логику/интерфейс панели (без перезапуска Revit)."""
        old = self.controller
        self.controller = controller
        self.page.Content = controller.root
        controller.dirty = True
        if old is not None:
            try:
                old.detach()
            except Exception:
                pass


def get_host():
    return AppDomain.CurrentDomain.GetData(STATE_KEY)


def install(uiapp):
    """Зарегистрировать панель (один раз за сеанс Revit) и вернуть оболочку."""
    existing = get_host()
    if existing is not None:
        return existing
    if DockablePane.PaneIsRegistered(pane_id()):
        raise RuntimeError(u'Панель View Manager уже зарегистрирована. Перезапустите Revit.')
    host = Host(uiapp)
    try:
        uiapp.RegisterDockablePane(pane_id(), PANE_TITLE, host)
    except Exception:
        host.event.Dispose()
        raise
    AppDomain.CurrentDomain.SetData(STATE_KEY, host)
    host.subscribe()
    return host
