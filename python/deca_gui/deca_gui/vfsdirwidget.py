from typing import Optional
from .vfs_widgets import used_color_calc, NodeCache
from deca.util import gc_paused
from deca.db_view import VfsView
from deca.db_processor import VfsNode
from deca.ff_types import *
from deca.dxgi_types import dxgi_name_db
from PySide6.QtCore import QAbstractItemModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QHeaderView, QSizePolicy, QWidget, QHBoxLayout, QTreeView, QAbstractItemView, QApplication


class VfsDirLeaf(object):
    # __slots__: one object per named file (hundreds of thousands on COTW), a per-instance dict tripled memory use
    __slots__ = ('name', 'parent', 'row', 'uids', 'uids_hard', 'uids_sym', 'in_gdcc_count')

    def __init__(self, name, uids_hard, uids_sym, in_gdcc_count):
        self.name = name
        self.parent = None
        self.row = 0
        self.uids = uids_hard + uids_sym
        self.uids_hard = uids_hard
        self.uids_sym = uids_sym
        self.in_gdcc_count = in_gdcc_count

    def v_path(self):
        pn = ''
        if self.parent is not None:
            pn = self.parent.v_path(True)
        return pn + self.name

    def child_count(self):
        return 0


class VfsDirBranch(object):
    __slots__ = ('name', 'parent', 'row', 'children', 'child_name_map')

    def __init__(self, name):
        self.name = name
        self.parent = None
        self.row = 0
        self.children = []
        self.child_name_map = {}

    def v_path(self, child_called=False):
        s = ''
        if self.parent is not None:
            s = self.parent.v_path(True)
            s = s + self.name + '/'
        return s

    def child_count(self):
        return len(self.children)

    def child_add(self, c):
        if c.name in self.child_name_map:
            return self.children[self.child_name_map[c.name]]
        else:
            c.parent = self
            c.row = len(self.children)
            self.child_name_map[c.name] = c.row
            self.children.append(c)
            return c


class VfsDirModel(QAbstractItemModel):
    vfs_changed_signal = Signal()

    def __init__(self):
        QAbstractItemModel.__init__(self)
        self.vfs_view: Optional[VfsView] = None
        self.gdc_body_uids = set()
        self.root_node = None
        self._branches = []  # every branch of the current tree, to break its cycles when it is replaced
        self._nodes = NodeCache()
        self.n_rows = 0
        self.n_cols = 0

        self.header_labels = \
            ("Path", "Index", "Type", "Sub_Type", "Hash", "EXT_Hash", "Size_U", "Size_C", "G, H, S", "Notes")

        self.vfs_changed_signal.connect(self.update_model)

    def vfs_view_get(self):
        return self.vfs_view

    def vfs_view_set(self, vfs_view: VfsView):
        if self.vfs_view != vfs_view:
            if self.vfs_view is not None:
                self.vfs_view.signal_visible_changed.disconnect(self)
                self.vfs_view = None

            self.vfs_view = vfs_view

            # find gdc.DECA / FTYPE_GDCBODY files, so they can tag their children
            gdc_uids = set(self.vfs_view.vfs().nodes_where_match(file_type=FTYPE_GDCBODY, uid_only=True))

            self.gdc_body_uids = set()
            for pid in gdc_uids:
                uids = self.vfs_view.vfs().nodes_where_match(pid_in=pid, uid_only=True)
                self.gdc_body_uids = self.gdc_body_uids.union(uids)

            self.vfs_view.signal_visible_changed.connect(self, lambda x: x.vfs_changed_signal.emit())
            self.vfs_changed_signal.emit()

    def _release_tree(self):
        """
        Break the parent <-> children cycles of the current tree.

        Without this, a replaced tree (hundreds of thousands of nodes on COTW) could only be freed by a cyclic
        garbage collection, which walked every object and froze the window for seconds after a filter change.
        Once the children lists are emptied, reference counting frees the whole tree immediately.
        Must be called between beginResetModel() and endResetModel(): views may hold indexes pointing to it.
        """
        for branch in self._branches:
            branch.children = []
            branch.child_name_map = {}
            branch.parent = None
        self._branches = []
        self.root_node = None

    def update_model(self):
        self.beginResetModel()
        self._nodes.clear()
        self._release_tree()

        with gc_paused():
            vpaths: dict = self.vfs_view.nodes_visible_map_get()
            self.root_node = VfsDirBranch('/')
            # directory path -> branch: each directory is created once, instead of building (and mostly discarding)
            # one VfsDirBranch per path component of every file
            branches = {(): self.root_node}

            def branch_for(parts):
                branch = branches.get(parts)
                if branch is None:
                    branch = branch_for(parts[:-1]).child_add(VfsDirBranch(parts[-1]))
                    branches[parts] = branch
                return branch

            gdc_body_uids = self.gdc_body_uids
            for v_path in sorted(vpaths):
                vp_uids, sym_uids = vpaths[v_path]

                if v_path.find('\\') >= 0:
                    print(f'GUI: Warning: Windows Path {v_path}')
                    path = v_path.split('\\')
                else:
                    path = v_path.split('/')

                cnode = branch_for(tuple(path[:-1]))

                in_gdcc_count = 0
                if gdc_body_uids:
                    for uid in vp_uids:
                        if uid in gdc_body_uids:
                            in_gdcc_count += 1
                cnode.child_add(VfsDirLeaf(path[-1], vp_uids, sym_uids, in_gdcc_count=in_gdcc_count))

            self._branches = list(branches.values())

        self.endResetModel()

    def parent(self, index):
        if not index.isValid():
            return QModelIndex()

        node = index.internalPointer()
        if node is None or node.parent is None:
            return QModelIndex()
        else:
            return self.createIndex(node.parent.row, 0, node.parent)

    def index(self, row, column, parent):
        if not parent.isValid():
            return self.createIndex(row, column, self.root_node)

        parent_node = parent.internalPointer()
        return self.createIndex(row, column, parent_node.children[row])

    def rowCount(self, parent):
        if not parent.isValid():
            return 1

        node = parent.internalPointer()
        if node is None:
            return 0

        return node.child_count()

    def columnCount(self, parent):
        return 10

    def headerData(self, section, orientation, role):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self.header_labels[section]
        else:
            return None

    def data(self, index, role):
        if not index.isValid():
            return None
        node = index.internalPointer()
        if node is None:
            return None
        column = index.column()
        if role == Qt.DisplayRole:
            if column == 0:
                return node.name
            elif isinstance(node, VfsDirLeaf):
                uid = node.uids[0]
                vnode: VfsNode = self._nodes.get(self.vfs_view, uid)
                if column == 1:
                    return '{}'.format(vnode.uid)
                elif column == 2:
                    return '{}'.format(vnode.file_type)
                elif column == 3:
                    if vnode.file_sub_type is None:
                        return ''
                    elif vnode.file_type in ftype_adf_family:
                        return '{:08x}'.format(vnode.file_sub_type)
                    else:
                        return '{} ({})'.format(dxgi_name_db.get(vnode.file_sub_type, 'UNKNOWN'), vnode.file_sub_type)
                elif column == 4:
                    return vnode.v_hash_to_str()
                elif column == 5:
                    if vnode.ext_hash is None:
                        return ''
                    else:
                        return '{:08x}'.format(vnode.ext_hash)
                elif column == 6:
                    return '{}'.format(vnode.size_u)
                elif column == 7:
                    return '{}'.format(vnode.size_c)
                elif column == 8:
                    return '{}, {}, {}'.format(node.in_gdcc_count, len(node.uids_hard), len(node.uids_sym))
                elif column == 9:
                    return '{}'.format(self.vfs_view.lookup_note_from_file_path(vnode.v_path))
        elif role == Qt.BackgroundRole:
            if isinstance(node, VfsDirLeaf):
                uid = node.uids[0]
                vnode: VfsNode = self._nodes.get(self.vfs_view, uid)
                if column == 8:
                    if node.in_gdcc_count > 0:
                        return QColor(Qt.yellow)
                elif column == 3:
                    if vnode.file_sub_type is not None and \
                            vnode.file_type in ftype_adf_family and \
                            vnode.file_sub_type not in self.vfs_view.adf_db().type_map_def:
                        return QColor(Qt.red)
        return None


class VfsDirWidget(QWidget):
    def __init__(self, vfs_view, *args, **kwargs):
        QWidget.__init__(self, *args, **kwargs)

        self.vnode_2click_selected = None

        # Getting the Model
        self.source_model = VfsDirModel()

        # Creating a QTableView
        self.view = QTreeView()
        self.view.doubleClicked.connect(self.double_clicked)
        self.view.clicked.connect(self.clicked)
        self.view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.view.setSelectionMode(QAbstractItemView.ExtendedSelection)

        font = self.view.font()
        font.setPointSize(8)
        self.view.setFont(font)
        self.view.setModel(self.source_model)
        self.view.setFont(QFont(QApplication.font().family(), QApplication.font().pointSize()))

        # # QTableView Headers
        self.header = self.view.header()
        self.header.setSectionResizeMode(QHeaderView.ResizeToContents)
        self.header.setStretchLastSection(True)

        size = QSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        size.setHorizontalStretch(1)
        self.view.setSizePolicy(size)

        self.main_layout = QHBoxLayout()
        self.main_layout.addWidget(self.view)
        self.setLayout(self.main_layout)

        self.source_model.vfs_view_set(vfs_view)

    def vfs_view_get(self):
        return self.source_model.vfs_view_get()

    def clicked(self, index):
        if index.isValid():
            if self.source_model.vfs_view is not None:
                items = self.view.selectedIndexes()
                items = list(set([idx.internalPointer() for idx in items]))
                items = [idx.v_path() for idx in items if isinstance(idx, VfsDirLeaf) or isinstance(idx, VfsDirBranch)]
                self.source_model.vfs_view_get().capture_set(items)

    def double_clicked(self, index):
        if index.isValid():
            tnode = index.internalPointer()
            if isinstance(tnode, VfsDirLeaf) and self.vnode_2click_selected is not None:
                self.vnode_2click_selected(tnode.uids_hard)
