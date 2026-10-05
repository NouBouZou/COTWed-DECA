"""RTPC viewer: lazy tree with names resolved (DECA strings, cotw/dictionnaires/hp_noms.tsv) and value notes."""
from .viewer import *
from PySide6.QtWidgets import QVBoxLayout

from .reading import node_bytes, noms_for
from .tree_items import TreeItem, root, rtpc_tree
from .tree_view import TreeViewer


class DataViewerRtpc(DataViewer):
    def __init__(self):
        super().__init__()
        self.tree = TreeViewer()
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.tree)
        self.setLayout(layout)

    def vnode_process(self, vfs: VfsProcessor, vnode: VfsNode):
        import deca.ff_rtpc_write as W
        from deca.ff_rtpc import RtpcVisitorDumpToString
        data = node_bytes(vfs, vnode)

        def raw_dump():
            dump = RtpcVisitorDumpToString(vfs)
            dump.visit(data)
            return dump.result()

        try:
            tree = rtpc_tree(W.rtpc_from_bytes(data), noms_for(vfs))
        except Exception as e:  # noqa: BLE001 - unreadable file: say why, keep DECA's dump available
            # the text is taken now: "e" is deleted at the end of the except block, the loader runs later
            cols = ['RTPC not readable', type(e).__name__, str(e)]
            tree = root(lambda: [TreeItem(cols)])
        self.tree.set_tree(tree, raw_dump, expand_depth=4)
