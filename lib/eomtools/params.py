# -*- coding: utf-8 -*-
"""Общие хелперы чтения/записи параметров элементов Revit."""

try:
    unicode  # noqa: F821 — IronPython/Python 2
except NameError:  # Python 3
    unicode = str


from Autodesk.Revit.DB import StorageType, ElementId


def set_element_id_param(el, name, element_id):
    """
    Записывает ElementId в параметр типа StorageType.ElementId (например
    «Проводник» электрической цепи — ссылка на WireType, выбирается в
    Revit выпадающим списком, а не текстом/SetValueString). Возвращает
    True, если запись удалась.
    """
    try:
        if el is None or element_id is None:
            return False

        p = el.LookupParameter(name)
        if not p or p.IsReadOnly or p.StorageType != StorageType.ElementId:
            return False

        p.Set(element_id)
        return True
    except:
        return False


def get_double_param(el, names):
    """Первое числовое значение параметра из списка возможных имён."""
    for name in names:
        try:
            p = el.LookupParameter(name)
            if p and p.HasValue and p.StorageType == StorageType.Double:
                return p.AsDouble()
        except:
            pass
    return None


def set_double_param(el, names, value):
    for name in names:
        try:
            p = el.LookupParameter(name)
            if p and not p.IsReadOnly and p.StorageType == StorageType.Double:
                p.Set(value)
        except:
            pass


def set_string_param(el, name, value):
    """Возвращает True, если запись удалась (параметр найден, текстовый и не read-only)."""
    try:
        p = el.LookupParameter(name)
        if p and not p.IsReadOnly and p.StorageType == StorageType.String:
            p.Set(u"{}".format(value if value is not None else ""))
            return True
    except:
        pass
    return False


def set_string_param_including_type(doc, el, name, value):
    """
    Как set_string_param, но если у экземпляра такого параметра нет или он
    недоступен для записи — пробует записать на ТИП элемента (GetTypeId()).
    Нужно для параметров, заведённых в проекте как «Тип» (Type), а не
    «Экземпляр» — в диалоге свойств экземпляра такой параметр виден, но
    показан серым/неактивным; el.LookupParameter на самом экземпляре его
    не находит (или находит как read-only), в отличие от get_type_string_param
    при чтении.
    """
    if set_string_param(el, name, value):
        return True

    try:
        type_el = doc.GetElement(el.GetTypeId())
    except:
        type_el = None

    if type_el is None:
        return False

    return set_string_param(type_el, name, value)


def get_string_param(el, name):
    """Строковое значение параметра: AsString для текстовых, иначе AsValueString."""
    try:
        p = el.LookupParameter(name)
        if not p or not p.HasValue:
            return None
        if p.StorageType == StorageType.String:
            return p.AsString()
        return p.AsValueString()
    except:
        return None


def get_type_string_param(doc, el, name):
    """
    Строковое значение параметра типа элемента (Type Parameter — например
    «Обозначение», общее у всех экземпляров одного типа семейства).
    el.LookupParameter ищет только среди параметров экземпляра и не видит
    параметры типа, поэтому здесь явно берём тип через GetTypeId().
    """
    try:
        type_el = doc.GetElement(el.GetTypeId())
    except:
        return None
    if type_el is None:
        return None
    return get_string_param(type_el, name)


def get_param_any(el, name):
    """
    Строковое представление значения параметра любого типа хранения
    (String/Integer/Double/ElementId). Для чисел без текстового
    представления (AsValueString) возвращает строку от AsInteger/AsDouble.
    """
    try:
        if el is None:
            return None

        p = el.LookupParameter(name)
        if not p or not p.HasValue:
            return None

        if p.StorageType == StorageType.String:
            v = p.AsString()
            return v.strip() if v else None

        if p.StorageType == StorageType.Integer:
            v = p.AsValueString()
            if v:
                return v.strip()
            try:
                return str(p.AsInteger())
            except:
                return None

        if p.StorageType == StorageType.Double:
            v = p.AsValueString()
            if v:
                return v.strip()
            try:
                return str(p.AsDouble())
            except:
                return None

        if p.StorageType == StorageType.ElementId:
            v = p.AsValueString()
            return v.strip() if v else None

        return None
    except:
        return None


def set_param_any(el, name, value):
    """
    Записывает value в параметр любого типа хранения, подбирая подходящий
    способ (Set(str)/Set(int)/Set(float)/SetValueString). Возвращает True,
    если запись удалась.
    """
    try:
        if el is None:
            return False

        p = el.LookupParameter(name)
        if not p or p.IsReadOnly:
            return False

        if value is None:
            value = ""

        try:
            if p.StorageType == StorageType.String:
                p.Set(str(value))
                return True

            if p.StorageType == StorageType.Integer:
                try:
                    p.Set(int(value))
                    return True
                except:
                    p.SetValueString(str(value))
                    return True

            if p.StorageType == StorageType.Double:
                try:
                    p.Set(float(value))
                    return True
                except:
                    p.SetValueString(str(value))
                    return True

            p.SetValueString(str(value))
            return True
        except:
            try:
                p.Set(str(value))
                return True
            except:
                try:
                    p.SetValueString(str(value))
                    return True
                except:
                    return False
    except:
        return False


def param_to_text(p):
    u"""
    Значение параметра текстом «как видно в Revit»: строка как есть, числа —
    AsValueString (в единицах проекта), ссылка на элемент — её имя. Пустое
    значение / None -> u"". Пара к set_param_text (обмен с Excel/JSON).
    """
    if p is None or not p.HasValue:
        return u""
    st = p.StorageType
    try:
        if st == StorageType.String:
            return p.AsString() or u""
        if st == StorageType.Integer:
            vs = p.AsValueString()
            return vs if vs is not None else unicode(p.AsInteger())
        if st == StorageType.Double:
            vs = p.AsValueString()
            return vs if vs is not None else unicode(p.AsDouble())
        if st == StorageType.ElementId:
            vs = p.AsValueString()
            if vs:
                return vs
            eid = p.AsElementId()
            return unicode(eid.IntegerValue) if eid is not None else u""
    except Exception:
        return u""
    return u""


def set_param_text(p, text):
    u"""
    Записать текст в параметр, подобрав способ под тип хранения: строка —
    Set, число с единицами — SetValueString (пробуя и точку, и запятую),
    целое / «Да-Нет» — да/нет/1/0. ElementId не пишется. True, если записалось.
    """
    st = p.StorageType
    try:
        if st == StorageType.String:
            return bool(p.Set(text))
        if st == StorageType.Double:
            if p.SetValueString(text):
                return True
            alt = text.replace(u".", u",") if u"." in text else text.replace(u",", u".")
            if alt != text and p.SetValueString(alt):
                return True
            return bool(p.Set(float(text.replace(u",", u"."))))
        if st == StorageType.Integer:
            t = text.strip().lower()
            if t in (u"да", u"yes", u"true", u"истина", u"1", u"x", u"✓"):
                return bool(p.Set(1))
            if t in (u"нет", u"no", u"false", u"ложь", u"0", u"-", u""):
                return bool(p.Set(0))
            return bool(p.Set(int(round(float(text.replace(u",", u"."))))))
    except Exception:
        return False
    return False


def read_project_parameter(doc,ref):
    """Project Information reference: parameter name or Name [BUILTIN_NAME]."""
    from Autodesk.Revit.DB import BuiltInParameter
    if not ref:
        return u''
    name,bipname,param=ref,None,None
    if ref.endswith(u']') and u' [' in ref:
        name,bipname=ref[:-1].rsplit(u' [',1)
    if bipname:
        try:
            param=doc.ProjectInformation.get_Parameter(getattr(BuiltInParameter,bipname))
        except Exception:
            param=None
    if param is None:
        param=doc.ProjectInformation.LookupParameter(name)
    return (param_to_text(param) or u'').strip()
