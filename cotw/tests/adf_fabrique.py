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

Trois dispositions : "tables_avant" (tables puis donnees, comme adf_minimal), "donnees_avant" (donnees puis
tables, comme equipment_stats_ui.bin du jeu) : adf_ecriture doit accepter les deux ; "noms_a_la_fin" (donnees,
instances, types, StringHash, noms : la disposition des .ammotunec du jeu, dont on renomme l'instance).

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
        self.specs.append((1, size, align, type_hash, name, 0, members, 0))

    def array(self, name, type_hash, element, size=16, align=8):
        self.name(name)
        self.specs.append((3, size, align, type_hash, name, element, None, 0))

    def inline_array(self, name, type_hash, element, length, element_size, align=4):
        """Tableau de taille fixe range dans la structure (metatype 4) : DECA lit element_length elements
        a la position du membre, sans en-tete (EquipmentOwned[1024] des sauvegardes)."""
        self.name(name)
        self.specs.append((4, length * element_size, align, type_hash, name, element, None, length))

    def stringhash(self, name, type_hash, size=4):
        self.name(name)
        self.specs.append((9, size, size, type_hash, name, 0x48c5294d, None, 0))

    def blob(self) -> bytes:
        out = b''
        for metatype, size, align, type_hash, name, element, members, length in self.specs:
            out += struct.pack('<IIII', metatype, size, align, type_hash) + struct.pack('<Q', self.names.index(name))
            out += struct.pack('<III', 0, element, length)
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
    elif order == 'noms_a_la_fin':
        datas()
        _align(out, 8)
        pos['instances'] = len(out)
        out.extend(b'\0' * (24 * len(instances)))
        pos['types'] = len(out)
        out.extend(typedefs)
        pos['sh'] = len(out)
        out.extend(sh)
        pos['noms'] = len(out)
        out.extend(name_table)
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


# --- tables de traduction (.stringlookup), types et hash releves dans string_ids.stringlookup du jeu ---
T_SL, T_LANG, T_PAIR, T_LINE, T_SUB, T_SH4 = 0xc7254276, 0xdd2b5629, 0xdee51fbe, 0x1912f7ff, 0xbb6ad12b, 0xc03f64bf
A_LANG, A_PAIR, A_LINE, A_SUB, A_S8 = 0xdd5e6bf8, 0xaa4302de, 0xef187b6f, 0x56aaaf0d, 0x00a1294f


def stringlookup_types() -> Types:
    from deca.ff_adf import typedef_f32, typedef_s8
    t = Types()
    t.struct('StringLookup', T_SL, 32, 8, [('Languages', A_LANG, 16, 0), ('Text', A_S8, 16, 16)])
    t.array('A[LanguageStringLookup]', A_LANG, T_LANG)
    t.struct('LanguageStringLookup', T_LANG, 32, 8, [('SortedPairs', A_PAIR, 16, 0), ('SortedDialogueLines', A_LINE, 16, 16)])
    t.array('A[HashPropertiesPair]', A_PAIR, T_PAIR)
    t.struct('HashPropertiesPair', T_PAIR, 8, 4, [('Hash', typedef_u32, 4, 0), ('TextOffset', typedef_u32, 4, 4)])
    t.array('A[DialogueLine]', A_LINE, T_LINE)
    t.struct('DialogueLine', T_LINE, 40, 8, [
        ('Hash', typedef_u32, 4, 0), ('NameOffset', typedef_u32, 4, 4), ('Subtitles', A_SUB, 16, 8),
        ('FMODEvent', typedef_u32, 4, 24), ('IncomingCall', T_SH4, 4, 28), ('CharacterName', T_SH4, 4, 32),
        ('Flags', typedef_u32, 4, 36)])
    t.array('A[Subtitle]', A_SUB, T_SUB)
    t.struct('Subtitle', T_SUB, 12, 4, [('LineHash', typedef_u32, 4, 0), ('Start', typedef_f32, 4, 4),
                                        ('Duration', typedef_f32, 4, 8)])
    t.stringhash('StringHash_48c5294d_4', T_SH4)
    t.array('A[int8]', A_S8, typedef_s8)
    return t


def stringlookup(entries: Dict[str, Sequence[str]], trailer: bytes = b'\x04\0\0\0', text_last: bool = True) -> bytes:
    """Table de traduction comme string_ids.stringlookup : entries = {cle: [texte par langue]} ; paires triees par
    hash32 de la cle, une ligne de dialogue par cle (sous-titre propre), bloc Text a la fin de l'instance
    (text_last=False : au debut des donnees, pour le cas ou il faut le recopier pour l'allonger)."""
    n_lang = len(next(iter(entries.values())))
    keys = sorted(entries, key=lambda k: int(hash32_func(k.encode())))
    text = bytearray()
    offs: Dict[str, int] = {}

    def put(s: str) -> int:
        if s not in offs:
            offs[s] = len(text)
            text.extend(s.encode('utf-8') + b'\0')
        return offs[s]

    for k in keys:
        put(k)
        for s in entries[k]:
            put(s)
    b = Blob(32)
    text_pos = None if text_last else b.alloc(bytes(text), 8)
    langs = b.alloc(b'\0' * 32 * n_lang, 8)
    b.pointer(0, langs)
    b.put(8, '<II', n_lang, 0)
    for li in range(n_lang):
        lp = langs + 32 * li
        pairs = b.alloc(b''.join(struct.pack('<II', int(hash32_func(k.encode())), offs[entries[k][li]]) for k in keys), 8)
        b.pointer(lp, pairs)
        b.put(lp + 8, '<II', len(keys), 0)
        lines = b.alloc(b'\0' * 40 * len(keys), 8)
        b.pointer(lp + 16, lines)
        b.put(lp + 24, '<II', len(keys), 0)
        for i, k in enumerate(keys):
            h = int(hash32_func(k.encode()))
            sub = b.alloc(struct.pack('<Iff', h, 0.0, -1.0), 8)
            p = lines + 40 * i
            b.put(p, '<II', h, offs[k])
            b.pointer(p + 8, sub)
            b.put(p + 16, '<II', 1, 0)
            b.put(p + 24, '<IIII', 1, 0, 0xdeadbeef, 1219670572)
    if text_last:
        text_pos = b.alloc(bytes(text), 8)           # le bloc Text finit l'instance, comme dans le jeu
    b.pointer(16, text_pos)
    b.put(24, '<II', len(text), 0)
    b.link()
    return build(stringlookup_types(), [('stringlookup', T_SL, bytes(b.b))], order='donnees_avant',
                 comment=b'StringLookup.adf in THP ', trailer=trailer)


LOOKUP_ENTRIES = {
    'equipment_ammo_243_sp_name': ['.243 SOFT-POINT BULLET', 'BALLES À TÊTE MOLLE .243', 'TMG .243 WINCHESTER'],
    'equipment_ammo_243_sp_description': ['Soft-point bullets.', 'Balles à tête molle.', 'Teilmantel.'],
    'equipment_ammo_270_sp_name': ['.270 SOFT-POINT', 'BALLES À TÊTE MOLLE .270', 'TMG .270'],
    'ui_menu_store': ['STORE', 'MAGASIN', 'LADEN'],
}

# les 10 langues du jeu (stringlookup.LANGUES), pour les outils qui attendent un texte par langue du jeu
LANGUES_JEU = ['en', 'fr', 'de', 'es', 'ru', 'pl', 'ja', 'pt', 'cs', 'zh']
LOOKUP_ENTRIES_10 = {k: [v[i] if i < len(v) else '{} ({})'.format(v[0], code) for i, code in enumerate(LANGUES_JEU)]
                     for k, v in LOOKUP_ENTRIES.items()}


# --- reglage balistique (.ammotunec), disposition du jeu : donnees, instances, types, StringHash, noms ---
# Types, hash, tailles et offsets releves dans equipment_ammo_243_sp_01.ammotunec du jeu (2026-10-06).
T_TUNE, A_CLASS, T_CLASS, T_SH_TUNE = 0x5bb59511, 0x4bcce369, 0xf18aface, 0xc03f64bf
T_TRACER, T_TRACER_COLOR, T_PICKUP = 0x3792cf6b, 0x0accbe7a, 0x44a9307e
# valeurs de la .243 SP, a l'octet pres : (membre, offset, format struct, valeur)
TUNE_243_SP = [('max_range', 16, '<f', 1000.0), ('diameter', 20, '<f', 0.0062), ('length', 24, '<f', 0.017),
               ('drag_coefficient', 28, '<f', 0.27979), ('kinetic_energy', 32, '<f', 2618.0), ('mass', 36, '<f', 0.00616),
               ('projectile_penetration', 40, '<f', 0.106935985), ('projectile_damage', 44, '<f', 0.00158776145),
               ('projectile_expansion_rate', 48, '<f', 0.19658), ('projectile_contraction_rate', 52, '<f', 0.02184222),
               ('projectile_max_expansion', 56, '<f', 0.00294869975), ('projectiles_per_shot', 60, '<B', 1),
               ('projectile_entity_spawn_tag', 64, '<I', 0xdeadbeef), ('projectile_model_path', 68, '<I', 0xdeadbeef),
               ('broken_projectile_model_path', 72, '<I', 0xdeadbeef), ('scatter_multiplier', 76, '<f', 1.0),
               ('velocity_deviance', 80, '<f', 0.0)]
TRACER_243_SP = [('enabled', 0, '<B', 0), ('flash_freq', 4, '<f', 0.1), ('radius', 8, '<f', 0.3),
                 ('tracer_scale_factor', 12, '<f', 0.001), ('position_offset', 16, '<f', 0.0)]


def ammotune(instance_name: str, levels=(2, 3, 4, 5, 6), mass: float = 0.00616) -> bytes:
    """Reglage balistique (AmmunitionTuning, 128 octets) avec les types et les valeurs de la .243 SP ; instance
    nommee comme dans le jeu (nom du fichier source tronque a 31 caracteres) ; une chaine StringHash."""
    from deca.ff_adf import typedef_f32 as F32, typedef_u8 as U8
    t = Types()
    t.struct('AmmunitionTuning', T_TUNE, 128, 8,
             [('ammunition_class', A_CLASS, 8, 0)]
             + [(n, {'<f': F32, '<B': U8, '<I': T_SH_TUNE}[f], 4 if f != '<B' else 1, o) for n, o, f, _v in TUNE_243_SP]
             + [('tracer_settings', T_TRACER, 4, 84), ('pickup_settings', T_PICKUP, 4, 116)])
    t.array('A[AmmunitionClass]', A_CLASS, T_CLASS)
    t.struct('AmmunitionClass', T_CLASS, 1, 1, [('level', U8, 1, 0)])
    t.stringhash('StringHash_48c5294d_4', T_SH_TUNE)
    t.struct('Tracer', T_TRACER, 32, 4, [(n, {'<f': F32, '<B': U8}[f], 4, o) for n, o, f, _v in TRACER_243_SP]
             + [('color', T_TRACER_COLOR, 4, 20)])
    t.struct('TracerColor', T_TRACER_COLOR, 12, 4, [('red', F32, 4, 0), ('green', F32, 4, 4), ('blue', F32, 4, 8)])
    t.struct('PickupData', T_PICKUP, 8, 4, [('collision_path', T_SH_TUNE, 4, 0), ('never_break', U8, 1, 4),
                                            ('dog_requires_skill', U8, 1, 5)])
    b = Blob(128)
    for name, off, fmt, value in TUNE_243_SP:
        b.put(off, fmt, mass if name == 'mass' else value)
    for _n, off, fmt, value in TRACER_243_SP:
        b.put(84 + off, fmt, value)
    b.put(84 + 20, '<fff', 0.0, 1.0, 0.0)
    b.put(116, '<IBB', 0xdeadbeef, 0, 1)
    if levels:
        arr = b.alloc(bytes(levels), 8)
        b.pointer(0, arr)
        b.put(8, '<II', len(levels), 0)
    # sans classes (clay_pigeon, 22_hp... dans le jeu) : tableau vide de cible 0, instance sans chaine de relocation
    b.link()
    return build(t, [(instance_name, T_TUNE, bytes(b.b))], stringhashes=[(b'', 0xdeadbeef)], order='noms_a_la_fin',
                 comment=b'AmmunitionTuningSettings_adf.adf in THP')


# ---------------------------------------------------------------------------
# Sauvegarde du profil (journal 2026-10-03) : PlayerProfileData_22, puis conteneur SAVE / COMP / zlib
# ---------------------------------------------------------------------------
# Releves sur les sauvegardes reelles : EquipmentOwned, EquipmentAmount (en parallele), EquipmentBackPack et
# EquipmentUnlocked sont des tableaux fixes de 1 024 u32 ; InventorySlot = 16 x PPSlot_1 { Slots : A[u32] } ;
# LoadoutInfo.LoadoutData[].LoadoutEquipmentSlot, idem ; WeaponData = 1 024 x PPWeaponData_4.
# INVENTE pour le test (non releve) : les hash des types, l'ordre et les offsets des membres, le contenu de
# PPWeaponData_4 (WeaponHash, KillCount) et le membre LoadoutName.
T_PROFILE, T_SLOT, T_LOADOUT_INFO, T_LOADOUT, T_WEAPON = (0x5AE00001 + i for i in range(5))
IA_EQUIP, IA_SLOTS, IA_WEAPONS, A_LOADOUT = (0x5AE01001 + i for i in range(4))
PROFILE_SLOTS = 16


def profile_types(n_equipment: int, n_weapons: int) -> Tuple[Types, Dict[str, int], int]:
    """Types du profil ; renvoie aussi l'offset de chaque membre et la taille de la structure."""
    t = Types()
    off = {}
    pos = 0
    members = []
    for name, type_hash, size in (('EquipmentOwned', IA_EQUIP, 4 * n_equipment),
                                  ('EquipmentAmount', IA_EQUIP, 4 * n_equipment),
                                  ('EquipmentBackPack', IA_EQUIP, 4 * n_equipment),
                                  ('EquipmentUnlocked', IA_EQUIP, 4 * n_equipment),
                                  ('InventorySlot', IA_SLOTS, 16 * PROFILE_SLOTS),
                                  ('LoadoutInfo', T_LOADOUT_INFO, 16),
                                  ('WeaponData', IA_WEAPONS, 8 * n_weapons)):
        off[name] = pos
        members.append((name, type_hash, size, pos))
        pos += size
    t.struct('PlayerProfileData_22', T_PROFILE, pos, 8, members)
    t.inline_array('equipment[]', IA_EQUIP, typedef_u32, n_equipment, 4)
    t.struct('PPSlot_1', T_SLOT, 16, 8, [('Slots', A_U32, 16, 0)])
    t.array('A[uint32]', A_U32, typedef_u32)
    t.inline_array('PPSlot_1[16]', IA_SLOTS, T_SLOT, PROFILE_SLOTS, 16, 8)
    t.struct('PPLoadoutInfo', T_LOADOUT_INFO, 16, 8, [('LoadoutData', A_LOADOUT, 16, 0)])
    t.array('A[PPLoadoutData_2]', A_LOADOUT, T_LOADOUT)
    t.struct('PPLoadoutData_2', T_LOADOUT, 8 + 16 * PROFILE_SLOTS, 8,
             [('LoadoutName', STRING, 8, 0), ('LoadoutEquipmentSlot', IA_SLOTS, 16 * PROFILE_SLOTS, 8)])
    t.struct('PPWeaponData_4', T_WEAPON, 8, 4, [('WeaponHash', typedef_u32, 4, 0), ('KillCount', typedef_u32, 4, 4)])
    t.inline_array('PPWeaponData_4[]', IA_WEAPONS, T_WEAPON, n_weapons, 8)
    return t, off, pos


def profile(owned: Sequence[Tuple[int, int]], backpack: Sequence[int] = (), unlocked: Sequence[int] = (),
            slots: Dict[int, Sequence[int]] = None, loadouts: Sequence[Tuple[str, Dict[int, Sequence[int]]]] = (),
            weapons: Sequence[Tuple[int, int]] = (), n_equipment: int = 1024, n_weapons: int = 1024,
            instance: str = 'thp_player_profile_adf') -> bytes:
    """Charge utile d'une sauvegarde de profil : prefixe 01 01 00 00 00 puis l'ADF (types dans le fichier).
    owned : (hash, quantite) ranges en tete d'EquipmentOwned / EquipmentAmount ; slots : emplacement (0..15) ->
    hashes ; loadouts : (nom, emplacements) ; weapons : (hash de l'arme, compteur)."""
    t, off, size = profile_types(n_equipment, n_weapons)
    b = Blob(size)

    def u32s(pos, values):
        for i, v in enumerate(values):
            b.put(pos + 4 * i, '<I', v)

    u32s(off['EquipmentOwned'], [h for h, _n in owned])
    u32s(off['EquipmentAmount'], [n for _h, n in owned])
    u32s(off['EquipmentBackPack'], backpack)
    u32s(off['EquipmentUnlocked'], unlocked)

    def slot_array(pos, contents: Dict[int, Sequence[int]]):
        for s, hashes in (contents or {}).items():
            p = pos + 16 * s
            data = b''.join(struct.pack('<I', h) for h in hashes)
            b.pointer(p, b.alloc(data, 8) if hashes else 0)
            b.put(p + 8, '<II', len(hashes), 0)

    slot_array(off['InventorySlot'], slots)
    if loadouts:
        stride = 8 + 16 * PROFILE_SLOTS
        lpos = b.alloc(b'\0' * stride * len(loadouts), 8)
        b.pointer(off['LoadoutInfo'], lpos)
        b.put(off['LoadoutInfo'] + 8, '<II', len(loadouts), 0)
        for i, (name, contents) in enumerate(loadouts):
            p = lpos + stride * i
            b.pointer(p, b.alloc(name.encode() + b'\0', 1))
            slot_array(p + 8, contents)
    for i, (h, kills) in enumerate(weapons):
        b.put(off['WeaponData'] + 8 * i, '<II', h, kills)
    b.link()
    return build(t, [(instance, T_PROFILE, bytes(b.b))], prefix=b'\x01\x01\x00\x00\x00',
                 comment=b'savegamedata_spec_imm.adf in THP')


def save_container(payload: bytes, level: int = 1, save_size=None) -> bytes:
    """Fichier de sauvegarde : 'SAVE', u32 0x0032d3c2, u64 ; 'COMP', u32 0x0101, u64 taille decompressee ; zlib.
    Le u64 de SAVE n'est pas interprete par le lecteur (save_size : valeur ecrite, defaut taille decompressee)."""
    import zlib
    size = len(payload) if save_size is None else save_size
    return (b'SAVE' + struct.pack('<IQ', 0x0032d3c2, size) + b'COMP' + struct.pack('<IQ', 0x0101, len(payload))
            + zlib.compress(payload, level))
