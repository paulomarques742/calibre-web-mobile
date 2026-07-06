# -*- coding: utf-8 -*-

#   This file is part of the Calibre-Web (https://github.com/janeczku/calibre-web)
#   calibre-web-mobile fork: background task to (re)build the FTS5 search index.
#
#   This program is free software: you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation, either version 3 of the License, or
#   (at your option) any later version.

from flask_babel import lazy_gettext as N_

from cps import logger, fts
from cps.services.worker import CalibreTask


class TaskRebuildFTS(CalibreTask):
    def __init__(self, task_message=N_('Rebuilding search index')):
        super(TaskRebuildFTS, self).__init__(task_message)
        self.log = logger.create()

    def run(self, worker_thread):
        try:
            fts.rebuild()
            self._handleSuccess()
        except Exception as ex:
            self.log.error("FTS rebuild task failed: %s", ex)
            self._handleError(str(ex))

    @property
    def name(self):
        return "Rebuild Search Index"

    @property
    def is_cancellable(self):
        return False
