from collections import OrderedDict

from PySide6.QtGui import QColor


def used_color_calc(level):
    return QColor(0x00, max(0x10, 0xff - 0x20 * level), 0x00, 0xff)


class NodeCache:
    """
    Recently displayed nodes, by uid.

    Qt asks a model for every cell and several roles of each visible row; without a cache each request was one
    SQLite query (about 500 per repaint of the directory tree). Bounded so that scrolling through a million nodes
    does not keep them all in memory. Must be cleared whenever the model is reset.
    """
    def __init__(self, maxsize=4096):
        self.maxsize = maxsize
        self._nodes = OrderedDict()

    def clear(self):
        self._nodes.clear()

    def get(self, vfs_view, uid):
        node = self._nodes.get(uid)
        if node is None:
            node = vfs_view.node_where_uid(uid)
            self._nodes[uid] = node
            if len(self._nodes) > self.maxsize:
                self._nodes.popitem(last=False)
        else:
            self._nodes.move_to_end(uid)
        return node
