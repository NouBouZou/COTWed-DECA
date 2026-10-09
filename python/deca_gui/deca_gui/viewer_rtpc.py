"""RTPC viewer: lazy tree with names resolved (DECA strings, cotw/dictionnaires/hp_noms.tsv) and value notes."""
from .viewer import *
from PySide6.QtWidgets import QVBoxLayout

from .reading import node_bytes, noms_for
from .tree_items import TreeItem, root, rtpc_tree
from .tree_view import TreeViewer


def inline_text(data: bytes, noms) -> str:
    """Text dump of an inline RTPC file (world.bin, *.smodc), names resolved like the tree."""
    from . import cotw_tools
    cotw_tools.ensure_path()
    import rtpc_dump as D
    from deca.ff_rtpc_inline import inline_from_bytes, to_rtpc
    rtpc = to_rtpc(inline_from_bytes(data))
    noms.prefetch(D.collect_hashes(rtpc))
    lines = []
    D.format_node(rtpc.root_node, noms.table(), 'racine', 0, None, lines)
    return '\n'.join(lines)


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
        from deca.ff_types import FTYPE_RTPC_INLINE
        data = node_bytes(vfs, vnode)
        inline = vnode.file_type == FTYPE_RTPC_INLINE

        def raw_dump():
            if inline:
                # DECA has no reader for inline RTPC: the text dump is the fork's (cotw/rtpc_dump.py)
                return inline_text(data, noms_for(vfs))
            dump = RtpcVisitorDumpToString(vfs)
            dump.visit(data)
            return dump.result()

        try:
            if inline:
                from deca.ff_rtpc_inline import inline_from_bytes, to_rtpc
                rtpc = to_rtpc(inline_from_bytes(data))
            else:
                rtpc = W.rtpc_from_bytes(data)
            tree = rtpc_tree(rtpc, noms_for(vfs))
        except Exception as e:  # noqa: BLE001 - unreadable file: say why, keep DECA's dump available
            # the text is taken now: "e" is deleted at the end of the except block, the loader runs later
            cols = ['RTPC not readable', type(e).__name__, str(e)]
            tree = root(lambda: [TreeItem(cols)])
        self.tree.set_tree(tree, raw_dump, expand_depth=4)
