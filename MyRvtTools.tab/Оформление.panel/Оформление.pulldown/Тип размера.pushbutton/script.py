# -*- coding: utf-8 -*-
"""Сохранение типа размера для команд MyRvtTools в текущем проекте."""
from Autodesk.Revit.DB import (BuiltInParameter, DimensionStyleType,
                               DimensionType, FilteredElementCollector)
from pyrevit import forms, revit, script


__title__ = u"Тип\nразмера"
__doc__ = u"Выбирает тип линейного размера для текущего проекта."

doc = revit.doc


class DimensionTypeOption(object):
    def __init__(self, dimension_type):
        self.dimension_type = dimension_type
        parameter = dimension_type.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM)
        value = parameter.AsString() if parameter else None
        self.name = value or u"Тип размера {}".format(dimension_type.Id.IntegerValue)

    def __str__(self):
        return self.name


def _project_key():
    try:
        unique_id = doc.ProjectInformation.UniqueId
    except Exception:
        unique_id = doc.Title
    return u"dimension_type_{}".format(unique_id.replace(u"-", u"_"))


options = []
for dimension_type in FilteredElementCollector(doc).OfClass(DimensionType):
    try:
        if dimension_type.StyleType == DimensionStyleType.Linear:
            options.append(DimensionTypeOption(dimension_type))
    except Exception:
        pass

if not options:
    forms.alert(u"В проекте нет доступных линейных типов размеров.",
                title=u"Тип размера", exitscript=True)

choice = forms.SelectFromList.show(
    sorted(options, key=lambda option: unicode(option)),
    title=u"Тип линейного размера",
    button_name=u"Сохранить",
    multiselect=False)

if choice:
    config = script.get_config(u"myrvttools_dimension_type")
    setattr(config, _project_key(), choice.dimension_type.UniqueId)
    script.save_config()
    forms.alert(u"Тип размера «{}» сохранён для текущего проекта.".format(
        unicode(choice)), title=u"Тип размера")
