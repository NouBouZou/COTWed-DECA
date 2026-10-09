"""Havok viewer (COTWed DECA): objects of a packfile (.ban, .bsk, .brd, .navmeshc, .physicsc), decoded members."""
from .viewer import *
from PySide6.QtWidgets import QVBoxLayout

from .reading import node_bytes
from .tree_items import TreeItem, root, havok_tree
from .tree_view import TreeViewer


def havok_text(pf) -> str:
    from deca.ff_havok import summary_lines
    lines = summary_lines(pf)
    lines += ['', 'objects:'] + ['  @0x{:08x} {:8d} bytes  {}'.format(o.offset, o.size, o.class_name) for o in pf.objects]
    return '\n'.join(lines)


class DataViewerHavok(DataViewer):
    def __init__(self):
        super().__init__()
        self.tree = TreeViewer()
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.tree)
        self.setLayout(layout)

    def vnode_process(self, vfs: VfsProcessor, vnode: VfsNode):
        from deca.ff_havok import Packfile
        data = node_bytes(vfs, vnode)
        try:
            pf = Packfile(data)
            tree = havok_tree(pf)
            raw = lambda: havok_text(pf)   # noqa: E731
        except Exception as e:  # noqa: BLE001 - unreadable file: say why
            cols = ['Havok packfile not readable', type(e).__name__, str(e)]
            tree, raw = root(lambda: [TreeItem(cols)]), None
        self.tree.set_tree(tree, raw, expand_depth=1)
