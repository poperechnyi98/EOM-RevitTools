# -*- coding: utf-8 -*-
"""Обновляет расширение EOM-RevitTools из GitHub и перезагружает pyRevit.

Штатная кнопка pyRevit -> Update перед обновлением проверяет интернет
подключением к 8.8.8.8:53 и в некоторых сетях ошибочно пропускает обновление.
Эта кнопка обновляет только наше расширение напрямую через git, без такой
проверки. Модель Revit не изменяется.
"""

import os

import pyrevit.coreutils.git as libgit
from pyrevit import forms
from pyrevit import script


logger = script.get_logger()


def find_extension_repo():
    """Найти git-репозиторий, в котором лежит эта кнопка."""
    here = os.path.dirname(os.path.abspath(__file__))
    repo_path = libgit.libgit.Repository.Discover(here)
    if not repo_path:
        return None
    return libgit.get_repo(repo_path)


def short_message(repo_info):
    msg = repo_info.repo.Head.Tip.MessageShort or u''
    return u'{} {}'.format(repo_info.last_commit_hash[:7], msg)


def main():
    repo_info = find_extension_repo()
    if not repo_info:
        forms.alert(
            u'Расширение установлено не из GitHub (нет папки .git), '
            u'поэтому обновить его этой кнопкой нельзя.\n\n'
            u'Переустановите расширение командой из README:\n'
            u'pyrevit extend ui EOM-RevitTools '
            u'https://github.com/poperechnyi98/EOM-RevitTools.git --branch=main',
            title=u'Обновление EOM-RevitTools')
        return

    before = short_message(repo_info)
    try:
        updated = libgit.git_pull(repo_info)
    except Exception as err:
        logger.debug('git pull failed: %s', err)
        forms.alert(
            u'Не удалось скачать обновление с GitHub.\n\n'
            u'Проверьте доступ к github.com. Если вы меняли файлы '
            u'расширения вручную, их нужно вернуть или переустановить '
            u'расширение.\n\nТекст ошибки:\n{}'.format(err),
            title=u'Обновление EOM-RevitTools')
        return

    after = short_message(updated)
    if updated.last_commit_hash == repo_info.last_commit_hash:
        # Файлы могли измениться в папке расширения и без скачивания
        # (локальная разработка) — предлагаем перечитать их в ленту.
        if forms.alert(
                u'Обновлений нет, установлена последняя версия.\n\n{}\n\n'
                u'Перезагрузить pyRevit, чтобы подхватить изменения '
                u'в папке расширения?'.format(after),
                title=u'Обновление EOM-RevitTools', yes=True, no=True):
            from pyrevit.loader import sessionmgr
            sessionmgr.reload_pyrevit()
        return

    forms.alert(
        u'Расширение обновлено.\n\nБыло:  {}\nСтало: {}\n\n'
        u'Сейчас pyRevit перезагрузится.'.format(before, after),
        title=u'Обновление EOM-RevitTools')

    from pyrevit.loader import sessionmgr
    sessionmgr.reload_pyrevit()


if __name__ == '__main__':
    main()
