# -*- coding: utf-8 -*-
"""Display Revit link types by their own views on multiple host views.

Revit imports are deferred so validation and transaction behaviour can be
tested without Revit. All settings belong to the host view.
"""

try:
    unicode
except NameError:
    unicode = str

from eomlab_coord.compat import tr

CUSTOM_VIEW = '__custom__'


class CustomView(object):
    """Custom display with an explicitly selected linked view (or keep current)."""
    def __init__(self, view=None):
        self.view = view


def _choice(choice):
    if isinstance(choice, CustomView):
        return True, choice.view
    return (True, None) if choice == CUSTOM_VIEW else (False, choice)


def supports_custom(doc):
    """SetLinkOverrides supports Custom only starting with Revit 2025."""
    return int(doc.Application.VersionNumber) >= 2025


def _db():
    from Autodesk.Revit import DB
    return DB


def safe_name(element):
    try:
        return _db().Element.Name.GetValue(element)
    except Exception:
        return unicode(element.Id)


def display_view_name(view):
    """Return the company display form without changing the Revit name."""
    raw = safe_name(view)
    if (len(raw) >= 2 and raw[0].isdigit()
            and raw[1] not in (u'-', u'_', u' ')):
        return raw[0] + u'-' + raw[1:]
    return raw


def is_top_level_link(link_type):
    """Return True only for a link inserted directly into the host model."""
    if link_type is None:
        return True
    try:
        if link_type.IsNestedLink:
            return False
    except Exception:
        pass
    # GetParentId is available in newer Revit versions. Keep the explicit
    # IsNestedLink check above for older versions and use this as a second
    # guard where the API exposes the parent relationship.
    try:
        parent = link_type.GetParentId()
        raw = getattr(parent, 'Value', None)
        if raw is None:
            raw = getattr(parent, 'IntegerValue', None)
        if raw is not None and int(raw) >= 0:
            return False
    except Exception:
        pass
    return True


def template_controls_links(doc, view):
    """Return whether the active view template owns the Revit Links setting."""
    if view is None or getattr(view, 'IsTemplate', False):
        return False
    try:
        template = doc.GetElement(view.ViewTemplateId)
        if template is None:
            return False
        db = _db()
        param = db.ElementId(db.BuiltInParameter.VIS_GRAPHICS_RVT_LINKS)
        controlled = template.GetTemplateParameterIds()
        free = template.GetNonControlledTemplateParameterIds()
        return any(p == param for p in controlled) and not any(p == param for p in free)
    except Exception:
        return False


def availability(doc, view, allow_template=False):
    """Explain unsupported views/templates before showing an editable dialog."""
    db = _db()
    try:
        if (hasattr(view, 'IsTemporaryViewPropertiesModeEnabled')
                and view.IsTemporaryViewPropertiesModeEnabled()):
            return tr(u"Temporary View Properties mode is active. Turn it off and run the command again.",
                      u"Temporary View Properties mode is active. Turn it off and run the command again.")
    except Exception:
        pass
    if not hasattr(view, 'SetLinkOverrides') or not hasattr(db, 'RevitLinkGraphicsSettings'):
        return tr(u"Настройка видов связей требует Revit 2024 или новее.",
                  u"Linked view settings require Revit 2024 or later.")
    if view.IsTemplate or not view.AreGraphicsOverridesAllowed():
        return tr(u"На текущем виде нельзя настроить отображение связей.",
                  u"Link display cannot be configured on the current view.")
    template = doc.GetElement(view.ViewTemplateId)
    if template is not None:
        param = db.ElementId(db.BuiltInParameter.VIS_GRAPHICS_RVT_LINKS)
        controlled = template.GetTemplateParameterIds()
        free = template.GetNonControlledTemplateParameterIds()
        if (any(p == param for p in controlled)
                and not any(p == param for p in free)):
            return tr(u"Отображение связей задаёт шаблон «{}». Исключите «Связи Revit» из управления шаблоном и повторите команду.",
                      u'Link display is controlled by template "{}". Exclude "Revit Links" from template control and run the command again.').format(safe_name(template))
    return None


def compatible_views(link_doc, host_view):
    """Conservative candidates: the same view type, never templates."""
    db = _db()
    return sorted([v for v in db.FilteredElementCollector(link_doc).OfClass(db.View)
                   if not v.IsTemplate and v.ViewType == host_view.ViewType],
                  key=lambda v: safe_name(v).lower())


def instances_by_type(doc):
    groups = {}
    db = _db()
    for link in db.FilteredElementCollector(doc).OfClass(db.RevitLinkInstance):
        groups.setdefault(unicode(link.GetTypeId()), []).append(link)
    return groups


def current_settings(host_view, type_id, instances):
    """Read the type settings, with a fallback for older instance assignments."""
    db = _db()
    settings = host_view.GetLinkOverrides(type_id)
    if settings is not None and settings.LinkVisibilityType != db.LinkVisibility.ByHostView:
        return settings, False
    legacy = {}
    for link in instances:
        override = host_view.GetLinkOverrides(link.Id)
        if override is not None and override.LinkVisibilityType != db.LinkVisibility.ByHostView:
            legacy[(override.LinkVisibilityType, unicode(override.LinkedViewId))] = override
    if len(legacy) == 1:
        return next(iter(legacy.values())), False
    return settings, len(legacy) > 1


def settings_owner(doc, host_view, allow_template=False):
    """Return the active view; templates are never edited by this command."""
    return host_view


def collect_rows(doc, host_view, allow_template=False):
    db = _db()
    custom_available = supports_custom(doc)
    rows = []
    for instances in instances_by_type(doc).values():
        instances.sort(key=lambda item: unicode(item.Id))
        link = instances[0]
        linked_doc = link.GetLinkDocument()
        try:
            link_type = doc.GetElement(link.GetTypeId())
        except Exception:
            link_type = None
        if not is_top_level_link(link_type):
            # Nested links are governed by the parent linked model. They
            # cannot be assigned an independent host-view override here.
            continue
        display_name = safe_name(link_type) if link_type is not None else safe_name(link)
        views = compatible_views(linked_doc, host_view) if linked_doc else []
        reason = None
        if linked_doc is None:
            reason = tr(u"Связь не загружена", u"Link is unloaded")
        elif not views and not custom_available:
            reason = tr(u"Нет видов такого же типа", u"No views of the same type")
        settings = None
        mixed = False
        if linked_doc:
            # Read the effective override from the active host view only.
            owner = settings_owner(doc, host_view, False)
            try:
                settings, mixed = current_settings(owner, link.GetTypeId(), instances)
            except Exception:
                settings = None
        current_view = None
        current = tr(u"По основному виду", u"By host view")
        if mixed:
            current = tr(u"Разные настройки экземпляров", u"Mixed instance settings")
        elif settings is not None:
            if settings.LinkVisibilityType == db.LinkVisibility.ByLinkView:
                linked_view = linked_doc.GetElement(settings.LinkedViewId)
                current_view = linked_view
                current = (safe_name(linked_view) if linked_view else
                           tr(u"Связанный вид недоступен", u"Linked view is unavailable"))
            elif settings.LinkVisibilityType == db.LinkVisibility.Custom:
                current = tr(u"Пользовательские настройки", u"Custom settings")
        rows.append(dict(link=link, instances=instances, name=display_name,
                         views=views, reason=reason, current=current,
                         current_view=current_view,
                         custom_available=custom_available and linked_doc is not None))
    return sorted(rows, key=lambda row: (row['name'].lower(), unicode(row['link'].GetTypeId())))


def collect_many_rows(doc, host_views):
    """Show shared settings, or an explicit mixed value across host views."""
    rows = collect_rows(doc, host_views[0])
    for host in host_views[1:]:
        other = dict((unicode(r['link'].GetTypeId()), r) for r in collect_rows(doc, host))
        for row in rows:
            if row['current'] != other[unicode(row['link'].GetTypeId())]['current']:
                row['current'] = tr(u"Разные значения", u"Mixed values")
    return rows


def apply(doc, host_view, choices):
    return apply_many(doc, [host_view], choices)


def apply_many(doc, host_views, choices):
    choices = list(choices)
    return apply_assignments(doc, [(host, choices) for host in host_views])


def apply_assignments(doc, assignments, before_commit=None, allow_template=False,
                      removals=None):
    """Apply the linked view to each link type in one Revit Undo step.

    Read the existing settings so other supported graphics properties survive.
    Revit validates the actual compatibility in SetLinkOverrides. Any rejection
    rolls back the entire batch, including failures during commit.
    """
    db = _db()
    assignments = [(host, list(choices)) for host, choices in assignments]
    removals = [(host, list(links)) for host, links in (removals or [])]
    seen = set()
    removal_seen = set()
    for host_view, choices in assignments:
        problem = availability(doc, host_view, allow_template)
        if problem:
            raise ValueError(u"{}: {}".format(safe_name(host_view), problem))
        for link, choice in choices:
            custom, linked_view = _choice(choice)
            key = (host_view, unicode(link.GetTypeId()))
            if key in seen:
                raise ValueError(tr(u"Связь выбрана повторно: {}", u"Link selected twice: {}").format(safe_name(link)))
            seen.add(key)
            linked_doc = link.GetLinkDocument()
            if custom:
                if not supports_custom(doc):
                    raise ValueError(tr(u"Пользовательские настройки связей требуют Revit 2025 или новее.",
                                        u"Custom link settings require Revit 2025 or later."))
                if linked_doc is None:
                    raise ValueError(tr(u"Связь не загружена", u"Link is unloaded"))
                if linked_view is None:
                    continue
            if (linked_doc is None or linked_view.Document != linked_doc
                    or linked_view.IsTemplate
                    or linked_view.ViewType != host_view.ViewType):
                raise ValueError(tr(u"Вид не подходит для связи: {}", u"View is incompatible with link: {}").format(safe_name(link)))
    for host_view, links in removals:
        problem = availability(doc, host_view, allow_template)
        if problem:
            raise ValueError(u"{}: {}".format(safe_name(host_view), problem))
        for link in links:
            key = (host_view, unicode(link.GetTypeId()))
            if key in removal_seen or key in seen:
                raise ValueError(tr(u"Link selected twice: {}", u"Link selected twice: {}").format(safe_name(link)))
            removal_seen.add(key)
    if not seen and not removal_seen:
        return 0
    instances = instances_by_type(doc)
    transaction = db.Transaction(doc, tr(u"Виды связанных моделей", u"Linked model views"))
    try:
        transaction.Start()
        for host_view, choices in assignments:
            for link, choice in choices:
                custom, linked_view = _choice(choice)
                type_id = link.GetTypeId()
                settings = host_view.GetLinkOverrides(type_id)
                if settings is None:
                    settings = db.RevitLinkGraphicsSettings()
                if custom:
                    settings.LinkVisibilityType = db.LinkVisibility.Custom
                else:
                    settings.LinkVisibilityType = db.LinkVisibility.ByLinkView
                if linked_view is not None:
                    settings.LinkedViewId = linked_view.Id
                host_view.SetLinkOverrides(type_id, settings)
                _remove_instance_overrides(host_view, instances[unicode(type_id)])
        for host_view, links in removals:
            for link in links:
                type_id = link.GetTypeId()
                host_view.RemoveLinkOverrides(type_id)
                _remove_instance_overrides(host_view, instances[unicode(type_id)])
        if before_commit is not None:
            before_commit()
        status = transaction.Commit()
        if status != db.TransactionStatus.Committed:
            raise RuntimeError(tr(u"Revit не сохранил настройки связей.", u"Revit did not commit the link settings."))
    except Exception:
        if transaction.GetStatus() == db.TransactionStatus.Started:
            transaction.RollBack()
        raise
    finally:
        transaction.Dispose()
    return len(seen) + len(removal_seen)


def _remove_instance_overrides(host_view, instances):
    for link in instances:
        if host_view.GetLinkOverrides(link.Id) is not None:
            host_view.RemoveLinkOverrides(link.Id)
