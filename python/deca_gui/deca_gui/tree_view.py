"""
Qt side of the lazy trees (tree_items.py): a model that loads children when the view expands an item, and a viewer
widget with find, expand, copy and text export. DECA's original text dump stays available ("DECA text dump").
"""
from typing import Callable, List, Optional

from PySide6.QtCore import QAbstractItemModel, QModelIndex, Qt
from PySide6.QtGui import QFont, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPlainTextEdit,
    QPushButton, QStackedWidget, QTreeView, QVBoxLayout, QWidget)

from .tree_items import COLUMNS, TreeItem

FIND_LIMIT = 500000      # items visited by one search (loads children on the way)
EXPAND_LIMIT = 5000      # items expanded by "Expand all"
EXPORT_LIMIT = 500000    # lines written by "Export text"


class LazyTreeModel(QAbstractItemModel):
    def __init__(self, root: Optional[TreeItem] = None, parent=None):
        super().__init__(parent)
        self.root = root or TreeItem(['root'])

    def set_root(self, root: TreeItem):
        self.beginResetModel()
        self.root = root
        self.endResetModel()

    def item(self, index: QModelIndex) -> TreeItem:
        return index.internalPointer() if index.isValid() else self.root

    def index(self, row, column, parent=QModelIndex()):
        children = self.item(parent).children()
        if 0 <= row < len(children):
            return self.createIndex(row, column, children[row])
        return QModelIndex()

    def index_of(self, item: TreeItem, column: int = 0) -> QModelIndex:
        if item is None or item is self.root:
            return QModelIndex()
        return self.createIndex(item.row, column, item)

    def parent(self, index):
        if not index.isValid():
            return QModelIndex()
        p = index.internalPointer().parent
        return self.index_of(p)

    def rowCount(self, parent=QModelIndex()):
        if parent.column() > 0:
            return 0
        return len(self.item(parent).children())

    def hasChildren(self, parent=QModelIndex()):
        return self.item(parent).has_children()

    def columnCount(self, parent=QModelIndex()):
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return COLUMNS[section]
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        it = index.internalPointer()
        if role == Qt.DisplayRole:
            return it.cols[index.column()]
        if role == Qt.ToolTipRole:
            return it.tooltip or it.cols[index.column()] or None
        return None


class TreeViewer(QWidget):
    """Tree with a find bar; ``raw_dump`` (optional) gives DECA's text dump, computed only when asked for."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.model = LazyTreeModel(parent=self)
        self.view = QTreeView()
        self.view.setModel(self.model)
        self.view.setUniformRowHeights(True)
        self.view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.view.header().setSectionResizeMode(QHeaderView.Interactive)
        self.view.header().setStretchLastSection(True)
        self.view.setAlternatingRowColors(True)
        font = QFont('Courier', 9)
        font.setStyleHint(QFont.Monospace)
        self.view.setFont(font)
        self.raw_text = QPlainTextEdit()
        self.raw_text.setReadOnly(True)
        self.raw_text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.raw_text.setFont(font)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.view)
        self.stack.addWidget(self.raw_text)

        self.find_edit = QLineEdit()
        self.find_edit.setPlaceholderText('Find (name, value, hash...): Enter = next')
        self.find_edit.returnPressed.connect(self.find_next)
        self.find_button = QPushButton('Next')
        self.find_button.clicked.connect(self.find_next)
        self.expand_button = QPushButton('Expand all')
        self.expand_button.setToolTip('Expands up to {} items'.format(EXPAND_LIMIT))
        self.expand_button.clicked.connect(self.expand_all)
        self.export_button = QPushButton('Export text...')
        self.export_button.clicked.connect(self.export_text)
        self.raw_check = QCheckBox('DECA text dump')
        self.raw_check.setToolTip('The original DECA view (flat text), computed when shown')
        self.raw_check.toggled.connect(self._toggle_raw)
        self.status = QLabel()
        bar = QHBoxLayout()
        bar.addWidget(self.find_edit, 1)
        bar.addWidget(self.find_button)
        bar.addWidget(self.expand_button)
        bar.addWidget(self.export_button)
        bar.addWidget(self.raw_check)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(bar)
        layout.addWidget(self.stack)
        layout.addWidget(self.status)
        QShortcut(QKeySequence.Copy, self.view, activated=self.copy_selection)
        self._raw_dump: Optional[Callable[[], str]] = None
        self._raw_done = False

    def set_tree(self, root: TreeItem, raw_dump: Optional[Callable[[], str]] = None, expand_depth: int = 2):
        self.model.set_root(root)
        self._raw_dump = raw_dump
        self._raw_done = False
        self.raw_check.setEnabled(raw_dump is not None)
        if self.raw_check.isChecked():
            self._toggle_raw(True)
        self.view.expandToDepth(expand_depth - 1)
        for c in range(len(COLUMNS) - 1):
            self.view.resizeColumnToContents(c)
            self.view.setColumnWidth(c, min(self.view.columnWidth(c), 420))
        self.status.setText('')

    def _toggle_raw(self, on: bool):
        if on and not self._raw_done and self._raw_dump is not None:
            self.raw_text.setPlainText(self._raw_dump())
            self._raw_done = True
        self.stack.setCurrentIndex(1 if on else 0)

    def _ensure_visible(self, item: TreeItem):
        chain = []
        p = item.parent
        while p is not None and p is not self.model.root:
            chain.append(p)
            p = p.parent
        for p in reversed(chain):
            self.view.expand(self.model.index_of(p))
        index = self.model.index_of(item)
        self.view.setCurrentIndex(index)
        self.view.scrollTo(index, QAbstractItemView.PositionAtCenter)

    def find_next(self) -> Optional[TreeItem]:
        """Next item (depth-first, after the current one) whose columns contain the text, case-insensitive."""
        text = self.find_edit.text().strip().lower()
        if not text:
            return None
        current = self.view.currentIndex()
        start = current.internalPointer() if current.isValid() else None
        passed = start is None
        first = None
        for n, it in enumerate(self.model.root.walk(FIND_LIMIT)):
            if it is self.model.root:
                continue
            hit = any(text in c.lower() for c in it.cols if c)
            if hit and first is None:
                first = it
            if it is start:
                passed = True
                continue
            if passed and hit:
                self._ensure_visible(it)
                self.status.setText('')
                return it
        if first is not None:   # wrap around
            self._ensure_visible(first)
            self.status.setText('Wrapped to the first match.')
            return first
        self.status.setText('Not found in the first {} items.'.format(FIND_LIMIT))
        return None

    def expand_all(self):
        n = 0
        for it in self.model.root.walk(EXPAND_LIMIT):
            if it is not self.model.root and it.has_children():
                self.view.expand(self.model.index_of(it))
            n += 1
        self.status.setText('Expanded {} items{}.'.format(n, ' (limit reached)' if n >= EXPAND_LIMIT else ''))

    def copy_selection(self):
        rows = self.view.selectionModel().selectedRows() if self.view.selectionModel() else []
        lines = ['\t'.join(r.internalPointer().cols) for r in rows]
        if lines:
            QGuiApplication.clipboard().setText('\n'.join(lines))

    def export_text(self, path: Optional[str] = None) -> Optional[str]:
        if not isinstance(path, str):
            path, _ = QFileDialog.getSaveFileName(self, 'Export text', '', 'Text (*.txt)')
            if not path:
                return None
        lines: List[str] = []
        for child in self.model.root.children():
            lines += child.text_lines(limit=EXPORT_LIMIT - len(lines))
        with open(path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n')
        self.status.setText('Exported {} lines to {}'.format(len(lines), path))
        return path
