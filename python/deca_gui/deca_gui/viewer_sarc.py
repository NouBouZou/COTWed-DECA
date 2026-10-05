"""
SARC viewer: the entries of the archive (path, offset, size, type found by DECA's magic rules, links), read with
cotw/sarc.py. Double-click an entry to show it (its node in the project); "Open Archive" opens the archive as a tab.
"""
import io

from .viewer import *
from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout

from . import cotw_tools
from .reading import node_bytes

COLUMNS = ['#', 'Path', 'Offset', 'Size', 'Type', 'Link']


def entry_rows(data: bytes):
    """[(index, path, offset, size, type, is_link)] of a SARC, and its sub-version."""
    cotw_tools.ensure_path()
    import sarc as S
    from deca.ff_determine import determine_file_type_and_size
    sarc = S.parse(data)
    rows = []
    for i, e in enumerate(sarc.entries):
        if e.is_link:
            kind = ''
        else:
            try:
                kind = determine_file_type_and_size(io.BytesIO(e.data), len(e.data))[0] or '(unknown)'
            except Exception:  # noqa: BLE001 - type detection is informative only
                kind = '(unknown)'
        rows.append((i, e.path, None if e.is_link else e.offset, e.length, kind, e.is_link))
    return rows, sarc.version


class DataViewerSarc(DataViewer):
    signal_archive_open = Signal(VfsNode)

    def __init__(self):
        DataViewer.__init__(self)
        self.vnode = None
        self.vfs = None
        self.open_entry = None   # set by DataViewWidget: callable(uid) that shows a node

        self.bttn_open_archive = QPushButton()
        self.bttn_open_archive.setObjectName('bttn_open_archive')
        self.bttn_open_archive.setText('Open Archive')
        self.bttn_open_archive.clicked.connect(self.open_archive)
        self.summary = QLabel()
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.cellDoubleClicked.connect(self._double_clicked)

        self.cmd_layout = QHBoxLayout()
        self.cmd_layout.addWidget(self.bttn_open_archive)
        self.cmd_layout.addWidget(self.summary, 1)
        self.main_layout = QVBoxLayout()
        self.main_layout.addLayout(self.cmd_layout)
        self.main_layout.addWidget(self.table)
        self.setLayout(self.main_layout)

    def vnode_process(self, vfs: VfsProcessor, vnode: VfsNode):
        self.vnode = vnode
        self.vfs = vfs
        try:
            rows, version = entry_rows(node_bytes(vfs, vnode))
        except Exception as e:  # noqa: BLE001 - unreadable archive: say why
            self.table.setRowCount(0)
            self.summary.setText('SARC not readable: {}: {}'.format(type(e).__name__, e))
            return
        self.table.setRowCount(len(rows))
        for r, (i, path, offset, size, kind, link) in enumerate(rows):
            for c, v in enumerate((i, path, '' if offset is None else '0x{:x}'.format(offset), size, kind,
                                   'link' if link else '')):
                item = QTableWidgetItem(str(v))
                if c in (0, 3):
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(r, c, item)
        self.table.resizeColumnsToContents()
        n_links = sum(1 for row in rows if row[5])
        self.summary.setText('SARC v{}: {} entries, {} links. Double-click an entry to show it.'.format(
            version, len(rows), n_links))

    def entry_uid(self, row: int):
        """Node of the entry in the project (child of the archive with that path), or None."""
        if self.vfs is None or self.vnode is None:
            return None
        path = self.table.item(row, 1).text()
        for child in self.vfs.nodes_where_match(pid_in=self.vnode.uid):
            v_path = child.v_path.decode('utf-8', 'replace') if isinstance(child.v_path, bytes) else child.v_path
            if v_path == path:
                return child.uid
        return None

    def _double_clicked(self, row, _column):
        uid = self.entry_uid(row)
        if uid is not None and self.open_entry is not None:
            self.open_entry(uid)

    def open_archive(self):
        if self.vnode is not None:
            self.signal_archive_open.emit(self.vnode)
