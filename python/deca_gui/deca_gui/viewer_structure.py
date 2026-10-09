"""Structure viewer (COTWed DECA): text description of small formats decoded by the fork (fmod_guids, TrueType)."""
from typing import List

from .viewer_text import DataViewerText
from .viewer import VfsNode, VfsProcessor
from .reading import node_bytes, noms_for
from deca.ff_types import FTYPE_BTC, FTYPE_FMOD_GUIDS, FTYPE_TTF


def structure_lines(file_type: str, data: bytes, noms=None) -> List[str]:
    if file_type == FTYPE_FMOD_GUIDS:
        from deca.ff_fmod_guids import dump_lines
        return dump_lines(data)
    if file_type == FTYPE_TTF:
        from deca.ff_ttf import dump_lines
        return dump_lines(data)
    if file_type == FTYPE_BTC:
        from deca.ff_btc import Btc
        btc = Btc(data)
        names = {}
        if noms is not None:
            noms.prefetch([p['hash'] for p in btc.params])
            names = {p['hash']: noms.get(p['hash']) for p in btc.params}
        return btc.dump_lines(names)
    raise ValueError('no structure view for type {}'.format(file_type))


class DataViewerStructure(DataViewerText):
    def vnode_process(self, vfs: VfsProcessor, vnode: VfsNode):
        self.content_set(structure_lines(vnode.file_type, node_bytes(vfs, vnode), noms_for(vfs)))
