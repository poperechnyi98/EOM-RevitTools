# MyRvtTools project

This is a local pyRevit extension for Autodesk Revit.

- Target the installed pyRevit 5.2 environment.
- Write button scripts with Python 2 / IronPython-compatible syntax unless the user explicitly requests another engine.
- Do not change the Revit model without a clear user request. First commands should be read-only or provide an explicit confirmation.
- Keep each command in its own `.pushbutton` bundle, include a brief `bundle.yaml` description, and report errors clearly.
- The user tests all changes in Revit after pyRevit Reload, preferably on a copy of the model.
