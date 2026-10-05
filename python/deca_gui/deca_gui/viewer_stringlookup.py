"""
StringLookup viewer (string_ids.stringlookup, ui_localization.stringlookup...): one row per key, its hash32 and
its text in every language, with a filter. Read directly from the bytes with cotw/stringlookup.py (Vue): the game's
38.8 MB string_ids is read from its bytes, without DECA's generic ADF reading (17 s on that file; its text dump
prints the 24.7 MB text block element by element).

Format (research log, 2026-10-03): StringLookup { Languages: A[LanguageStringLookup], Text: A[int8] };
per language, SortedPairs (Hash = hash32 of the key, TextOffset) sorted by hash, and SortedDialogueLines in the same
order whose NameOffset points at the key itself.
"""
from typing import Dict, List, Optional

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPlainTextEdit, QSplitter, QTableView,
    QVBoxLayout)

from . import cotw_tools
from .reading import node_bytes, noms_for
from .viewer import DataViewer

LANGUAGE_NAMES = {'en': 'English', 'fr': 'French', 'de': 'German', 'es': 'Spanish', 'ru': 'Russian',
                  'pl': 'Polish', 'ja': 'Japanese', 'pt': 'Portuguese', 'cs': 'Czech', 'zh': 'Chinese'}


class StringTable:
    """Keys and texts of a StringLookup, without Qt (tested directly)."""

    def __init__(self, data: bytes, noms=None):
        cotw_tools.ensure_path()
        import stringlookup as SL
        self.vue = SL.Vue(data)
        n = self.vue.n_lang
        self.languages = [SL.LANGUES[i] if i < len(SL.LANGUES) else 'lang{}'.format(i) for i in range(n)]
        self.offsets: List[Dict[int, int]] = [dict(self.vue.pairs(i)) for i in range(n)]
        hashes = set()
        for d in self.offsets:
            hashes.update(d)
        self.hashes = sorted(hashes)
        # Each dialogue line carries its hash and the offset of its key in clear: hash -> key offset, language 0
        # first, the other languages for hashes it lacks. Not tied to the lines being parallel to the pairs (the
        # game's are), so a table where they are not still shows the keys it has.
        self.key_offsets: Dict[int, int] = {}
        for i in range(n):
            missing = len(self.hashes) - len(self.key_offsets)
            if not missing:
                break
            for h, name_off, _s_off, _s_n in self.vue.lines(i):
                self.key_offsets.setdefault(h, name_off)
        self.noms = noms
        self._search: Optional[List[str]] = None
        self._keys: Optional[Dict[int, str]] = None

    def keys(self) -> Dict[int, str]:
        """hash -> key, decoded once (sorting by key reads every key)."""
        if self._keys is None:
            self._keys = {h: self.key(h) for h in self.hashes}
        return self._keys

    def key(self, h: int) -> str:
        off = self.key_offsets.get(h)
        if off is not None:
            return self.vue.text(off)
        name = self.noms.get(h) if self.noms is not None else None
        return name or ''

    def text(self, h: int, lang: int) -> Optional[str]:
        off = self.offsets[lang].get(h)
        return None if off is None else self.vue.text(off)

    def matching(self, text: str) -> List[int]:
        """Row numbers whose key, hash or one of the texts contains ``text`` (case-insensitive)."""
        text = text.strip().lower()
        if not text:
            return list(range(len(self.hashes)))
        if self._search is None:   # built once: key, hash and every language, lowercase
            self._search = ['\0'.join([self.key(h), '{:08x}'.format(h)] +
                                      [self.text(h, i) or '' for i in range(len(self.languages))]).lower()
                            for h in self.hashes]
        return [r for r, s in enumerate(self._search) if text in s]


class StringTableModel(QAbstractTableModel):
    """Rows = indices into table.hashes, filtered and sorted here.

    Why not a QSortFilterProxyModel: it sorts by calling data() for every comparison, through Python; on the game's
    string_ids (22 079 keys) that is 350 000 calls and seconds of freeze when the tab opens. One list.sort on
    precomputed keys takes a few milliseconds."""

    def __init__(self, table: StringTable, parent=None):
        super().__init__(parent)
        self.table = table
        self.rows = list(range(len(table.hashes)))
        self._sort = None   # (column, order) of the last sort, re-applied after filtering

    def set_filter(self, text: str):
        self.beginResetModel()
        self.rows = self.table.matching(text)
        self._apply_sort()
        self.endResetModel()

    def sort(self, column, order=Qt.AscendingOrder):
        self._sort = (column, order)
        self.layoutAboutToBeChanged.emit()
        self._apply_sort()
        self.layoutChanged.emit()

    def _apply_sort(self):
        if self._sort is None:
            return
        column, order = self._sort
        hashes = self.table.hashes
        if column == 0:
            keys = self.table.keys()
            sort_key = lambda r: keys[hashes[r]].lower()  # noqa: E731
        elif column == 1:
            sort_key = lambda r: hashes[r]  # noqa: E731
        else:
            lang = column - 2
            sort_key = lambda r: (self.table.text(hashes[r], lang) or '').lower()  # noqa: E731
        self.rows.sort(key=sort_key, reverse=order == Qt.DescendingOrder)

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 2 + len(self.table.languages)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Vertical:
            return None
        if section == 0:
            return 'Key'
        if section == 1:
            return 'Hash'
        code = self.table.languages[section - 2]
        return '{} ({})'.format(LANGUAGE_NAMES.get(code, code), code)

    def hash_at(self, row: int) -> int:
        return self.table.hashes[self.rows[row]]

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or role not in (Qt.DisplayRole, Qt.ToolTipRole):
            return None
        h = self.hash_at(index.row())
        c = index.column()
        if c == 0:
            return self.table.key(h)
        if c == 1:
            return '{:08x}'.format(h)
        t = self.table.text(h, c - 2)
        return '(missing)' if t is None else t


class DataViewerStringLookup(DataViewer):
    def __init__(self):
        super().__init__()
        self.table: Optional[StringTable] = None
        self.model: Optional[StringTableModel] = None
        self.view = QTableView()
        self.view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.view.setSelectionMode(QAbstractItemView.SingleSelection)
        self.view.verticalHeader().setVisible(False)
        self.view.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText('Filter: key, hash or text in any language (Enter)')
        self.filter_edit.returnPressed.connect(self.apply_filter)
        self.language_combo = QComboBox()
        self.language_combo.currentIndexChanged.connect(self._columns)
        self.status = QLabel()
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        bar = QHBoxLayout()
        bar.addWidget(self.filter_edit, 1)
        bar.addWidget(QLabel('Show:'))
        bar.addWidget(self.language_combo)
        split = QSplitter()
        split.setOrientation(Qt.Vertical)
        split.addWidget(self.view)
        split.addWidget(self.detail)
        split.setSizes([500, 160])
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(bar)
        layout.addWidget(split)
        layout.addWidget(self.status)
        self.setLayout(layout)

    def vnode_process(self, vfs, vnode):
        self.load(node_bytes(vfs, vnode), noms_for(vfs))

    def load(self, data: bytes, noms=None):
        self.table = StringTable(data, noms)
        self.model = StringTableModel(self.table, self)
        self.view.setSortingEnabled(False)
        self.view.setModel(self.model)
        self.view.setSortingEnabled(True)
        self.view.sortByColumn(0, Qt.AscendingOrder)   # by key, A to Z (Qt 6's default indicator is descending)
        self.view.selectionModel().currentRowChanged.connect(self._show_detail)
        self.language_combo.blockSignals(True)
        self.language_combo.clear()
        self.language_combo.addItem('all languages', None)
        for i, code in enumerate(self.table.languages):
            self.language_combo.addItem('{} ({})'.format(LANGUAGE_NAMES.get(code, code), code), i)
        self.language_combo.blockSignals(False)
        self._columns()
        self.status.setText('{} keys, {} languages ({} keys named by their dialogue line).'.format(
            len(self.table.hashes), len(self.table.languages), len(self.table.key_offsets)))
        self.detail.setPlainText('')

    def apply_filter(self):
        if self.model is not None:
            self.model.set_filter(self.filter_edit.text())
            self.status.setText('{} of {} keys.'.format(len(self.model.rows), len(self.table.hashes)))

    def _columns(self, *_):
        if self.model is None:
            return
        lang = self.language_combo.currentData()
        for i in range(len(self.table.languages)):
            self.view.setColumnHidden(2 + i, lang is not None and i != lang)
        self.view.setColumnWidth(0, 320)
        self.view.setColumnWidth(1, 80)

    def _show_detail(self, current, _previous=None):
        if not current.isValid() or self.model is None:
            return
        h = self.model.hash_at(current.row())
        lines = ['{}   (hash32 {:08x})'.format(self.table.key(h), h), '']
        for i, code in enumerate(self.table.languages):
            t = self.table.text(h, i)
            lines.append('{:<3} {}'.format(code, '(missing)' if t is None else t))
        self.detail.setPlainText('\n'.join(lines))
