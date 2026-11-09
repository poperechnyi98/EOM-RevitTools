# MyRvtTools project

This is a pyRevit extension for Autodesk Revit, distributed from GitHub (`poperechnyi98/EOM-RevitTools`).

- Target the installed pyRevit 5.2 environment.
- Write button scripts with Python 2 / IronPython-compatible syntax unless the user explicitly requests another engine.
- Do not change the Revit model without a clear user request. First commands should be read-only or provide an explicit confirmation.
- Keep each command in its own `.pushbutton` bundle, include a brief `bundle.yaml` description, and report errors clearly.
- The user tests all changes in Revit after pyRevit Reload, preferably on a copy of the model.

## Repository layout and distribution

- The repository root is the extension root: `MyRvtTools.tab` lives directly in the root. Do not wrap the `.tab` folder in a `*.extension` folder — users install the repo with `pyrevit extend ui`, which clones it into a folder that is already an extension.
- `extensions.json` in the root describes the extension for pyRevit Custom Extension Sources; keep it valid JSON.
- Shared code lives in `lib/eomtools` (pyRevit puts the extension's `lib` folder on `sys.path`; import as `from eomtools import ...`).
- Buttons marked as ported from LowLife (github.com/avpersiyanov-afk/LowLife, MIT) use `lib/eomtools`. Keep `lib/eomtools/LICENSE-LowLife.txt`. Local settings files are named `EOM*_settings.json`; model-facing identifiers (Extensible Storage schema GUIDs/names, shared parameter GUIDs and group "LowLife", updater GUID) were deliberately kept identical to LowLife for compatibility with models it already processed — do not change them.
- Push to `main` only changes that were tested in Revit: pyRevit → Update delivers `main` to every user immediately. Use a separate branch for work in progress.
