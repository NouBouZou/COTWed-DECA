"""
StringLookup viewer (string_ids.stringlookup, ui_localization.stringlookup...): one row per key, its hash32 and
its text in every language, with a filter. Read directly from the bytes with cotw/stringlookup.py (Vue): the game's
38.8 MB string_ids opens in about a second, where DECA's generic ADF reading takes 17 s and its text dump is
unusable (24.7 MB text block printed element by element).

Format (research log, 2026-10-03): StringLookup { Languages: A[LanguageStringLookup], Text: A[int8] };
per language, SortedPairs (Hash = hash32 of the key, TextOffset) sorted by hash, and SortedDialogueLines in the same
order whose NameOffset points at the key itself.
"""
from typing import Dict, List, Optional

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt
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
        self.key_offsets: Dict[int, int] = {}
        if n:
            lines = self.vue.lines(0)
            if len(lines) == len(self.offsets[0]):   # dialogue lines parallel to the pairs: they carry the key
                self.key_offsets = {h: name_off for h, name_off, _s_off, _s_n in lines}
        self.noms = noms
        self._search: Optional[List[str]] = None

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
    def __init__(self, table: StringTable, parent=None):
        super().__init__(parent)
        self.table = table
        self.rows = list(range(len(table.hashes)))

    def set_filter(self, text: str):
        self.beginResetModel()
        self.rows = self.table.matching(text)
        self.endResetModel()

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
        self.proxy = QSortFilterProxyModel(self)
        self.view = QTableView()
        self.view.setModel(self.proxy)
        self.view.setSortingEnabled(True)
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
        self.proxy.setSourceModel(self.model)
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
        row = self.proxy.mapToSource(current).row()
        h = self.model.hash_at(row)
        lines = ['{}   (hash32 {:08x})'.format(self.table.key(h), h), '']
        for i, code in enumerate(self.table.languages):
            t = self.table.text(h, i)
            lines.append('{:<3} {}'.format(code, '(missing)' if t is None else t))
        self.detail.setPlainText('\n'.join(lines))
