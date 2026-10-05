"""
XLSBook viewer (equipment_stats_ui.bin and the other settings sheets): one grid per sheet, as DECA's xlsx export
would show it. Rows from the first empty row on are greyed: the game stops reading the stats of the ammo sheet there
(test12 H, research log 2026-10-03); the store logo search, by contrast, goes beyond it.
"""
from typing import List, Optional

from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QTableWidget, QTableWidgetItem, QVBoxLayout

from . import cotw_tools
from .viewer import DataViewer

AFTER_EMPTY = QColor('#9a9a9a')
AFTER_EMPTY_BG = QColor('#efefef')


class XlsBook:
    """Sheets of an XLSBook instance (values read by DECA), without Qt."""

    def __init__(self, values):
        cotw_tools.ensure_path()
        import xlsbook as X
        X.require_book(values)
        self.values = values
        self.names: List[str] = [bytes(s['Name']).decode('utf-8', 'replace') for s in values['Sheet']]
        self._x = X

    def rows(self, sheet: int):
        return self._x.sheet_rows(self.values, sheet)

    def first_empty_row(self, sheet: int) -> int:
        return self._x.first_empty_row(self.values, sheet)


EMPTY_TIP = 'Empty cell: a boolean cell (BoolData, True), which DECA\'s xlsx export shows as "True".'


def text_of(v) -> str:
    """Text of a cell. Booleans are the empty cells of the game's sheets (xlsbook.py): shown blank."""
    if isinstance(v, bool):
        return ''
    if isinstance(v, float):
        return '{:g}'.format(v)
    return '' if v is None else str(v)


class DataViewerXlsBook(DataViewer):
    def __init__(self):
        super().__init__()
        self.book: Optional[XlsBook] = None
        self.sheet_combo = QComboBox()
        self.sheet_combo.currentIndexChanged.connect(self.show_sheet)
        self.table = QTableWidget()
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.note = QLabel()
        self.note.setWordWrap(True)
        bar = QHBoxLayout()
        bar.addWidget(QLabel('Sheet:'))
        bar.addWidget(self.sheet_combo, 1)
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(bar)
        layout.addWidget(self.table)
        layout.addWidget(self.note)
        self.setLayout(layout)

    def vnode_process(self, vfs, vnode):
        from deca.ff_adf import AdfDatabase
        adf = AdfDatabase(vfs).read_node(vfs, vnode)
        self.load(adf.table_instance_values[0])

    def load(self, values):
        self.book = XlsBook(values)
        self.sheet_combo.blockSignals(True)
        self.sheet_combo.clear()
        for i, name in enumerate(self.book.names):
            rows = self.book.rows(i)
            self.sheet_combo.addItem('{}  ({} rows x {} columns)'.format(name, max(len(rows) - 1, 0),
                                                                        len(rows[0]) if rows else 0), i)
        self.sheet_combo.blockSignals(False)
        self.show_sheet(0)

    def show_sheet(self, index: int):
        if self.book is None or not 0 <= index < len(self.book.names):
            return
        rows = self.book.rows(index)
        empty = self.book.first_empty_row(index)
        header = rows[0] if rows else []
        self.table.clear()
        self.table.setColumnCount(len(header))
        self.table.setRowCount(max(len(rows) - 1, 0))
        self.table.setHorizontalHeaderLabels([text_of(h) or '#{}'.format(c) for c, h in enumerate(header)])
        self.table.setVerticalHeaderLabels([str(r) for r in range(1, len(rows))])
        for r in range(1, len(rows)):
            for c, v in enumerate(rows[r]):
                item = QTableWidgetItem(text_of(v))
                if isinstance(v, bool):
                    item.setToolTip(EMPTY_TIP)
                if r >= empty:
                    item.setForeground(QBrush(AFTER_EMPTY))
                    item.setBackground(QBrush(AFTER_EMPTY_BG))
                    item.setToolTip('After the first empty row ({}): not read for the stats (test12 H).'.format(empty))
                self.table.setItem(r - 1, c, item)
        self.table.resizeColumnsToContents()
        n = len(rows) - 1
        if empty < len(rows):
            self.note.setText('{} rows; the first empty row is row {}: the {} rows from there on are greyed (the game '
                              'stops reading the ammo stats at the first empty row, test12 H).'.format(n, empty,
                                                                                                       len(rows) - empty))
        else:
            self.note.setText('{} rows, no empty row.'.format(n))
