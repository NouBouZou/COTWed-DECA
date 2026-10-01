"""
Recherche d'un terme (nom d'objet, chemin) a l'interieur d'un fichier RTPC ou ADF deja decode, avec le
chemin du champ qui le contient. Partage par adf_dump.py et comparer_objets.py.

Un terme est cherche en texte et sous ses trois hashes DECA : hash32 (Jenkins), hash48 et hash64
(murmur3). Un chemin est cherche avec '/' et avec '\\'.
"""
from dataclasses import dataclass
from typing import Iterator, List, Optional, Tuple

M32 = 0xFFFFFFFF
M48 = 0xFFFFFFFFFFFF
M64 = 0xFFFFFFFFFFFFFFFF


@dataclass
class Pattern:
    term: str
    texts: List[bytes]
    h32: set
    h48: set
    h64: set


def pattern(term: str) -> Pattern:
    import mmh3
    from deca.hashes import hash32_func_bytes
    forms = [term.encode('utf-8')]
    if '/' in term or '\\' in term:
        forms = list({term.replace('\\', '/').encode('utf-8'), term.replace('/', '\\').encode('utf-8')})
    h32, h48, h64 = set(), set(), set()
    for b in forms:
        h32.add(int(hash32_func_bytes(b)) & M32)
        v = mmh3.hash128(b, x64arch=True)
        h48.add((v >> 16) & M48)
        h64.add(v & M64)
    return Pattern(term, forms, h32, h48, h64)


def match_int(value: int, pat: Pattern, width: Optional[int] = None) -> Optional[str]:
    """Encodage sous lequel un entier vaut un hash du terme (None sinon). width : largeur connue (StringHash)."""
    if not isinstance(value, int) or isinstance(value, bool):
        return None
    v = value & M64
    if width in (None, 32) and v <= M32 and v in pat.h32:
        return 'hash32'
    if width in (None, 64) and v in pat.h64:
        return 'hash64'
    if width in (None, 48, 64) and (v & M48) in pat.h48:
        return 'hash48'
    return None


def match_decimal(value, pat: Pattern) -> bool:
    """Chaine de chiffres egale au hash32 du terme ("3656775585")."""
    if isinstance(value, str):
        value = value.encode('utf-8')
    if not isinstance(value, (bytes, bytearray)) or not 1 <= len(value) <= 10 or not bytes(value).isdigit():
        return False
    return int(value) in pat.h32


def match_text(value, pat: Pattern) -> bool:
    if isinstance(value, str):
        value = value.encode('utf-8')
    if not isinstance(value, (bytes, bytearray)):
        return False
    value = bytes(value)
    return any(t in value for t in pat.texts)


# ---------------------------------------------------------------------------------------------------------
# RTPC
# ---------------------------------------------------------------------------------------------------------

def find_in_rtpc(tree, pat: Pattern) -> List[Tuple[str, str]]:
    """[(chemin, comment)] : hash du noeud, nom ou valeur d'une propriete, element de tableau, chaine."""
    from deca.ff_rtpc import k_type_str, k_type_u32, k_type_array_u32, k_type_objid, k_type_event
    out = []

    def walk(node, path):
        if match_int(node.name_hash, pat, 32):
            out.append((path, 'hash32 du noeud'))
        for p in node.prop_table:
            where = '{} propriete 0x{:08x}'.format(path, p.name_hash & M32)
            if match_int(p.name_hash, pat, 32):
                out.append((where, 'hash32 du nom de la propriete'))
            if p.type == k_type_u32 and match_int(p.data, pat, 32):
                out.append((where, 'hash32 en valeur u32'))
            elif p.type == k_type_array_u32:
                for i, v in enumerate(p.data):
                    if match_int(v, pat, 32):
                        out.append(('{}[{}]'.format(where, i), 'hash32 dans un tableau u32'))
            elif p.type == k_type_objid and match_int(p.data, pat, 64):
                out.append((where, '{} en objid'.format(match_int(p.data, pat, 64))))
            elif p.type == k_type_event:
                for i, v in enumerate(p.data):
                    enc = match_int(v, pat, 64)
                    if enc:
                        out.append(('{}[{}]'.format(where, i), '{} dans un event'.format(enc)))
            elif p.type == k_type_str:
                if match_text(p.data, pat):
                    out.append((where, 'texte'))
                elif match_decimal(p.data, pat):
                    out.append((where, 'hash32 ecrit en chiffres'))
        for i, c in enumerate(node.child_table):
            walk(c, '{}.{}'.format(path, i))

    walk(tree.root_node, '0')
    return out


# ---------------------------------------------------------------------------------------------------------
# ADF
# ---------------------------------------------------------------------------------------------------------

def _prim_sizes():
    from deca import ff_adf as A
    return {A.typedef_u8: 1, A.typedef_s8: 1, A.typedef_u16: 2, A.typedef_s16: 2, A.typedef_u32: 4,
            A.typedef_s32: 4, A.typedef_f32: 4, A.typedef_u64: 8, A.typedef_s64: 8, A.typedef_f64: 8}


STRING_TYPE = 0x8955583e
DEFERRED_TYPE = 0xdefe88ed


@dataclass
class Leaf:
    path: str
    value: object
    offset: Optional[int]
    kind: str                 # nombre, chaine, octets, hash32, hash48, hash64, enum
    note: Optional[str]       # chaine de la table du fichier (StringHash) ou nom d'enum
    ancestors: list           # [(chemin, AdfValue)] des structures englobantes, de la racine vers la feuille
    header: Optional[int] = None  # chaine : offset de son en-tete (u32 offset relatif a l'instance, u32 longueur)


def adf_leaves(adf) -> Iterator[Leaf]:
    """Toutes les valeurs terminales des instances, avec leur chemin et leur offset dans le fichier."""
    from deca.ff_adf import AdfValue, MetaType
    type_map = adf.extended_map_typedef
    sizes = _prim_sizes()

    def walk(v, path, ancestors):
        if isinstance(v, AdfValue):
            td = type_map.get(v.type_id)
            mt = td.metatype if td is not None else None
            if v.type_id == DEFERRED_TYPE:
                yield from walk(v.value, path, ancestors)
            elif isinstance(v.value, dict):
                chain = ancestors + [(path, v)]
                for k, iv in v.value.items():
                    yield from walk(iv, '{}.{}'.format(path, k), chain)
            elif mt in (MetaType.Array, MetaType.InlineArray):
                elems = v.value
                if isinstance(elems, (bytes, bytearray)):
                    yield Leaf(path, bytes(elems), v.data_offset, 'octets', None, ancestors)
                elif elems is not None and len(elems) and not isinstance(elems[0], AdfValue):
                    stride = sizes.get(td.element_type_hash) or (type_map[td.element_type_hash].size
                                                                  if td.element_type_hash in type_map else 0)
                    for i, x in enumerate(elems):
                        yield Leaf('{}[{}]'.format(path, i), x, v.data_offset + i * stride if stride else None,
                                   'nombre', None, ancestors)
                else:
                    for i, iv in enumerate(elems or []):
                        yield from walk(iv, '{}[{}]'.format(path, i), ancestors)
            elif mt == MetaType.StringHash:
                width = {4: 32, 6: 48, 8: 64}.get(td.size)
                note = v.hash_string.decode('utf-8', 'replace') if isinstance(v.hash_string, bytes) else v.hash_string
                yield Leaf(path, v.value, v.data_offset, 'hash{}'.format(width) if width else 'hash', note, ancestors)
            elif v.type_id == STRING_TYPE:
                yield Leaf(path, v.value, v.data_offset, 'chaine', None, ancestors, v.info_offset)
            elif mt == MetaType.Enumeration:
                note = v.enum_string.decode('utf-8', 'replace') if isinstance(v.enum_string, bytes) else v.enum_string
                yield Leaf(path, v.value, v.data_offset, 'enum', note, ancestors)
            else:
                yield Leaf(path, v.value, v.data_offset, 'nombre', None, ancestors)
        elif isinstance(v, list):
            for i, iv in enumerate(v):
                yield from walk(iv, '{}[{}]'.format(path, i), ancestors)
        # pointeurs (tuples) et valeurs absentes : ignores

    for ins, fv in zip(adf.table_instance, adf.table_instance_full_values):
        name = ins.name.decode('utf-8', 'replace') if isinstance(ins.name, bytes) else str(ins.name)
        yield from walk(fv, name, [])


def find_in_adf(adf, pat: Pattern) -> List[Tuple[Leaf, str]]:
    out = []
    for leaf in adf_leaves(adf):
        if leaf.kind in ('chaine', 'octets'):
            if match_text(leaf.value, pat):
                out.append((leaf, 'texte'))
            elif match_decimal(leaf.value, pat):
                out.append((leaf, 'hash32 ecrit en chiffres'))
        elif leaf.kind.startswith('hash'):
            width = int(leaf.kind[4:]) if leaf.kind[4:].isdigit() else None
            enc = match_int(leaf.value, pat, width)
            if enc or (leaf.note and match_text(leaf.note, pat)):
                out.append((leaf, enc or 'texte (table de chaines du fichier)'))
        else:
            enc = match_int(leaf.value, pat)
            if enc:
                out.append((leaf, enc))
    return out


def row_of(leaf: Leaf) -> Optional[Tuple[str, object]]:
    """Structure englobante la plus proche qui est un element de tableau (une "ligne"), sinon la plus proche."""
    for path, value in reversed(leaf.ancestors):
        if path.endswith(']'):
            return path, value
    return leaf.ancestors[-1] if leaf.ancestors else None


def find(kind: str, obj, term: str) -> List[str]:
    """Citations du terme dans un fichier decode ('rtpc' ou 'adf'), sous forme 'chemin (comment)'."""
    pat = pattern(term)
    if kind == 'rtpc':
        return ['{} ({})'.format(p, how) for p, how in find_in_rtpc(obj, pat)]
    if kind == 'adf':
        return ['{} ({})'.format(leaf.path, how) for leaf, how in find_in_adf(obj, pat)]
    return []
