# -*- coding: utf-8 -*-
"""
Конец выноски марки «держится» за УГО прибора — дополнение к кнопке
«Марки оборудования» (Tools.panel/TagEquipment).

Revit умеет вести конец выноски за элементом только в режиме
``LeaderEndCondition.Attached``, но тогда сам ставит конец на 3D-тело
семейства, а не на вложенное УГО (проверено на модели: опорные плоскости
семейства марковать нельзя — «The reference can not be tagged», а
присоединённый конец ложится на грань тела). Поэтому конец выноски
свободный (``Free``) и стоит на краю УГО, а «привязку» делаем сами:

  1. Кнопка, поставив выноску, запоминает в марке (Extensible Storage,
     схема :data:`SCHEMA_GUID`) положение конца выноски в системе
     координат прибора: ``el.GetTransform().Inverse.OfPoint(end)``
     (:func:`remember`).
  2. Updater (:class:`LeaderFollowUpdater`, регистрируется при запуске
     Revit из ``hooks/app-init.py``, запасной путь —
     ``hooks/doc-opened.py``) ловит изменение геометрии экземпляров
     семейств (сдвиг, поворот, отражение) и ставит конец выноски каждой
     такой марки в ``T_new.OfPoint(local)`` — излом и голова марки
     остаются на месте, как у присоединённой выноски Revit.
  3. Если поправить саму марку (перетащить конец выноски), а прибор не
     двигался — updater перезаписывает запомненное положение, и дальше
     конец ездит за прибором уже от нового места.

Updater необязательный (``isOptional=True``): у пользователей без LowLife
модель открывается без предупреждений, просто конец выноски при сдвиге
прибора у них остаётся на месте. Марки, занятые другим пользователем
(совместная работа), не трогаются.
"""

from System import Guid, String

from Autodesk.Revit.DB import (
    ChangePriority, Element, ElementClassFilter, FamilyInstance,
    IndependentTag, LeaderEndCondition, Reference, UpdaterId,
    UpdaterRegistry, WorksharingUtils, CheckoutStatus, XYZ, IUpdater
)
from Autodesk.Revit.DB.ExtensibleStorage import (
    AccessLevel, Entity, Schema, SchemaBuilder
)

SCHEMA_GUID = Guid("6c1d2f0e-6b0a-4d55-9a3e-2b8f6e4a7c11")
UPDATER_GUID = Guid("b7f4c8a2-3d1e-4f6a-8c5b-9e0d2a1f3b64")
FIELD = "EndLocal"


def _id_int(eid):
    try:
        return eid.Value  # Revit 2024+
    except AttributeError:
        return eid.IntegerValue


# ------------------------------------------------------------
# ХРАНЕНИЕ В МАРКЕ
# ------------------------------------------------------------

def get_schema(create=False):
    schema = Schema.Lookup(SCHEMA_GUID)
    if schema is None and create:
        sb = SchemaBuilder(SCHEMA_GUID)
        sb.SetSchemaName("LowLifeTagLeaderFollow")
        sb.SetDocumentation(u"Конец выноски марки в координатах прибора (LowLife)")
        sb.SetReadAccessLevel(AccessLevel.Public)
        sb.SetWriteAccessLevel(AccessLevel.Public)
        sb.AddSimpleField(FIELD, String)
        schema = sb.Finish()
    return schema


def _read(tag, schema):
    try:
        ent = tag.GetEntity(schema)
        if ent is None or not ent.IsValid():
            return None
        x, y, z = [float(v) for v in ent.Get[String](FIELD).split(";")]
        return XYZ(x, y, z)
    except Exception:
        return None


def _write(tag, schema, local):
    ent = Entity(schema)
    ent.Set[String](FIELD, u"{:.9f};{:.9f};{:.9f}".format(local.X, local.Y, local.Z))
    tag.SetEntity(ent)


def _tag_ref(tag, el):
    try:
        refs = list(tag.GetTaggedReferences())  # 2022+
        for r in refs:
            if r.ElementId == el.Id:
                return r
        if refs:
            return refs[0]
    except AttributeError:
        pass
    return Reference(el)


def _leader_end(tag, ref):
    try:
        return tag.GetLeaderEnd(ref)  # 2022+
    except AttributeError:
        return tag.LeaderEnd


def _set_leader_end(tag, ref, point):
    try:
        tag.SetLeaderEnd(ref, point)  # 2022+
    except AttributeError:
        tag.LeaderEnd = point


def remember(tag, el, end):
    """Запомнить конец выноски end (XYZ) в координатах прибора el.
    Вызывать внутри транзакции, после установки свободного конца."""
    _write(tag, get_schema(create=True), el.GetTransform().Inverse.OfPoint(end))


def forget(tag):
    """Марке больше не следовать за прибором (например, выноска стала
    присоединённой — Revit ведёт её сам)."""
    schema = get_schema()
    if schema is None:
        return
    try:
        if tag.GetEntity(schema).IsValid():
            tag.DeleteEntity(schema)
    except Exception:
        pass


# ------------------------------------------------------------
# UPDATER
# ------------------------------------------------------------

def _editable(doc, eid):
    try:
        if not doc.IsWorkshared:
            return True
        return WorksharingUtils.GetCheckoutStatus(doc, eid) != CheckoutStatus.OwnedByOtherUser
    except Exception:
        return True


def _single_element(doc, tag):
    try:
        ids = list(tag.GetTaggedLocalElementIds())  # 2022+
    except AttributeError:
        try:
            ids = [tag.TaggedLocalElementId]
        except Exception:
            ids = []
    if len(ids) != 1:
        return None
    el = doc.GetElement(ids[0])
    return el if isinstance(el, FamilyInstance) else None


def _following(tag):
    try:
        return tag.HasLeader and tag.LeaderEndCondition == LeaderEndCondition.Free
    except Exception:
        return False


def follow(doc, tag, el, schema):
    """Поставить конец выноски туда, где он был относительно прибора.
    Глубина (по направлению взгляда вида) — как у нынешнего конца."""
    local = _read(tag, schema)
    if local is None or not _following(tag) or not _editable(doc, tag.Id):
        return
    ref = _tag_ref(tag, el)
    old = _leader_end(tag, ref)
    p = el.GetTransform().OfPoint(local)
    view = doc.GetElement(tag.OwnerViewId)
    if view is not None:
        d = view.ViewDirection
        p = p + d.Multiply((old - p).DotProduct(d))
    if p.DistanceTo(old) > 1e-6:
        _set_leader_end(tag, ref, p)


def rerecord(doc, tag, el, schema):
    """Марку поправили вручную — запомнить её нынешний конец выноски."""
    local = _read(tag, schema)
    if local is None or not _following(tag) or not _editable(doc, tag.Id):
        return
    new = el.GetTransform().Inverse.OfPoint(_leader_end(tag, _tag_ref(tag, el)))
    if new.DistanceTo(local) > 1e-6:
        _write(tag, schema, new)


class LeaderFollowUpdater(IUpdater):

    def __init__(self, addin_id):
        self._id = UpdaterId(addin_id, UPDATER_GUID)

    def GetUpdaterId(self):
        return self._id

    def GetUpdaterName(self):
        return "LowLife: tag leader follows symbol"

    def GetAdditionalInformation(self):
        return u"LowLife: конец выноски марки оборудования следует за УГО прибора"

    def GetChangePriority(self):
        return ChangePriority.Annotations

    def Execute(self, data):
        try:
            schema = get_schema()
            if schema is None:
                return  # в этой сессии ни одной «следящей» марки не было
            doc = data.GetDocument()
            modified = list(data.GetModifiedElementIds())
            modified_ints = set(_id_int(i) for i in modified)
            tag_filter = ElementClassFilter(IndependentTag)
            for eid in modified:
                el = doc.GetElement(eid)
                try:
                    if isinstance(el, FamilyInstance):
                        # сдвинули прибор (и, может быть, марку вместе с ним —
                        # тогда конец уже на месте, follow ничего не меняет)
                        for tid in el.GetDependentElements(tag_filter):
                            tag = doc.GetElement(tid)
                            if isinstance(tag, IndependentTag) and _single_element(doc, tag) is not None:
                                follow(doc, tag, el, schema)
                    elif isinstance(el, IndependentTag):
                        host = _single_element(doc, el)
                        if host is not None and _id_int(host.Id) not in modified_ints:
                            rerecord(doc, el, host, schema)
                except Exception:
                    continue
        except Exception:
            pass


def install(app, force=False):
    """
    Зарегистрировать updater (все документы). app — UIApplication или
    Application (нужен ActiveAddInId). force — перерегистрировать, даже если
    уже есть (после перезагрузки pyRevit — чтобы работал свежий код);
    без force — только если ещё не зарегистрирован. Возвращает True, если
    updater зарегистрирован.
    """
    addin_id = app.ActiveAddInId
    if addin_id is None:
        return False
    updater = LeaderFollowUpdater(addin_id)
    uid = updater.GetUpdaterId()
    if UpdaterRegistry.IsUpdaterRegistered(uid):
        if not force:
            return True
        UpdaterRegistry.UnregisterUpdater(uid)
    UpdaterRegistry.RegisterUpdater(updater, True)
    UpdaterRegistry.AddTrigger(uid, ElementClassFilter(FamilyInstance),
                               Element.GetChangeTypeGeometry())
    UpdaterRegistry.AddTrigger(uid, ElementClassFilter(IndependentTag),
                               Element.GetChangeTypeAny())
    return True
