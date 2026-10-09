"""Structure viewer (COTWed DECA): text description of small formats decoded by the fork (fmod_guids, TrueType)."""
from typing import List

from .viewer_text import DataViewerText
from .viewer import VfsNode, VfsProcessor
from .reading import node_bytes
from deca.ff_types import FTYPE_FMOD_GUIDS, FTYPE_TTF


def structure_lines(file_type: str, data: bytes) -> List[str]:
    if file_type == FTYPE_FMOD_GUIDS:
        from deca.ff_fmod_guids import dump_lines
        return dump_lines(data)
    if file_type == FTYPE_TTF:
        from deca.ff_ttf import dump_lines
        return dump_lines(data)
    raise ValueError('no structure view for type {}'.format(file_type))


class DataViewerStructure(DataViewerText):
    def vnode_process(self, vfs: VfsProcessor, vnode: VfsNode):
        self.content_set(structure_lines(vnode.file_type, node_bytes(vfs, vnode)))
