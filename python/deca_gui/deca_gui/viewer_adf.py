"""ADF viewer: lazy tree read by DECA, large arrays split into ranges, StringHash and u32 hashes named."""
from .viewer import *
from deca.ff_adf import EDecaMissingAdfType, AdfDatabase
from PySide6.QtWidgets import QVBoxLayout

from .reading import RAW_DUMP_LIMIT, noms_for
from .tree_items import TreeItem, adf_tree, root
from .tree_view import TreeViewer


class DataViewerAdf(DataViewer):
    def __init__(self):
        super().__init__()
        self.tree = TreeViewer()
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.tree)
        self.setLayout(layout)

    def vnode_process(self, vfs: VfsProcessor, vnode: VfsNode):
        label = to_text(vnode.v_path) or '{:08x}'.format(vnode.v_hash or 0)
        try:
            adf = AdfDatabase(vfs).read_node(vfs, vnode)
        except EDecaMissingAdfType as e:
            message = 'Missing ADF type {:08x} (parsing type {})'.format(
                e.type_id, '{:08x}'.format(vnode.file_sub_type) if vnode.file_sub_type is not None else '?')
            self.tree.set_tree(root(lambda: [TreeItem(['ADF not readable', 'missing type', message])]), None)
            return
        if adf is None:
            self.tree.set_tree(root(lambda: [TreeItem(['ADF not readable', '', 'DECA returned nothing'])]), None)
            return

        def raw_dump():
            if (vnode.size_u or 0) > RAW_DUMP_LIMIT:
                return ('DECA text dump disabled for files over {} MB ({} bytes): use the tree and "Export text".'
                        .format(RAW_DUMP_LIMIT // (1024 * 1024), vnode.size_u))
            return adf.dump_to_string(vfs)

        self.tree.set_tree(adf_tree(adf, noms_for(vfs), label), raw_dump, expand_depth=3)


def to_text(v):
    if v is None:
        return None
    return v.decode('utf-8', 'replace') if isinstance(v, (bytes, bytearray)) else str(v)
