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

## View Manager (dockable pane)

- `startup.py` registers the pane once per Revit session (`lib/eomtools/vm_host.py`, kept minimal and stable) and on every pyRevit reload binds a fresh `vm_ui.Controller` — so changes in `vm_ui.py`, `vm_revit.py`, `vm_profile.py` and the XAML apply after pyRevit Reload; changes in `vm_host.py` need a Revit restart.
- Model reads/writes happen only in ExternalEvent/Idling. Every action is one named transaction `View Manager: …` (Ctrl+Z must undo exactly one click).
- Never modify a view template unless the profile's `allow_template` is on.

## Development workflow (author's PC)

- This working copy (`Desktop\Игрушки с ИИ\Плагин\Claude`) is what pyRevit loads on the author's PC: `%APPDATA%\pyRevit\Extensions\EOM-RevitTools.extension` is a directory junction to it. The original pyRevit clone was kept as `EOM-RevitTools.backup-appdata` in the same folder.
- Edit files here directly; do not touch the AppData folder. The author tests local changes with pyRevit → Reload (the Обновить button only downloads from GitHub). Script changes in existing buttons apply on the next click. New buttons/panels: with the pyRevit 6 new loader a Reload can create the ribbon button without its command class ("class not found" on click) — tell the author to restart Revit after adding a button.
- Work on the `dev` branch. When the author confirms a change works in Revit, merge it into `main` and push — colleagues then get it via the Обновить button.
