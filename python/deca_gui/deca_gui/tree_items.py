"""
Lazy trees for the viewers (RTPC, ADF, save file), without Qt: built and tested as plain Python objects.

A TreeItem has display columns and an optional loader that creates its children the first time they are needed
(when the view expands the item). Large arrays are split into ranges of at most CHUNK items, recursively: the
24.7 MB text block of string_ids.stringlookup becomes a few levels of ranges instead of 24.7 million rows.

Columns: name, type, value, location (hash and offset). Names come from cotw/noms.py (DECA strings, hp_noms.tsv,
names registered by the caller); u32 values that are the hash of a known name are annotated, as in rtpc_dump.
"""
from typing import Callable, List, Optional, Sequence

from . import cotw_tools

COLUMNS = ['Name', 'Type', 'Value', 'Hash / offset']
CHUNK = 256          # maximum children per level for a range of array elements
PREVIEW = 16         # array elements shown in the value column
BYTES_PER_ROW = 32   # byte arrays: one child row per BYTES_PER_ROW bytes (hex and text)
MAX_TEXT = 300


class TreeItem:
    __slots__ = ('cols', 'parent', 'row', '_children', '_load', 'tooltip', 'payload')

    def __init__(self, cols: Sequence[str], load: Optional[Callable[[], List['TreeItem']]] = None,
                 tooltip: Optional[str] = None, payload=None):
        self.cols = list(cols) + [''] * (len(COLUMNS) - len(cols))
        self.parent = None
        self.row = 0
        self._children = None if load is not None else []
        self._load = load
        self.tooltip = tooltip
        self.payload = payload

    def has_children(self) -> bool:
        return self._load is not None if self._children is None else bool(self._children)

    def children(self) -> List['TreeItem']:
        if self._children is None:
            items = self._load() or []
            for i, it in enumerate(items):
                it.parent, it.row = self, i
            self._children, self._load = items, None
        return self._children

    def loaded(self) -> bool:
        return self._children is not None

    def walk(self, limit: Optional[int] = None):
        """Depth-first, loading children (find, export). ``limit``: maximum items visited."""
        stack, n = [self], 0
        while stack:
            it = stack.pop()
            yield it
            n += 1
            if limit is not None and n >= limit:
                return
            stack.extend(reversed(it.children()))

    def text_lines(self, depth: int = 0, limit: int = 200000) -> List[str]:
        """Indented text of the subtree (export, tests), at most ``limit`` lines."""
        out = []
        stack = [(self, depth)]
        while stack and len(out) < limit:
            it, d = stack.pop()
            out.append('{}{}'.format('  ' * d, '  '.join(c for c in it.cols if c)))
            stack.extend((c, d + 1) for c in reversed(it.children()))
        return out


def root(children_loader) -> TreeItem:
    return TreeItem(['root'], load=children_loader)


def text_preview(data: bytes) -> str:
    """Printable text of a byte block, '.' for the rest (strings of a text block are 0-terminated)."""
    return ''.join(chr(b) if 0x20 <= b < 0x7f else '.' for b in data)


def utf8_preview(data: bytes, limit: int = MAX_TEXT) -> str:
    text = bytes(data[:limit]).decode('utf-8', 'replace').replace('\0', ' | ')
    return text + ('...' if len(data) > limit else '')


def ranges(n: int, make_leaf: Callable[[int], TreeItem], label: str = '', chunk: int = CHUNK,
           lo: int = 0, hi: Optional[int] = None,
           prepare: Optional[Callable[[int, int], None]] = None) -> List[TreeItem]:
    """Children for elements lo..hi-1: leaves if there are at most ``chunk``, otherwise ranges of equal size
    (a power of ``chunk``), each loading its own children when expanded. ``prepare(lo, hi)`` runs before a batch
    of leaves is made (e.g. resolve the names of their hashes in one query)."""
    hi = n if hi is None else hi
    count = hi - lo
    if count <= chunk:
        if prepare is not None:
            prepare(lo, hi)
        return [make_leaf(i) for i in range(lo, hi)]
    step = chunk
    while step * chunk < count:
        step *= chunk
    out = []
    for a in range(lo, hi, step):
        b = min(a + step, hi)
        out.append(TreeItem(['{}[{} .. {}]'.format(label, a, b - 1), '', '{} elements'.format(b - a)],
                            load=lambda a=a, b=b: ranges(n, make_leaf, label, chunk, a, b, prepare)))
    return out


def byte_rows(data: bytes, base_offset: Optional[int]) -> List[TreeItem]:
    """Children of a byte array: one row of BYTES_PER_ROW bytes (hex, then text), grouped by ranges."""
    n_rows = (len(data) + BYTES_PER_ROW - 1) // BYTES_PER_ROW

    def leaf(r):
        chunk = data[r * BYTES_PER_ROW:(r + 1) * BYTES_PER_ROW]
        off = r * BYTES_PER_ROW
        loc = '+0x{:x}'.format(off) if base_offset is None else '@0x{:x}'.format(base_offset + off)
        return TreeItem(['+{}'.format(off), 'bytes', '{:<{w}}  {}'.format(chunk.hex(' '), text_preview(chunk),
                                                                       w=BYTES_PER_ROW * 3), loc])
    return ranges(n_rows, leaf, label='rows ')


# ---------------------------------------------------------------------------
# RTPC
# ---------------------------------------------------------------------------

def file_strings(rtpc) -> dict:
    """hash32 -> [string] for every string value of an RTPC file.

    Why: a node is often named after one of its own string values (equipment_data: node equipment_ammo_243_sp_01
    has name = "equipment_ammo_243_sp_01"), so a file read outside a project (a mod, an extracted sample) or not
    scanned yet still shows its names. The project names (core_strings, dictionary) take precedence."""
    import noms as N
    from deca.ff_rtpc import k_type_str
    out = {}
    stack = [rtpc.root_node]
    while stack:
        node = stack.pop()
        for p in node.prop_table:
            if p.type == k_type_str and p.data:
                text = bytes(p.data).decode('utf-8', 'replace')
                lst = out.setdefault(N.hash32(text), [])
                if text not in lst:
                    lst.append(text)
        stack.extend(node.child_table)
    return out


def rtpc_tree(rtpc, noms) -> TreeItem:
    """Tree of an RTPC file (deca.ff_rtpc.Rtpc). Every hash of the file is resolved in one batch first; names
    unknown to the project are looked up among the string values of the file (file_strings)."""
    cotw_tools.ensure_path()
    import rtpc_dump as D
    from deca.ff_rtpc import k_type_str
    noms.prefetch(D.collect_hashes(rtpc))
    local = file_strings(rtpc)
    table = dict(noms.table())
    for h, names in local.items():
        if not table.get(h):
            table[h] = names

    def name_of(h):
        names = table.get(h)
        return names[0] if names else noms.get(h)

    def node_item(node, label_prefix=''):
        name = name_of(node.name_hash)
        cls = node.prop_map.get(D.W.name_hash('_class')) if hasattr(node, 'prop_map') else None
        hint = ''
        if cls is not None and cls.type == k_type_str:
            hint = bytes(cls.data).decode('utf-8', 'replace')
        return TreeItem(
            [label_prefix + (name or '0x{:08x}'.format(node.name_hash)),
             'node: {} props, {} children'.format(len(node.prop_table), len(node.child_table)), hint,
             '0x{:08x} @0x{:x}'.format(node.name_hash, node.data_offset or 0)],
            load=(lambda node=node, name=name: [prop_item(p, name) for p in node.prop_table] +
                  [node_item(c) for c in node.child_table])
            if (node.prop_table or node.child_table) else None,
            payload=node)

    def prop_item(p, parent_name):
        from .reading import value_note
        name = name_of(p.name_hash)
        value = D.format_value(p, table)
        note = value_note(parent_name, name, value)
        if note:
            value = '{}   [{}]'.format(value, note)
        return TreeItem([name or '0x{:08x}'.format(p.name_hash), D.type_name(p.type), value,
                         '0x{:08x} @0x{:x}'.format(p.name_hash, p.pos or 0)], tooltip=value, payload=p)

    label = 'RTPC v{}: '.format(rtpc.version) if isinstance(rtpc.version, int) else 'RTPC ({}): '.format(rtpc.version)
    return root(lambda: [node_item(rtpc.root_node, label)])


# ---------------------------------------------------------------------------
# ADF
# ---------------------------------------------------------------------------

def adf_tree(adf, noms, file_label: str = '') -> TreeItem:
    """Tree of an ADF file read by DECA (deca.ff_adf.Adf with table_instance_full_values)."""
    cotw_tools.ensure_path()
    import citations as CI
    from deca.ff_adf import AdfValue, MetaType, adf_type_id_to_str
    type_map = adf.extended_map_typedef

    def tname(type_id) -> str:
        try:
            return adf_type_id_to_str(type_id, type_map)
        except Exception:  # noqa: BLE001 - unknown type id: show the hash
            return '0x{:08x}'.format(type_id)

    def int_note(v) -> str:
        if isinstance(v, int) and not isinstance(v, bool) and 0 < v <= 0xFFFFFFFF:
            name = noms.get(v)
            if name:
                return '  = hash of "{}"'.format(name)
        return ''

    def scalar_text(v) -> str:
        if isinstance(v, float):
            return '{:.7g}'.format(v)
        if isinstance(v, (bytes, bytearray)):
            return '"{}"'.format(utf8_preview(v))
        return '{}{}'.format(v, int_note(v))

    def prefetch_members(values):
        """Integers of these values and of their direct members: their names in one batch (not one query each)."""
        found = []
        for x in values:
            x = x.value if isinstance(x, AdfValue) else x
            if isinstance(x, dict):
                found += [m.value if isinstance(m, AdfValue) else m for m in x.values()]
            else:
                found.append(x)
        noms.prefetch(x for x in found if isinstance(x, int) and not isinstance(x, bool))

    def loc(v) -> str:
        return '@0x{:x}'.format(v.data_offset) if isinstance(v, AdfValue) and v.data_offset is not None else ''

    def value_item(label: str, v) -> TreeItem:
        if not isinstance(v, AdfValue):
            if isinstance(v, tuple):
                return TreeItem([label, 'pointer', repr(v[1] if len(v) > 1 else v)])
            return TreeItem([label, '', 'absent' if v is None else scalar_text(v)])
        td = type_map.get(v.type_id)
        mt = td.metatype if td is not None else None
        if v.type_id == CI.DEFERRED_TYPE:
            return value_item(label + ' (deferred)', v.value)
        if isinstance(v.value, dict):
            members = list(v.value.items())

            def load(members=members):
                prefetch_members([mv for _k, mv in members])
                return [value_item(k, mv) for k, mv in members]
            return TreeItem([label, tname(v.type_id), '{} members'.format(len(members)), loc(v)], load=load,
                            payload=v)
        if mt in (MetaType.Array, MetaType.InlineArray):
            return array_item(label, v)
        if mt == MetaType.StringHash:
            width = (td.size or 4) * 8 if td is not None else 32
            name = v.hash_string.decode('utf-8', 'replace') if isinstance(v.hash_string, bytes) else v.hash_string
            name = name or noms.wide(v.value, width if width in (32, 48, 64) else 32)
            text = '0x{:x}{}'.format(v.value, '  "{}"'.format(name) if name else '  (unknown)')
            return TreeItem([label, tname(v.type_id), text, loc(v)], tooltip=text, payload=v)
        if mt == MetaType.Enumeration:
            es = v.enum_string.decode('utf-8', 'replace') if isinstance(v.enum_string, bytes) else v.enum_string
            return TreeItem([label, tname(v.type_id), '{} ({})'.format(es, v.value), loc(v)], payload=v)
        text = scalar_text(v.value)
        return TreeItem([label, tname(v.type_id), text, loc(v)], tooltip=text, payload=v)

    def array_item(label: str, v) -> TreeItem:
        elems = v.value if v.value is not None else []
        n = len(elems)
        kind = tname(v.type_id)
        if isinstance(elems, (bytes, bytearray)):
            data = bytes(elems)
            value = '{} bytes: {}'.format(n, utf8_preview(data, 120)) if n else '0 bytes'
            return TreeItem([label, kind, value, loc(v)], load=(lambda: byte_rows(data, v.data_offset)) if n else None,
                            tooltip=value, payload=v)
        if n and not isinstance(elems[0], AdfValue):
            values = list(elems)
            noms.prefetch(x for x in values[:4096] if isinstance(x, int))
            shown = ', '.join(scalar_text(x).split('  = ')[0] for x in values[:PREVIEW])
            value = '{} elements [{}{}]'.format(n, shown, ', ...' if n > PREVIEW else '')
            return TreeItem([label, kind, value, loc(v)],
                            load=lambda: ranges(n, lambda i: TreeItem(['[{}]'.format(i), '', scalar_text(values[i])])),
                            tooltip=value, payload=v)
        value = '{} elements'.format(n)
        return TreeItem([label, kind, value, loc(v)],
                        load=(lambda: ranges(n, lambda i: value_item('[{}]'.format(i), elems[i]),
                                             prepare=lambda lo, hi: prefetch_members(elems[lo:hi]))) if n else None,
                        payload=v)

    def instances():
        out = []
        for ins, fv in zip(adf.table_instance, adf.table_instance_full_values):
            name = ins.name.decode('utf-8', 'replace') if isinstance(ins.name, bytes) else str(ins.name)
            item = value_item(name, fv)
            item.cols[0] = 'instance {}'.format(name)
            item.cols[3] = '0x{:08x} {}'.format(ins.type_hash, item.cols[3])
            out.append(item)
        return out

    return root(lambda: [TreeItem(['ADF {}'.format(file_label).strip(), '{} instances'.format(len(adf.table_instance)),
                                   '{} types'.format(len(adf.table_typedef))], load=instances)])
