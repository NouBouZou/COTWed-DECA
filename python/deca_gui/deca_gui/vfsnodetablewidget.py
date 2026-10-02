from typing import Optional
from .vfs_widgets import used_color_calc, NodeCache
from deca.db_processor import VfsNode
from deca.db_view import VfsView
from deca.ff_types import *
import PySide6
from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QHeaderView, QSizePolicy, QTableView, QWidget, QHBoxLayout, QAbstractItemView, QApplication

# SQL expression used to sort each column (fixed list: never built from user input). v_path is stored as TEXT or
# BLOB depending on who wrote it; the cast makes both sort together.
SORT_SQL = (
    'node_id',
    'parent_id',
    'file_type',
    'file_sub_type',
    'v_hash',
    'ext_hash',
    'size_u',
    'size_c',
    'COALESCE(CAST(v_path AS TEXT), p_path)',
)


class VfsNodeTableModel(QAbstractTableModel):
    vfs_changed_signal = Signal()

    def __init__(self, *args, **kwargs):
        QAbstractTableModel.__init__(self, *args, **kwargs)
        self.vfs_view: Optional[VfsView] = None
        self.show_all = True
        self.uid_table = None
        self._uids_source = None  # set the table was built from: skip the rebuild when it did not change
        self._sort_column = 0
        self._sort_order = Qt.AscendingOrder
        self._nodes = NodeCache()
        self.column_ids = ["Index", "PIDX", "Type", "Sub Type", "Hash", "EXT_hash", "Size_U", "Size_C", "Path"]

        self.vfs_changed_signal.connect(self.update_model)

    def vfs_view_get(self):
        return self.vfs_view

    def vfs_view_set(self, vfs_view: VfsView):
        if self.vfs_view != vfs_view:
            if self.vfs_view is not None:
                self.vfs_view.signal_visible_changed.disconnect(self)
                self.vfs_view = None

            self.vfs_view = vfs_view
            self.vfs_view.signal_visible_changed.connect(self, lambda x: x.vfs_changed_signal.emit())
            self.vfs_changed_signal.emit()

    def _uids_get(self):
        if self.show_all:
            return self.vfs_view.nodes_visible_uids_get()
        return self.vfs_view.nodes_visible_uids_no_vpath_get()

    def _ordered(self, uids):
        if self._sort_column == 0 and self._sort_order == Qt.AscendingOrder:
            return sorted(uids)
        return self.vfs_view.nodes_visible_uids_sorted(
            SORT_SQL[self._sort_column], descending=self._sort_order == Qt.DescendingOrder,
            no_vpath_only=not self.show_all)

    def update_model(self, force=False):
        uids = self._uids_get()

        # These lists ignore the filter: a filter change keeps the same set, and re-sorting up to a million uids
        # on every change froze the window for nothing.
        if uids is self._uids_source and not force:
            return

        self.beginResetModel()
        self._uids_source = uids
        self._nodes.clear()
        self.uid_table = self._ordered(uids)
        self.endResetModel()

    def sort(self, column: int, order: PySide6.QtCore.Qt.SortOrder):
        if not 0 <= column < len(SORT_SQL):
            return
        if (column, order) == (self._sort_column, self._sort_order) and self.uid_table is not None:
            return
        self._sort_column, self._sort_order = column, order
        if self.vfs_view is not None:
            self.layoutAboutToBeChanged.emit()
            self.uid_table = self._ordered(self._uids_get())
            self._nodes.clear()
            self.layoutChanged.emit()

    def rowCount(self, parent=QModelIndex()):
        if self.uid_table is None:
            return 0
        else:
            return len(self.uid_table)

    def columnCount(self, parent=QModelIndex()):
        return 9

    def headerData(self, section, orientation, role):
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal:
            return self.column_ids[section]
        else:
            return None

    def data(self, index, role=Qt.DisplayRole):
        column = index.column()
        row = index.row()

        if role == Qt.DisplayRole:
            uid = self.uid_table[row]
            node: VfsNode = self._nodes.get(self.vfs_view, uid)
            if node is None:
                return 'NA'
            else:
                if column == 0:
                    return '{}'.format(node.uid)
                elif column == 1:
                    return '{}'.format(node.pid)
                elif column == 2:
                    return '{}'.format(node.file_type)
                elif column == 3:
                    if node.file_sub_type is None:
                        return ''
                    elif node.file_type in ftype_adf_family:
                        return '{:08x}'.format(node.file_sub_type)
                    else:
                        return '{}'.format(node.file_sub_type)
                elif column == 4:
                    return node.v_hash_to_str()
                elif column == 5:
                    if node.ext_hash is None:
                        return ''
                    else:
                        return '{:08x}'.format(node.ext_hash)
                elif column == 6:
                    return '{}'.format(node.size_u)
                elif column == 7:
                    return '{}'.format(node.size_c)
                elif column == 8:
                    if node.v_path is not None:
                        return 'V: {}'.format(node.v_path.decode('utf-8'))
                    elif node.p_path is not None:
                        return 'P: {}'.format(node.p_path)
                    else:
                        return ''

        elif role == Qt.BackgroundRole:
            uid = self.uid_table[row]
            node: VfsNode = self._nodes.get(self.vfs_view, uid)
            if node is not None and node.is_valid():
                if column == 8:
                    if node.used_at_runtime_depth is not None:
                        return used_color_calc(node.used_at_runtime_depth)
                elif column == 3:
                    # ADF whose type definition is missing (same colouring as the directory tree)
                    if node.file_sub_type is not None and \
                            node.file_type in ftype_adf_family and \
                            node.file_sub_type not in self.vfs_view.adf_db().type_map_def:
                        return QColor(Qt.red)

        elif role == Qt.TextAlignmentRole:
            if column == 8:
                return Qt.AlignLeft
            else:
                return Qt.AlignRight

        return None


class VfsNodeTableWidget(QWidget):
    def __init__(self, vfs_view, *args, **kwargs):
        QWidget.__init__(self, *args, **kwargs)

        self.vnode_2click_selected = None

        # Getting the Model
        self.model = VfsNodeTableModel()

        # Creating a QTableView
        self.table_view = QTableView()
        self.table_view.clicked.connect(self.clicked)
        self.table_view.doubleClicked.connect(self.double_clicked)
        self.table_view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table_view.setFont(QFont(QApplication.font().family(), QApplication.font().pointSize()))
        self.table_view.setModel(self.model)
        # sorting is done by the model in SQL (see VfsNodeTableModel.sort); column 0 ascending is the natural order
        self.table_view.horizontalHeader().setSortIndicator(0, Qt.AscendingOrder)
        self.table_view.setSortingEnabled(True)

        # QTableView Headers
        self.horizontal_header = self.table_view.horizontalHeader()
        self.vertical_header = self.table_view.verticalHeader()
        self.horizontal_header.setSectionResizeMode(QHeaderView.Interactive)
        self.vertical_header.setSectionResizeMode(QHeaderView.Interactive)
        self.horizontal_header.setStretchLastSection(True)
        # To calculate default cell height we should use painters font-metrics instead of constructing a QFontMetrics(font)
        # N * [line-height] + 2 * [1 pixel space around]
        self.vertical_header.setMinimumSectionSize(    self.table_view.fontMetrics().lineSpacing() + 2)
        self.vertical_header.setDefaultSectionSize(2 * self.table_view.fontMetrics().lineSpacing() + 2)
        self.vertical_header.setVisible(False)

        # QWidget Layout
        self.main_layout = QHBoxLayout()
        size = QSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)

        # Left layout
        size.setHorizontalStretch(1)
        self.table_view.setSizePolicy(size)
        self.main_layout.addWidget(self.table_view)

        # Set the layout to the QWidget
        self.setLayout(self.main_layout)

        self.model.vfs_view_set(vfs_view)

    def show_all_set(self, v):
        self.model.show_all = v
        self.model.update_model(force=True)

    def vfs_view_get(self):
        return self.model.vfs_view_get()

    def clicked(self, index):
        if index.isValid():
            if self.model.vfs_view is not None:
                #see: https://bugreports.qt.io/browse/QTBUG-59478
                items = list()
                selection = self.table_view.selectionModel().selection()
                for selectionRange in selection:
                    items += self.model.uid_table[selectionRange.top(): selectionRange.top() + selectionRange.height()]
                items = self.model.vfs_view_get().nodes_where_uid(items)
                items = [item for item in items if item.file_type != FTYPE_SYMLINK and item.v_path is not None and item.p_path is None]
                self.model.vfs_view_get().capture_set(items)

    def double_clicked(self, index):
        if index.isValid():
            if self.vnode_2click_selected is not None:
                uids = [self.model.uid_table[index.row()]]
                self.vnode_2click_selected(uids)
