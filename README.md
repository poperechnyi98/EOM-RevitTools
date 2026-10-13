# MyRvtTools

Personal pyRevit extension maintained through Codex chats.

## Location

`C:\Users\poperechnyi\Documents\Codex\RevitTools\MyRvtTools.extension`

pyRevit must use the parent directory `C:\Users\poperechnyi\Documents\Codex\RevitTools` as a Custom Extension Directory.

## First test

After the extension directory is connected in pyRevit, open any non-production Revit model, select a few elements, choose **MyRvtTools → Проверка → Показать выбранное**, and inspect the pyRevit output window. The command only reads the current selection.

## Daily workflow

1. Describe the required Revit operation in a Codex chat.
2. Codex edits this extension folder directly.
3. In Revit, use **pyRevit → Reload**.
4. Test on a copy of the model and report the result or full error text.
5. Once the command works, commit it to Git. Remote sharing can be added later.
