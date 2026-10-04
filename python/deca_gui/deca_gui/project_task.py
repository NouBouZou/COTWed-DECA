"""
Open or scan a project without freezing the window.

DECA's scan (vfs_structure_new / vfs_structure_open -> VfsProcessor.process) runs in the calling thread; started
from the GUI thread it froze the window for the whole scan (about 40 minutes on COTW) and for several seconds on
every opening. ProjectTask runs it in a QThread and reports the DECA log live.

Thread hand-off: the VfsProcessor (and its SQLite connection) is created and used only by the worker thread until
``finished`` is emitted; from then on only the GUI thread uses it. The connection is opened with
check_same_thread=False for this reason (deca.db_types.DbBase).
"""
import os
import sqlite3
import time
import traceback
from typing import Optional

from PySide6.QtCore import QObject, QThread, Signal, Slot

from deca.db_processor import VfsProcessor, vfs_structure_new, vfs_structure_open
from deca.util import Logger

KIND_NEW = 'new'
KIND_OPEN = 'open'

# PRAGMA user_version of a project database (deca.db_processor.VfsProcessor.process)
SCAN_STARTED = 1
SCAN_COMPLETE = 2


def project_scan_state(project_file: str) -> Optional[int]:
    """
    PRAGMA user_version of the project's database: None if it does not exist, 0 if empty, SCAN_STARTED if the scan
    was interrupted, SCAN_COMPLETE if it finished. Read-only: the database is never created nor modified.

    Opening a project whose scan was interrupted does not just open it: VfsProcessor.process() resumes the scan.
    """
    db_file = os.path.join(os.path.dirname(os.path.abspath(project_file)), 'db', 'core.db')
    if not os.path.isfile(db_file):
        return None
    uri = 'file:{}?mode=ro'.format(db_file.replace('\\', '/').replace('?', '%3f').replace('#', '%23'))
    try:
        conn = sqlite3.connect(uri, uri=True)
        try:
            return conn.execute('PRAGMA user_version').fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error:
        return None


class ProjectTask(QObject):
    """Worker object: create (scan) or open a project. Emits exactly one of ``succeeded`` or ``failed``."""
    message = Signal(str)                  # one DECA log line
    succeeded = Signal(object)             # VfsProcessor, now owned by the receiving (GUI) thread
    failed = Signal(str)                   # human-readable reason, traceback included when there is one
    done = Signal()                        # always emitted last

    def __init__(self, kind: str, path: str):
        super().__init__()
        if kind not in (KIND_NEW, KIND_OPEN):
            raise ValueError('unknown task kind: {}'.format(kind))
        self.kind = kind
        self.path = path
        self.started_at: Optional[float] = None

    def _on_log(self, level, msg):
        if level <= 2:
            self.message.emit(msg)

    @Slot()
    def run(self):
        self.started_at = time.time()
        Logger.listeners.append(self._on_log)
        try:
            if self.kind == KIND_NEW:
                vfs: Optional[VfsProcessor] = vfs_structure_new((self.path, ''))
                if vfs is None:
                    self.failed.emit(
                        'Unknown game: {}\n\nThe executable path must match the exe_match pattern of a game in '
                        'resources/deca/gameinfo (COTW: it must contain "theHunterCotW", case-sensitive).'.format(
                            self.path))
                    return
            else:
                vfs = vfs_structure_open(self.path)
            self.succeeded.emit(vfs)
        except Exception:
            self.failed.emit(traceback.format_exc())
        finally:
            Logger.listeners.remove(self._on_log)
            self.done.emit()


class CallTask(QObject):
    """
    Worker object: ``func(progress)`` in a background thread, for the COTW tools (catalogue, mod build).

    ``progress`` is a callable taking a short text, forwarded as ``message``. Emits exactly one of ``succeeded``
    (with the return value) or ``failed``. The COTW tools are command-line scripts that report refusals with
    SystemExit("ERREUR : ..."): it is caught like any exception and turned into ``failed`` with its message only,
    since it is an expected refusal and not a bug (no traceback).
    """
    message = Signal(str)
    succeeded = Signal(object)
    failed = Signal(str)
    done = Signal()

    def __init__(self, func, expected_errors=()):
        super().__init__()
        self.func = func
        self.expected_errors = tuple(expected_errors)   # exception types reported by their message only

    @Slot()
    def run(self):
        try:
            result = self.func(self.message.emit)
        except SystemExit as e:
            self.failed.emit(str(e.code if e.code is not None else 'interrupted'))
        except self.expected_errors as e:
            self.failed.emit(str(e))
        except Exception:
            self.failed.emit(traceback.format_exc())
        else:
            self.succeeded.emit(result)
        finally:
            self.done.emit()


def start_task(task: QObject, parent: QObject) -> QThread:
    """Run ``task`` in a new QThread owned by ``parent``; the thread and the task are deleted when it is done."""
    thread = QThread(parent)
    task.moveToThread(thread)
    thread.started.connect(task.run)
    task.done.connect(thread.quit)
    thread.finished.connect(task.deleteLater)
    thread.finished.connect(thread.deleteLater)
    thread.start()
    return thread
