"""
Helpers shared by the COTW viewers: one name cache per project, bytes of a node, ADF instance types (header only),
and what the research log established about some values (annotations shown next to them).
"""
import io
import weakref
from typing import List, Optional

from . import cotw_tools

_noms_by_vfs = weakref.WeakKeyDictionary()

# ADF instance types with a dedicated viewer (research log: string_ids 2026-10-03, XLSBook 2026-10-02)
STRINGLOOKUP_TYPE = 0xc7254276
XLSBOOK_TYPE = 0x0b73315d
RAW_DUMP_LIMIT = 8 * 1024 * 1024   # DECA's flat ADF dump of larger files takes minutes and gigabytes

# secondary_ids: DLC that unlock an item ("-1" = base game); these three are given to every player (store survey
# of 2026-10-01). locked = 5 hides a base-game item from the store (test4).
FREE_DLC = {'3988076135', '2667029300', '1246326955'}


def noms_for(vfs):
    """Name cache of a project (cotw/noms.py), kept as long as the project is open."""
    cotw_tools.ensure_path()
    import noms as N
    try:
        n = _noms_by_vfs.get(vfs)
    except TypeError:
        n = None
    if n is None:
        n = N.Noms(vfs)
        try:
            _noms_by_vfs[vfs] = n
        except TypeError:
            pass
    return n


def node_bytes(vfs, vnode) -> bytes:
    with vfs.file_obj_from(vnode) as f:
        return f.read(vnode.size_u)


def adf_instance_types(data: bytes) -> List[int]:
    """Type hash of each instance, read from the header only (fast even for the 38.8 MB string_ids)."""
    from deca.ff_adf import Adf
    from deca.file import ArchiveFile
    base = 5 if data[5:9] == b' FDA' else 0
    if data[base:base + 4] != b' FDA':
        return []
    try:
        adf = Adf()
        adf.deserialize(ArchiveFile(io.BytesIO(data[base:])), process_instances=False)
    except Exception:  # noqa: BLE001 - not routable: the generic ADF viewer will report the problem
        return []
    return [ins.type_hash for ins in adf.table_instance]


def value_note(parent_name: Optional[str], prop_name: Optional[str], text: str) -> str:
    """Annotation from the research log for a property value, or ''."""
    value = text.strip('"')
    if parent_name == 'secondary_ids':
        if value == '-1':
            return 'base game'
        if value.isdigit():
            return 'DLC id, given to every player' if value in FREE_DLC else 'DLC id'
    if prop_name == 'locked' and value.split(' ')[0] == '5':
        return 'hidden from the store (base game item)'
    return ''
