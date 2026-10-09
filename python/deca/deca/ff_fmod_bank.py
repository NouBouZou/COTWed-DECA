"""
FMOD Studio banks of theHunter: COTW (*.fmod_bankc, DECA type riff): 171 files, 1.9 GB; 166 of them had no name.

COTWed DECA, 2026-10-09. A bank is a RIFF file of form 'FEV ': chunk 'FMT ' (u32 format version, u32 oldest
compatible version: 125 and 119 in COTW), then LIST 'PROJ' with the project chunks. This module walks the chunk
tree of any bank (headers only: the sound data is never read), and decodes the string table of the strings bank
(sound/fmod_banks/master_strings.fmod_bankc), chunk STDT: the full path of every FMOD object by its GUID.

STDT layout (decoded on master_strings.fmod_bankc: 4 001 names, 6 214 nodes, 206 701 bytes, read to the last byte;
string_table_bytes rebuilds these 206 701 bytes identically from the 4 001 names):

    u32  1 (version)
    u32  node array header: count in bits 1-15 (bit 0 set), element size (8) in bits 16-31
         nodes, 8 bytes each: u24 text offset (0xFFFFFF: none), u8 first character in lower case (0: end of a name
         that is also the prefix of others, and the root), u24 first child index (or, for a node without child,
         the GUID index of the name ending there), u8 child count
    u32  GUID array header: count (bit 0 set), element size (16)
         GUIDs, sorted by their first u32 (Windows layout, as FMOD displays them)
    u32  text block header: size in bytes in bits 0-15, element size (1) in bits 16-31
         node texts, each NUL-terminated, original case (the node character is the first one, lower-cased for the
         case-insensitive search of the runtime)
    u16  count (= GUID count), u24 node index of each GUID's name
    u16  count (= node count), u24 parent index of each node (0xFFFFFF for the root)

A name is the concatenation of the node texts from the root to its node: 'event:/' 'weapons/' 'bullet_impact/'...
The game's bank files are sound/fmod_banks/<last part of the bank path>.fmod_bankc ('bank:/weapons/weapons' ->
sound/fmod_banks/weapons.fmod_bankc): this names the 166 banks that had no name.
"""
import io
import struct
import uuid
from typing import BinaryIO, Dict, Iterable, List, Optional, Tuple

FORM_FEV = b'FEV '
CONTAINERS = (b'RIFF', b'LIST')
BANK_DIR = 'sound/fmod_banks/'
BANK_EXT = '.fmod_bankc'
NONE24 = 0xFFFFFF


class EFmodBank(Exception):
    pass


class Chunk:
    __slots__ = ('cid', 'form', 'offset', 'size', 'children', 'head')

    def __init__(self, cid: str, form: Optional[str], offset: int, size: int, head: bytes = b''):
        self.cid, self.form, self.offset, self.size, self.head = cid, form, offset, size, head
        self.children: List['Chunk'] = []

    @property
    def label(self) -> str:
        return '{} {}'.format(self.cid, self.form) if self.form else self.cid

    @property
    def body(self) -> int:
        """Offset of the chunk data (after the form of a RIFF / LIST)."""
        return self.offset + (12 if self.form else 8)

    def find(self, cid: str) -> Optional['Chunk']:
        for c in self.children:
            if c.cid == cid:
                return c
            sub = c.find(cid)
            if sub is not None:
                return sub
        return None


def read_chunks(f: BinaryIO, size: int, head_bytes: int = 16) -> Chunk:
    """Chunk tree from the headers only (seek past the data). head: first bytes of each leaf chunk."""
    f.seek(0)
    h = f.read(12)
    if len(h) < 12 or h[:4] != b'RIFF':
        raise EFmodBank('not a RIFF file')
    riff_size = struct.unpack_from('<I', h, 4)[0]
    if 8 + riff_size > size:
        raise EFmodBank('RIFF size {} beyond the file ({} bytes)'.format(riff_size, size))
    top = Chunk('RIFF', h[8:12].decode('latin-1'), 0, riff_size)
    _read_children(f, top, 12, 8 + riff_size, head_bytes)
    return top


def _read_children(f: BinaryIO, parent: Chunk, start: int, end: int, head_bytes: int):
    off = start
    while off + 8 <= end:
        f.seek(off)
        h = f.read(12)
        cid = h[:4]
        size = struct.unpack_from('<I', h, 4)[0]
        if off + 8 + size > end:
            raise EFmodBank('chunk {} at 0x{:x}: size {} beyond its parent'.format(cid, off, size))
        if cid in CONTAINERS:
            c = Chunk(cid.decode('latin-1'), h[8:12].decode('latin-1'), off, size)
            _read_children(f, c, off + 12, off + 8 + size, head_bytes)
        else:
            f.seek(off + 8)
            c = Chunk(cid.decode('latin-1'), None, off, size, f.read(min(size, head_bytes)))
        parent.children.append(c)
        off += 8 + size + (size & 1)


def chunk_data(f: BinaryIO, c: Chunk) -> bytes:
    f.seek(c.offset + 8)
    return f.read(c.size)


def format_versions(f: BinaryIO, top: Chunk) -> Optional[Tuple[int, int]]:
    fmt = top.find('FMT ')
    if fmt is None or fmt.size < 8:
        return None
    return struct.unpack_from('<II', chunk_data(f, fmt), 0)


# ---------------------------------------------------------------------------------------------------------
# String table (STDT)
# ---------------------------------------------------------------------------------------------------------

class StringTable:
    def __init__(self, data: bytes):
        self.data = d = bytes(data)

        def u24(o):
            return d[o] | d[o + 1] << 8 | d[o + 2] << 16

        def array_header(p):
            v = struct.unpack_from('<I', d, p)[0]
            low = v & 0xFFFF
            return (low >> 1 if low & 1 else low), v >> 16

        try:
            self.version = struct.unpack_from('<I', d, 0)[0]
            nn, ne = array_header(4)
            nodes_off = 8
            p = nodes_off + nn * ne
            gn, ge = array_header(p)
            guids_off = p + 4
            p = guids_off + gn * ge
            text_size, _te = array_header(p)
            text_off = p + 4
            # the text block ends where the two index tables (sizes known from the counts) begin
            text_end = len(d) - (2 + 3 * gn) - (2 + 3 * nn)
            c1 = struct.unpack_from('<H', d, text_end)[0]
            c2 = struct.unpack_from('<H', d, text_end + 2 + 3 * gn)[0]
        except struct.error as e:
            raise EFmodBank('string table too short: {}'.format(e))
        if ne != 8 or ge != 16 or c1 != gn or c2 != nn or text_end < text_off:
            raise EFmodBank('string table layout not handled (nodes {}x{}, GUIDs {}x{}, index counts {} {})'.format(
                nn, ne, gn, ge, c1, c2))
        self.text_size_field = text_size
        map_off, par_off = text_end + 2, text_end + 2 + 3 * gn + 2
        self.node_count, self.guid_count = nn, gn
        self.text = d[text_off:text_end]

        def node(i):
            o = nodes_off + 8 * i
            return u24(o), d[o + 3], u24(o + 4), d[o + 7]

        def text_at(a):
            if a == NONE24:
                return ''
            if a >= len(self.text):
                raise EFmodBank('node text offset 0x{:x} beyond the text block'.format(a))
            e = self.text.index(b'\0', a)
            return self.text[a:e].decode('utf-8', 'replace')

        texts: Dict[int, str] = {}
        self.names: List[Tuple[uuid.UUID, str]] = []
        self.leaf_mismatches = 0
        for g in range(gn):
            guid = uuid.UUID(bytes_le=d[guids_off + 16 * g:guids_off + 16 * g + 16])
            i, parts, steps = u24(map_off + 3 * g), [], 0
            a, _c, value, children = node(i)
            if children == 0 and value != g:
                self.leaf_mismatches += 1
            while i not in (NONE24, 0):
                if i >= nn or steps > nn:
                    raise EFmodBank('broken parent chain for GUID {}'.format(guid))
                if i not in texts:
                    texts[i] = text_at(node(i)[0])
                parts.append(texts[i])
                i = u24(par_off + 3 * i)
                steps += 1
            self.names.append((guid, ''.join(reversed(parts))))

    def by_guid(self) -> Dict[uuid.UUID, str]:
        return dict(self.names)

    def kinds(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for _g, n in self.names:
            k = n.split(':/', 1)[0] if ':/' in n else '?'
            out[k] = out.get(k, 0) + 1
        return out

    def bank_paths(self) -> List[str]:
        return sorted(n for _g, n in self.names if n.startswith('bank:/'))


def _u24(v: int) -> bytes:
    return struct.pack('<I', v)[:3]


def _array_header(count: int, elem: int) -> bytes:
    return struct.pack('<I', ((count << 1) | 1) | (elem << 16))


class _Node:
    def __init__(self, text: str = ''):
        self.text = text
        self.children = {}
        self.end: Optional[int] = None


def string_table_bytes(entries: Iterable[Tuple[uuid.UUID, str]]) -> bytes:
    """STDT chunk data for these names, in the game's exact layout: StringTable(string_table_bytes(names)) gives
    the names back, and the 4 001 names of master_strings.fmod_bankc give its 206 701 bytes back, byte for byte.
    Node characters are lower case, texts keep their case; a name that is also the prefix of other names ends on a
    terminal node (character 0)."""
    # GUIDs sorted by their first u32, then their bytes
    entries = sorted(entries, key=lambda e: (struct.unpack('<I', e[0].bytes_le[:4])[0], e[0].bytes_le))
    root = _Node()
    for g, (_guid, name) in enumerate(entries):
        n = root
        for ch in name:
            n = n.children.setdefault(ch.lower(), _Node(ch))
        n.end = g

    def compress(n: _Node):
        for c in n.children.values():
            while len(c.children) == 1 and c.end is None:
                (only,) = c.children.values()
                c.text += only.text
                c.children, c.end = only.children, only.end
            compress(c)
    compress(root)

    # Order of the game (checked byte for byte on master_strings): depth first; visiting a node writes its text,
    # reserves a contiguous block for its children (the terminal first, then the children sorted by their original
    # text, byte order: 'UNKNOWN' before 'animals/'), then visits these children in order.
    records: List[Optional[tuple]] = [None]
    texts = bytearray()
    parents: List[int] = [NONE24]
    leaf_of_guid = {}

    def visit(n: Optional[_Node], index: int, value: Optional[int]):
        if n is None:                                       # terminal: end of a name prefix of others
            records[index] = (NONE24, 0, value, 0)
            leaf_of_guid[value] = index
            return
        if n is root:
            a, c = NONE24, 0
        else:
            a, c = len(texts), ord(n.text[0].lower())
            texts.extend(n.text.encode('utf-8') + b'\0')
        kids: List[Tuple[Optional[_Node], Optional[int]]] = []
        if n is not root and n.end is not None and n.children:
            kids.append((None, n.end))
        kids += [(child, None) for child in sorted(n.children.values(), key=lambda x: x.text.encode('utf-8'))]
        if len(kids) > 255:
            raise ValueError('{} children under one node: the count is a u8'.format(len(kids)))
        if not kids:
            records[index] = (a, c, n.end, 0)
            leaf_of_guid[n.end] = index
            return
        first = len(records)
        records.extend([None] * len(kids))
        parents.extend([index] * len(kids))
        records[index] = (a, c, first, len(kids))
        for k, (kid, v) in enumerate(kids):
            visit(kid, first + k, v)
    visit(root, 0, None)

    if len(records) > 0x7FFF or len(entries) > 0x7FFF or len(texts) > 0xFFFF:
        raise ValueError('table too large for the layout seen in COTW ({} nodes, {} names, {} text bytes)'.format(
            len(records), len(entries), len(texts)))
    out = bytearray(struct.pack('<I', 1))
    out += _array_header(len(records), 8)
    for a, c, b, k in records:
        out += _u24(a) + bytes([c]) + _u24(b) + bytes([k])
    out += _array_header(len(entries), 16)
    for guid, _name in entries:
        out += guid.bytes_le
    out += struct.pack('<I', len(texts) | (1 << 16))
    out += texts
    out += struct.pack('<H', len(entries)) + b''.join(_u24(leaf_of_guid[g]) for g in range(len(entries)))
    out += struct.pack('<H', len(records)) + b''.join(_u24(p) for p in parents)
    return bytes(out)


def bank_file_name(bank_path: str) -> str:
    """'bank:/animals/specific/animal_alligator' -> 'sound/fmod_banks/animal_alligator.fmod_bankc'."""
    return BANK_DIR + bank_path.rstrip('/').rsplit('/', 1)[-1] + BANK_EXT


def string_table_from(f: BinaryIO, size: int) -> Optional[StringTable]:
    """String table of a bank, or None if the bank has none (sound banks). Reads the chunk headers and STDT only."""
    top = read_chunks(f, size)
    if top.form != FORM_FEV.decode('latin-1'):
        return None
    stdt = top.find('STDT')
    if stdt is None or stdt.size == 0:
        return None
    return StringTable(chunk_data(f, stdt))


def is_fmod_bank(head: bytes) -> bool:
    return len(head) >= 12 and head[:4] == b'RIFF' and head[8:12] == FORM_FEV


# ---------------------------------------------------------------------------------------------------------
# Text view (Structure tab)
# ---------------------------------------------------------------------------------------------------------

def dump_lines(data: bytes, names_limit: Optional[int] = None) -> List[str]:
    return dump_lines_from(io.BytesIO(data), len(data), names_limit)


def dump_lines_from(f: BinaryIO, size: int, names_limit: Optional[int] = None) -> List[str]:
    """Text of the Structure viewer, from a file object: only the chunk headers and STDT are read."""
    top = read_chunks(f, size)
    lines = []
    vers = format_versions(f, top)
    lines.append('FMOD Studio bank (RIFF {}), {} bytes{}'.format(
        top.form, size, ', format version {} (oldest compatible {})'.format(*vers) if vers else ''))
    lines.append('chunks:')

    def walk(c: Chunk, depth: int):
        for ch in c.children:
            if ch.form:
                empty = ' (empty)' if ch.size <= 4 else ''
                lines.append('{}{:<10} @0x{:08x} {:>11} bytes{}'.format('  ' * depth, ch.label, ch.offset, ch.size, empty))
                walk(ch, depth + 1)
            else:
                lines.append('{}{:<10} @0x{:08x} {:>11} bytes  {}'.format('  ' * depth, ch.cid, ch.offset, ch.size,
                                                                          ch.head.hex(' ')))
    walk(top, 1)
    stdt = top.find('STDT')
    if stdt is not None and stdt.size:
        st = StringTable(chunk_data(f, stdt))
        kinds = ', '.join('{} {}'.format(n, k) for k, n in sorted(st.kinds().items(), key=lambda kv: (-kv[1], kv[0])))
        lines.append('string table (STDT): {} names ({}), {} trie nodes'.format(len(st.names), kinds, st.node_count))
        shown = sorted(st.names, key=lambda gn: gn[1].lower())
        if names_limit is not None:
            shown = shown[:names_limit]
        lines += ['  {{{}}}  {}'.format(g, n) for g, n in shown]
        if len(shown) < len(st.names):
            lines.append('  ... {} more'.format(len(st.names) - len(shown)))
    return lines
