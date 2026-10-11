"""Structure viewer (COTWed DECA): text description of formats decoded by the fork (fmod_guids, TrueType, btc, FMOD
banks, FSB5 sounds, MDI model instances, Scaleform GFx, Bink, HumanIK, IVF and WebM videos)."""
from typing import Dict, List, Optional

from .viewer_text import DataViewerText
from .viewer import VfsNode, VfsProcessor
from .reading import fmod_names_for, node_bytes, noms_for
from deca.ff_types import FTYPE_BINK_BIK, FTYPE_BTC, FTYPE_FMOD_GUIDS, FTYPE_FSB5C, FTYPE_GFX, FTYPE_HIKCC, FTYPE_MDI, \
    FTYPE_RIFF, FTYPE_TTF, FTYPE_IVF, FTYPE_WEBM


def structure_lines(file_type: str, data: bytes, noms=None, fmod_names: Optional[Dict] = None) -> List[str]:
    if file_type == FTYPE_FMOD_GUIDS:
        from deca.ff_fmod_guids import dump_lines
        return dump_lines(data, fmod_names)
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
    if file_type == FTYPE_RIFF:
        from deca.ff_fmod_bank import dump_lines
        return dump_lines(data)
    if file_type == FTYPE_FSB5C:
        from deca.ff_fsb5 import wavc_dump_lines
        return wavc_dump_lines(data)
    if file_type == FTYPE_MDI:
        from deca.ff_mdi import dump_lines
        return dump_lines(data)
    if file_type == FTYPE_GFX:
        from deca.ff_gfx import dump_lines
        return dump_lines(data)
    if file_type == FTYPE_BINK_BIK:
        from deca.ff_bink import dump_lines
        return dump_lines(data)
    if file_type == FTYPE_HIKCC:
        from deca.ff_hikcc import dump_lines
        return dump_lines(data)
    if file_type in (FTYPE_IVF, FTYPE_WEBM):
        from deca.ff_video import dump_lines
        return dump_lines(data)
    raise ValueError('no structure view for type {}'.format(file_type))


class DataViewerStructure(DataViewerText):
    def vnode_process(self, vfs: VfsProcessor, vnode: VfsNode):
        if vnode.file_type == FTYPE_RIFF:
            # FMOD banks reach 100 MB: only the chunk headers, the string table and the FSB5 headers are read
            from deca.ff_fmod_bank import dump_lines_from
            with vfs.file_obj_from(vnode) as f:
                self.content_set(dump_lines_from(f, vnode.size_u))
            return
        if vnode.file_type == FTYPE_BINK_BIK:
            # videos reach 88 MB: only the header and the frame index are read
            from deca.ff_bink import dump_lines_from as bink_lines
            with vfs.file_obj_from(vnode) as f:
                self.content_set(bink_lines(f, vnode.size_u))
            return
        fmod_names = fmod_names_for(vfs) if vnode.file_type == FTYPE_FMOD_GUIDS else None
        self.content_set(structure_lines(vnode.file_type, node_bytes(vfs, vnode), noms_for(vfs), fmod_names))
