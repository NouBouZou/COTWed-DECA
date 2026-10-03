"""
Fabrique des fichiers ADF de test, types compris, dans plusieurs dispositions (DECA ne sait pas ecrire d'ADF).

Disposition lue par deca.ff_adf.Adf.deserialize (voir aussi adf_minimal.py) :
  0x00  ' FDA', u32 version 4, (u32 nombre, u32 offset) des instances, des types, des StringHash et des noms,
        u32 taille totale, 5 u32 inconnus ; 0x40 commentaire termine par 0.
  noms      : nombre x u8 longueur, puis chaque nom suivi d'un octet nul ;
  types     : metatype, size, alignment, type_hash, u64 index du nom, flags, element_type_hash, element_length,
              puis u32 nombre de membres et membres (structure : u64 nom, type, taille, offset, type par defaut,
              u64 valeur par defaut), ou u32 0 ;
  StringHash: chaine terminee par 0, u64 hash ;
  instances : u32 hash du nom, u32 type, u32 offset, u32 taille, u64 index du nom.

Deux dispositions : "tables_avant" (tables puis donnees, comme adf_minimal) et "donnees_avant" (donnees puis
tables, comme equipment_stats_ui.bin du jeu) : adf_ecriture doit accepter les deux.

Chaine de relocation (comme dans le jeu) : dans chaque champ pointeur (en-tete de chaine ou de tableau), le u32 qui
suit la cible est la distance au champ pointeur suivant, 0 a la fin ; un tableau vide (cible 0) n'en fait pas
partie. chain=False ecrit a la place l'ancienne interpretation de DECA (longueur des chaines, flags des tableaux),
pour verifier qu'adf_ecriture refuse une disposition sans chaine.

xlsbook() fabrique une feuille de reglages au format XLSBook (type 0x0b73315d), tel que le lit
deca.export_import_adf : Sheet{Cols, Rows, CellIndex[], Name}, Cell{Type, AttributeIndex, DataIndex},
StringData[], ValueData[], BoolData[], Attribute{FGColorIndex, BGColorIndex}[], ColorData[]. Les cellules
identiques sont partagees, comme dans les fichiers du jeu.
"""
import struct
from typing import Dict, List, Sequence, Tuple, Union

from deca.ff_adf import typedef_f32, typedef_u8, typedef_u16, typedef_u32
from deca.hashes import hash32_func

STRING = 0x8955583e
XLSBOOK = 0x0b73315d
T_SHEET, T_CELL, T_ATTR, T_META = 0x5EE71001, 0x5EE71002, 0x5EE71003, 0x5EE71004
A_SHEET, A_CELL, A_STRING, A_F32, A_U8, A_ATTR, A_U32 = (0x5EE72000 + i for i in range(7))


class Types:
    def __init__(self):
        self.names: List[str] = []
        self.specs = []

    def name(self, n: str) -> int:
        if n not in self.names:
            self.names.append(n)
        return self.names.index(n)

    def struct(self, name, type_hash, size, align, members):
        self.name(name)
        for m in members:
            self.name(m[0])
        self.specs.append((1, size, align, type_hash, name, 0, members))

    def array(self, name, type_hash, element, size=16, align=8):
        self.name(name)
        self.specs.append((3, size, align, type_hash, name, element, None))

    def blob(self) -> bytes:
        out = b''
        for metatype, size, align, type_hash, name, element, members in self.specs:
            out += struct.pack('<IIII', metatype, size, align, type_hash) + struct.pack('<Q', self.names.index(name))
            out += struct.pack('<III', 0, element, 0)
            if members is None:
                out += struct.pack('<I', 0)
                continue
            out += struct.pack('<I', len(members))
            for mname, mtype, msize, moff in members:
                out += struct.pack('<Q', self.names.index(mname)) + struct.pack('<IIII', mtype, msize, moff, 0)
                out += struct.pack('<Q', 0)
        return out


class Blob:
    """Donnees d'une instance : la structure racine en 0, le reste alloue a la suite."""

    def __init__(self, root_size: int):
        self.b = bytearray(root_size)
        self.ptrs = []                      # champs pointeurs a cible non nulle, pour la chaine

    def pointer(self, pos: int, target: int, second: int = 0):
        """Champ pointeur [u32 cible][u32 lien] ; le lien est ecrit par link() (ou second si pas de chaine)."""
        self.put(pos, '<II', target, second)
        if target:
            self.ptrs.append(pos)

    def link(self):
        """Chaine de relocation : lien = distance au champ pointeur suivant, 0 au dernier."""
        ptrs = sorted(self.ptrs)
        for i, p in enumerate(ptrs):
            self.put(p + 4, '<I', ptrs[i + 1] - p if i + 1 < len(ptrs) else 0)

    def alloc(self, data: bytes, align: int = 4) -> int:
        self.b += b'\0' * ((-len(self.b)) % align)
        pos = len(self.b)
        self.b += data
        return pos

    def put(self, pos: int, fmt: str, *values):
        struct.pack_into(fmt, self.b, pos, *values)


def _align(out: bytearray, n: int):
    out += b'\0' * ((-len(out)) % n)


def build(types: Types, instances: Sequence[Tuple[str, int, bytes]], stringhashes=(), order='tables_avant',
          comment=b'', prefix=b'', trailer=b'') -> bytes:
    """trailer : octets places juste apres les donnees de chaque instance (hors instance), comme le
    04 00 00 00 00 00 qui suit l'instance d'equipment_stats_ui.bin."""
    for name, _t, _d in instances:
        types.name(name)
    names = types.names
    name_table = bytes(len(n) for n in names) + b''.join(n.encode() + b'\0' for n in names)
    typedefs = types.blob()
    sh = b''.join(s + b'\0' + struct.pack('<Q', h) for s, h in stringhashes)

    out = bytearray(b'\0' * 0x40) + comment + b'\0'
    pos = {}

    def tables():
        pos['noms'] = len(out)
        out.extend(name_table)
        _align(out, 4)
        pos['types'] = len(out)
        out.extend(typedefs)
        pos['sh'] = len(out)
        out.extend(sh)
        _align(out, 8)
        pos['instances'] = len(out)
        out.extend(b'\0' * (24 * len(instances)))

    def datas():
        pos['data'] = []
        for _n, _t, d in instances:
            _align(out, 16)
            pos['data'].append(len(out))
            out.extend(d)
            out.extend(trailer)

    if order == 'tables_avant':
        tables()
        datas()
    elif order == 'donnees_avant':
        datas()
        _align(out, 16)
        tables()
    else:
        raise ValueError(order)
    for i, (name, type_hash, d) in enumerate(instances):
        struct.pack_into('<IIII', out, pos['instances'] + 24 * i, int(hash32_func(name.encode())), type_hash,
                         pos['data'][i], len(d))
        struct.pack_into('<Q', out, pos['instances'] + 24 * i + 16, names.index(name))
    struct.pack_into('<4sIIIIIIIIII', out, 0, b' FDA', 4, len(instances), pos['instances'], len(types.specs),
                     pos['types'], len(stringhashes), pos['sh'], len(names), pos['noms'], len(out))
    return bytes(prefix) + bytes(out)


Cell = Union[str, float, bool]


def xlsbook_types() -> Types:
    t = Types()
    t.struct('XLSBook', XLSBOOK, 128, 8, [
        ('Sheet', A_SHEET, 16, 0), ('Cell', A_CELL, 16, 16), ('StringData', A_STRING, 16, 32),
        ('ValueData', A_F32, 16, 48), ('BoolData', A_U8, 16, 64), ('Attribute', A_ATTR, 16, 80),
        ('ColorData', A_U32, 16, 96), ('DateData', A_STRING, 16, 112)])
    t.struct('XLSSheet', T_SHEET, 32, 8, [
        ('Cols', typedef_u32, 4, 0), ('Rows', typedef_u32, 4, 4), ('CellIndex', A_U32, 16, 8), ('Name', STRING, 8, 24)])
    t.struct('XLSCell', T_CELL, 8, 4, [
        ('Type', typedef_u8, 1, 0), ('AttributeIndex', typedef_u16, 2, 2), ('DataIndex', typedef_u32, 4, 4)])
    t.struct('XLSAttribute', T_ATTR, 8, 4, [('FGColorIndex', typedef_u32, 4, 0), ('BGColorIndex', typedef_u32, 4, 4)])
    for name, h, e in (('A[XLSSheet]', A_SHEET, T_SHEET), ('A[XLSCell]', A_CELL, T_CELL), ('A[String]', A_STRING, STRING),
                       ('A[float]', A_F32, typedef_f32), ('A[uint8]', A_U8, typedef_u8),
                       ('A[XLSAttribute]', A_ATTR, T_ATTR), ('A[uint32]', A_U32, typedef_u32)):
        t.array(name, h, e)
    return t


def xlsbook_data(sheets: Sequence[Tuple[str, Sequence[Sequence[Cell]]]], chain: bool = True) -> bytes:
    """Donnees d'instance XLSBook, avec la chaine de relocation (chain=False : longueur des chaines et flags 1
    a la place des liens, l'ancienne lecture de DECA)."""
    strings: List[bytes] = []
    values: List[float] = []
    bools: List[int] = []
    cells: List[Tuple[int, int, int]] = []
    sheet_cells = []

    def idx(lst, v):
        if v not in lst:
            lst.append(v)
        return lst.index(v)

    for name, rows in sheets:
        idx(strings, name.encode())
        ci = []
        for row in rows:
            for v in row:
                if isinstance(v, bool):
                    c = (0, idx(bools, int(v)), 2)
                elif isinstance(v, str):
                    c = (1, idx(strings, v.encode()), 0)
                else:
                    c = (2, idx(values, float(v)), 1)
                ci.append(idx(cells, c))
        sheet_cells.append((name, len(rows[0]) if rows else 0, len(rows), ci))

    b = Blob(128)

    def second_of(text: bytes) -> int:
        return 0 if chain else len(text)

    flags = 0 if chain else 1

    text_pos = {}

    def text(s: bytes) -> int:
        if s not in text_pos:
            text_pos[s] = b.alloc(s + b'\0', 1)
        return text_pos[s]

    def array(root_pos, data: bytes, count: int, align=8):          # donnees alignees sur 8, comme dans le jeu
        off = b.alloc(data, align) if count else 0
        b.pointer(root_pos, off, flags)
        b.put(root_pos + 8, '<II', count, 0)

    sheet_pos = b.alloc(b'\0' * 32 * len(sheet_cells), 8)
    b.pointer(0, sheet_pos, flags)
    b.put(8, '<II', len(sheet_cells), 0)
    for i, (name, cols, nrows, ci) in enumerate(sheet_cells):
        p = sheet_pos + 32 * i
        b.put(p, '<II', cols, nrows)
        array(p + 8, b''.join(struct.pack('<I', x) for x in ci), len(ci))
        b.pointer(p + 24, text(name.encode()), second_of(name.encode()))
    array(16, b''.join(struct.pack('<BxHI', t, a, d) for t, d, a in cells), len(cells))
    headers_pos = b.alloc(b'\0' * 8 * len(strings), 8)
    b.pointer(32, headers_pos, flags)
    b.put(40, '<II', len(strings), 0)
    for i, s in enumerate(strings):
        b.pointer(headers_pos + 8 * i, text(s), second_of(s))
    array(48, b''.join(struct.pack('<f', v) for v in values), len(values))
    array(64, bytes(bools), len(bools))
    array(80, struct.pack('<II', 1, 2) * 3, 3)
    array(96, struct.pack('<II', 0x000000, 0xFFFFFF), 2)
    array(112, b'', 0)                      # tableau vide (cible 0), hors chaine, comme DateData dans le jeu
    if chain:
        b.link()
    return bytes(b.b)


def xlsbook(sheets, order: str = 'tables_avant', extra_instance: bool = False, prefix: bytes = b'',
            chain: bool = True, trailer: bytes = b'') -> bytes:
    """Fichier ADF XLSBook complet ; extra_instance ajoute une seconde instance (structure Meta) placee apres."""
    t = xlsbook_types()
    instances = [('stats', XLSBOOK, xlsbook_data(sheets, chain))]
    if extra_instance:
        t.struct('Meta', T_META, 16, 8, [('Version', typedef_u32, 4, 0), ('Label', STRING, 8, 8)])
        m = Blob(16)
        lab = m.alloc(b'meta_label\0', 1)
        m.put(0, '<I', 7)
        m.pointer(8, lab, 0 if chain else 10)
        instances.append(('meta', T_META, bytes(m.b)))
    return build(t, instances, stringhashes=[(b'equipment_ammo', 0x1122334455667788)], order=order,
                 comment=b'cotw test', prefix=prefix, trailer=trailer)


STATS_SHEETS = [
    ('ammo', [
        ['name', 'class', 'penetration', 'expansion', 'note'],
        ['equipment_ammo_243_sp_01', '2-6', 0.25, 0.75, 'sp'],
        ['equipment_ammo_243_pt_01', '2-6', 0.5, 0.5, 'pt'],
        ['equipment_ammo_762x54r_fmj_01', '3-7', 0.5, 0.5, 'fmj'],
    ]),
    ('weapons', [
        ['name', 'zoom', 'enabled'],
        ['equipment_weapon_ba_rifle_243_01', 1.5, True],
    ]),
]

# Comme la feuille ammo du jeu : deux lignes vides apres les objets (case vide = booleen True, Type 0)
STATS_SHEETS_VIDES = [
    ('ammo', STATS_SHEETS[0][1] + [[True] * 5, [True] * 5]),
    STATS_SHEETS[1],
]
