"""
Serialisation RTPC pour DECA.

Ce module ecrit un arbre RTPC tel que le produit ``deca.ff_rtpc.rtpc_from_binary`` (classes ``Rtpc``,
``RtpcNode``, ``RtpcProperty``), et fournit des aides pour modifier cet arbre (ajout de noeuds et de
proprietes) avant de le reecrire.

Format (tel que lu par ``deca.ff_rtpc``)
---------------------------------------
    0x00  'RTPC'                      magie
    0x04  u32 version
    0x08  en-tete du noeud racine     u32 name_hash, u32 data_offset, u16 prop_count, u16 child_count
    0x14  bloc du noeud racine

Bloc d'un noeud, a l'adresse ``data_offset`` :
    prop_count  x 9 octets   en-tetes de proprietes : u32 name_hash, u32 donnee, u8 type
    bourrage jusqu'a un multiple de 4
    child_count x 12 octets  en-tetes des noeuds enfants
    [version 2 uniquement] u32 : nombre de proprietes de type different de 'none' (0)
    valeurs des proprietes non inline de CE noeud, dans l'ordre des proprietes
    bourrage jusqu'a un multiple de 4
    blocs des enfants, dans l'ordre, recursivement (parcours en profondeur)

Pour les types inline (none, u32, f32, 15, 16), le champ "donnee" contient la valeur. Pour les autres,
il contient l'offset absolu de la valeur dans le fichier.

Regles de disposition
---------------------
Elles viennent de l'ecrivain RTPC d'apex-tools (EonZeNx, C#, ``ApexFormat.RTPC.V03``), annonce comme
reproduisant a l'octet pres les RTPC de Just Cause 3 : bourrage 0x50 ('P'), chaines non alignees et
dedupliquees sur tout le fichier, alignement 16 pour vec4/mat4x4, 4 pour les autres valeurs.
Rien ne garantit que theHunter: COTW suive exactement les memes regles : elles sont regroupees dans
``RtpcLayout`` pour pouvoir etre ajustees d'apres l'outil d'aller-retour ``cotw/rtpc_roundtrip.py``.
Le jeu lit les valeurs par offset : une disposition differente mais coherente (alignements respectes)
reste lisible. L'egalite a l'octet pres sert a prouver que l'ecrivain reproduit celui du jeu.

Resultat sur theHunter: COTW (2026-09-29, 6 348 RTPC uniques) : les 5 989 fichiers de version 1 sont
reecrits a l'identique avec ces regles, aucun arbre n'est altere. Les 359 fichiers de version 2 ont
4 octets de plus que la reecriture v1 juste apres les en-tetes des enfants de chaque noeud (le lecteur
de DECA les saute). apex-tools lit un u32 au meme endroit dans ses RTPC "flat"
(``RtpcV03Container.ValidProperties``) et l'ecrit comme le nombre de proprietes dont le type n'est pas
'none' : c'est la regle appliquee ici a la version 2, A CONFIRMER par ``cotw/rtpc_roundtrip.py``, qui
compare aussi la valeur lue a cette regle pour chaque noeud.

Dans les originaux, les enfants de 100 % des noeuds sont tries par hash croissant (86 % pour les
proprietes) : les aides d'insertion conservent cet ordre.
"""
import struct
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Dict, List, Mapping, Optional, Tuple, Union

from deca.file import ArchiveFile
from deca.hashes import hash32_func
from deca.ff_rtpc import (
    Rtpc, RtpcNode, RtpcProperty, rtpc_from_binary,
    k_type_none, k_type_u32, k_type_f32, k_type_str, k_type_vec2, k_type_vec3, k_type_vec4,
    k_type_mat3x3, k_type_mat4x4, k_type_array_u32, k_type_array_f32, k_type_array_u8,
    k_type_depreciated_12, k_type_objid, k_type_event, k_type_unk_15, k_type_unk_16,
)

RTPC_MAGIC = b'RTPC'
FILE_HEADER_SIZE = 8        # magie + version
NODE_HEADER_SIZE = 12       # name_hash, data_offset, prop_count, child_count
PROP_HEADER_SIZE = 9        # name_hash, donnee, type

INLINE_TYPES = frozenset({k_type_none, k_type_u32, k_type_f32, k_type_unk_15, k_type_unk_16})

# Nombre d'elements des types a taille fixe (flottants 32 bits).
FIXED_F32_COUNT = {k_type_vec2: 2, k_type_vec3: 3, k_type_vec4: 4, k_type_mat3x3: 9, k_type_mat4x4: 16}

# Alignements d'apex-tools (ApexFormat.RTPC.V03.Models.Properties.Variants.*.Alignment) ; 1 = aucun.
DEFAULT_ALIGNMENTS = MappingProxyType({
    k_type_str: 1,
    k_type_vec2: 4,
    k_type_vec3: 4,
    k_type_vec4: 16,
    k_type_mat3x3: 4,
    k_type_mat4x4: 16,
    k_type_array_u32: 4,
    k_type_array_f32: 4,
    k_type_array_u8: 4,
    k_type_objid: 4,
    k_type_event: 4,
})


class ERtpcWrite(Exception):
    """Arbre RTPC impossible a ecrire (type inconnu, valeur invalide, depassement de capacite)."""


@dataclass(frozen=True)
class RtpcLayout:
    """
    Regles de disposition utilisees par ``rtpc_to_binary``.

    pad_byte       octet de bourrage (apex-tools : 0x50, soit 'P').
    dedup_strings  une chaine identique n'est ecrite qu'une fois dans tout le fichier.
    dedup_values   meme regle pour les autres valeurs non inline (apex-tools : non).
    trailing_pad   bourrer la fin du fichier jusqu'a un multiple de 4. apex-tools le fait, mais son code
                   indique en commentaire que l'original ne le fait pas ("On final property in file,
                   fix this to not align") ; valeur par defaut : non.
    alignments     alignement de chaque type de valeur non inline.
    node_trailer   u32 ecrit apres les en-tetes des enfants de chaque noeud (RTPC version 2) :
                   None (absent), 'valid_props' (proprietes de type different de 'none'),
                   'all_props' (toutes les proprietes).
    """
    pad_byte: int = 0x50
    dedup_strings: bool = True
    dedup_values: bool = False
    trailing_pad: bool = False
    alignments: Mapping[int, int] = field(default_factory=lambda: DEFAULT_ALIGNMENTS)
    node_trailer: Optional[str] = None

    def __post_init__(self):
        if self.node_trailer not in NODE_TRAILER_MODES:
            raise ValueError('node_trailer inconnu : {!r}'.format(self.node_trailer))


NODE_TRAILER_MODES = (None, 'valid_props', 'all_props')

DEFAULT_LAYOUT = RtpcLayout()
LAYOUT_V2 = RtpcLayout(node_trailer='valid_props')


def layout_for_version(version) -> RtpcLayout:
    """Regles de disposition par defaut d'une version de RTPC (1 et 2 observees dans COTW)."""
    return LAYOUT_V2 if version == 2 else DEFAULT_LAYOUT


def node_trailer_value(node: RtpcNode, mode: str) -> int:
    """Valeur du u32 de fin d'en-tetes d'un noeud (RTPC version 2)."""
    if mode == 'valid_props':
        return sum(1 for p in node.prop_table if p.type != k_type_none)
    if mode == 'all_props':
        return len(node.prop_table)
    raise ValueError('node_trailer inconnu : {!r}'.format(mode))


def node_trailer_offset(node: RtpcNode) -> int:
    """Offset du u32 de fin d'en-tetes d'un noeud lu par DECA (valable pour un fichier de version 2)."""
    headers_end = node.data_offset + PROP_HEADER_SIZE * len(node.prop_table)
    return headers_end + (-headers_end) % 4 + NODE_HEADER_SIZE * len(node.child_table)


# ---------------------------------------------------------------------------
# Encodage des valeurs
# ---------------------------------------------------------------------------

def _u32(value, what):
    try:
        value = int(value)
    except (TypeError, ValueError):
        raise ERtpcWrite('{} : entier attendu, obtenu {!r}'.format(what, value))
    if not 0 <= value <= 0xFFFFFFFF:
        # les entiers negatifs sont acceptes et ecrits en complement a deux
        if -0x80000000 <= value < 0:
            return value & 0xFFFFFFFF
        raise ERtpcWrite('{} : {} ne tient pas sur 32 bits'.format(what, value))
    return value


def encode_inline(prop: RtpcProperty) -> int:
    """Valeur du champ "donnee" (u32) d'une propriete inline."""
    t = prop.type
    if t == k_type_f32:
        return struct.unpack('<I', struct.pack('<f', float(prop.data)))[0]
    if t in (k_type_none, k_type_u32, k_type_unk_15, k_type_unk_16):
        value = prop.data if prop.data is not None else prop.data_raw
        return _u32(value, 'propriete 0x{:08x}'.format(prop.name_hash))
    raise ERtpcWrite('type {} non inline'.format(t))


def encode_deferred(prop: RtpcProperty) -> bytes:
    """Octets de la valeur d'une propriete non inline, tels qu'ecrits a son offset."""
    t = prop.type
    data = prop.data
    what = 'propriete 0x{:08x}'.format(prop.name_hash)
    if t == k_type_str:
        if isinstance(data, str):
            data = data.encode('utf-8')
        data = bytes(data)
        if b'\0' in data:
            raise ERtpcWrite('{} : une chaine RTPC ne peut pas contenir d\'octet nul'.format(what))
        return data + b'\0'
    if t in FIXED_F32_COUNT:
        n = FIXED_F32_COUNT[t]
        if len(data) != n:
            raise ERtpcWrite('{} : {} flottants attendus, {} fournis'.format(what, n, len(data)))
        return struct.pack('<{}f'.format(n), *data)
    if t == k_type_array_u32:
        return struct.pack('<I', len(data)) + struct.pack('<{}I'.format(len(data)), *[_u32(v, what) for v in data])
    if t == k_type_array_f32:
        return struct.pack('<I', len(data)) + struct.pack('<{}f'.format(len(data)), *data)
    if t == k_type_array_u8:
        return struct.pack('<I', len(data)) + bytes(data)
    if t == k_type_objid:
        return struct.pack('<Q', int(data) & 0xFFFFFFFFFFFFFFFF)
    if t == k_type_event:
        return struct.pack('<I', len(data)) + struct.pack('<{}Q'.format(len(data)),
                                                          *[int(v) & 0xFFFFFFFFFFFFFFFF for v in data])
    if t == k_type_depreciated_12:
        raise ERtpcWrite('{} : type 12 (obsolete) non pris en charge, format inconnu'.format(what))
    raise ERtpcWrite('{} : type inconnu {}'.format(what, t))


def prop_value_bytes(prop: RtpcProperty) -> bytes:
    """Representation binaire canonique d'une valeur (inline ou non), pour les comparaisons exactes."""
    if prop.type in INLINE_TYPES:
        return struct.pack('<I', encode_inline(prop))
    return encode_deferred(prop)


# ---------------------------------------------------------------------------
# Ecriture
# ---------------------------------------------------------------------------

class _Writer:
    def __init__(self, layout: RtpcLayout):
        self.layout = layout
        self.out = bytearray()
        self.real_end = 0          # fin du dernier octet non-bourrage ecrit
        self.strings: Dict[bytes, int] = {}
        self.values: Dict[Tuple[int, bytes], int] = {}

    def pad_to(self, align):
        if align > 1:
            n = (-len(self.out)) % align
            self.out.extend(bytes([self.layout.pad_byte]) * n)

    def append(self, data: bytes) -> int:
        offset = len(self.out)
        self.out.extend(data)
        self.real_end = max(self.real_end, len(self.out))
        return offset

    def reserve(self, size: int) -> int:
        offset = len(self.out)
        self.out.extend(b'\0' * size)
        self.real_end = max(self.real_end, len(self.out))
        return offset

    def write_value(self, prop: RtpcProperty) -> int:
        if prop.type in INLINE_TYPES:
            return encode_inline(prop)
        data = encode_deferred(prop)
        if prop.type == k_type_str:
            if self.layout.dedup_strings and data in self.strings:
                return self.strings[data]
        elif self.layout.dedup_values and (prop.type, data) in self.values:
            return self.values[(prop.type, data)]
        self.pad_to(self.layout.alignments.get(prop.type, 4))
        offset = self.append(data)
        if prop.type == k_type_str:
            self.strings.setdefault(data, offset)
        else:
            self.values.setdefault((prop.type, data), offset)
        if offset > 0xFFFFFFFF:
            raise ERtpcWrite('fichier trop grand : offset {} au-dela de 32 bits'.format(offset))
        return offset

    def write_node(self, node: RtpcNode) -> int:
        """Ecrit le bloc d'un noeud a la position courante (alignee sur 4) et renvoie son offset."""
        props = node.prop_table
        children = node.child_table
        if len(props) > 0xFFFF or len(children) > 0xFFFF:
            raise ERtpcWrite('noeud 0x{:08x} : plus de 65535 proprietes ou enfants'.format(node.name_hash))

        offset = self.reserve(PROP_HEADER_SIZE * len(props))
        self.pad_to(4)
        child_headers = self.reserve(NODE_HEADER_SIZE * len(children))
        if self.layout.node_trailer:
            self.append(struct.pack('<I', node_trailer_value(node, self.layout.node_trailer)))

        raws = [self.write_value(p) for p in props]
        if props:
            self.pad_to(4)
        for i, (p, raw) in enumerate(zip(props, raws)):
            if not 0 <= p.type <= 0xFF:
                raise ERtpcWrite('propriete 0x{:08x} : type {} hors octet'.format(p.name_hash, p.type))
            struct.pack_into('<IIB', self.out, offset + PROP_HEADER_SIZE * i,
                             _u32(p.name_hash, 'nom de propriete'), raw, p.type)

        for i, child in enumerate(children):
            child_offset = self.write_node(child)
            struct.pack_into('<IIHH', self.out, child_headers + NODE_HEADER_SIZE * i,
                             _u32(child.name_hash, 'nom de noeud'), child_offset,
                             len(child.prop_table), len(child.child_table))
        return offset


def rtpc_to_binary(rtpc: Rtpc, layout: Optional[RtpcLayout] = None) -> bytes:
    """
    Serialise un arbre RTPC complet (en-tete de fichier compris).
    Sans ``layout``, les regles sont choisies d'apres la version de l'arbre (``layout_for_version``).
    """
    if rtpc.root_node is None:
        raise ERtpcWrite('arbre RTPC sans noeud racine')
    if layout is None:
        layout = layout_for_version(rtpc.version)
    w = _Writer(layout)
    w.append(RTPC_MAGIC + struct.pack('<I', _u32(rtpc.version if rtpc.version is not None else 1, 'version')))
    root_header = w.reserve(NODE_HEADER_SIZE)
    root = rtpc.root_node
    root_offset = w.write_node(root)
    struct.pack_into('<IIHH', w.out, root_header, _u32(root.name_hash, 'nom de noeud'), root_offset,
                     len(root.prop_table), len(root.child_table))
    if layout.trailing_pad:
        w.pad_to(4)
        return bytes(w.out)
    return bytes(w.out[:w.real_end])


def rtpc_from_bytes(data: bytes) -> Rtpc:
    """Lit un RTPC depuis des octets, avec le lecteur de DECA."""
    import io
    return rtpc_from_binary(io.BytesIO(data))


# ---------------------------------------------------------------------------
# Comparaison
# ---------------------------------------------------------------------------

def rtpc_diff(a: Rtpc, b: Rtpc) -> Optional[str]:
    """
    Compare deux arbres. Renvoie None s'ils sont identiques, sinon la description de la premiere
    difference (chemin des noeuds par index). Les valeurs sont comparees par leur representation binaire,
    ce qui traite correctement -0.0 et les NaN.
    """
    if a.version != b.version:
        return 'version {} != {}'.format(a.version, b.version)

    def walk(na: RtpcNode, nb: RtpcNode, path: str) -> Optional[str]:
        if na.name_hash != nb.name_hash:
            return '{} : nom 0x{:08x} != 0x{:08x}'.format(path, na.name_hash, nb.name_hash)
        if len(na.prop_table) != len(nb.prop_table):
            return '{} : {} proprietes != {}'.format(path, len(na.prop_table), len(nb.prop_table))
        for i, (pa, pb) in enumerate(zip(na.prop_table, nb.prop_table)):
            where = '{}/prop[{}]'.format(path, i)
            if pa.name_hash != pb.name_hash or pa.type != pb.type:
                return '{} : (0x{:08x}, type {}) != (0x{:08x}, type {})'.format(
                    where, pa.name_hash, pa.type, pb.name_hash, pb.type)
            if prop_value_bytes(pa) != prop_value_bytes(pb):
                return '{} (0x{:08x}) : valeur {!r} != {!r}'.format(where, pa.name_hash, pa.data, pb.data)
        if len(na.child_table) != len(nb.child_table):
            return '{} : {} enfants != {}'.format(path, len(na.child_table), len(nb.child_table))
        for i, (ca, cb) in enumerate(zip(na.child_table, nb.child_table)):
            r = walk(ca, cb, '{}/{}'.format(path, i))
            if r:
                return r
        return None

    return walk(a.root_node, b.root_node, 'racine')


# ---------------------------------------------------------------------------
# Aides d'edition
# ---------------------------------------------------------------------------

NameLike = Union[int, str, bytes]


def name_hash(name: NameLike) -> int:
    """Hash d'un nom de noeud ou de propriete (hash32 Jenkins, comme le jeu) ; un entier est garde tel quel."""
    if isinstance(name, int):
        return _u32(name, 'hash')
    return int(hash32_func(name))


def make_prop(name: NameLike, prop_type: int, value) -> RtpcProperty:
    """Cree une propriete. ``value`` suit la representation du lecteur DECA (voir ``encode_deferred``)."""
    p = RtpcProperty()
    p.name_hash = name_hash(name)
    p.type = prop_type
    p.data = value
    p.data_raw = 0
    prop_value_bytes(p)  # valide la valeur tout de suite plutot qu'a l'ecriture
    return p


def make_node(name: NameLike, props=(), children=()) -> RtpcNode:
    n = RtpcNode()
    n.name_hash = name_hash(name)
    n.data_offset = None
    for p in props:
        node_add_prop(n, p, position=len(n.prop_table))
    for c in children:
        node_add_child(n, c, position=len(n.child_table))
    return n


def _is_sorted(hashes: List[int]) -> bool:
    return all(hashes[i] <= hashes[i + 1] for i in range(len(hashes) - 1))


def _sorted_position(hashes: List[int], h: int) -> int:
    lo, hi = 0, len(hashes)
    while lo < hi:
        mid = (lo + hi) // 2
        if hashes[mid] <= h:
            lo = mid + 1
        else:
            hi = mid
    return lo


def _sync_counts(node: RtpcNode):
    node.prop_count = len(node.prop_table)
    node.child_count = len(node.child_table)


def node_get_prop(node: RtpcNode, name: NameLike) -> Optional[RtpcProperty]:
    h = name_hash(name)
    return next((p for p in node.prop_table if p.name_hash == h), None)


def node_get_child(node: RtpcNode, name: NameLike) -> Optional[RtpcNode]:
    h = name_hash(name)
    return next((c for c in node.child_table if c.name_hash == h), None)


def node_add_prop(node: RtpcNode, prop: RtpcProperty, position: Optional[int] = None) -> int:
    """
    Ajoute une propriete et renvoie son index.

    Sans ``position`` : si les proprietes du noeud sont deja triees par hash croissant, la nouvelle est
    inseree a sa place dans cet ordre, sinon elle est ajoutee a la fin. Raison : on ne sait pas si le
    moteur recherche les proprietes par dichotomie ; conserver un tri existant est le choix prudent.
    Deux proprietes du meme noeud ne peuvent pas avoir le meme hash (le moteur les retrouve par hash).
    """
    if any(p.name_hash == prop.name_hash for p in node.prop_table):
        raise ValueError('le noeud 0x{:08x} a deja une propriete 0x{:08x}'.format(node.name_hash, prop.name_hash))
    hashes = [p.name_hash for p in node.prop_table]
    if position is None:
        position = _sorted_position(hashes, prop.name_hash) if _is_sorted(hashes) else len(hashes)
    node.prop_table.insert(position, prop)
    node.prop_map[prop.name_hash] = prop
    _sync_counts(node)
    return position


def node_add_child(node: RtpcNode, child: RtpcNode, position: Optional[int] = None) -> int:
    """
    Ajoute un noeud enfant et renvoie son index. Meme regle de tri que ``node_add_prop``.
    Plusieurs enfants peuvent partager un meme hash (listes d'elements) : c'est autorise.
    """
    hashes = [c.name_hash for c in node.child_table]
    if position is None:
        position = _sorted_position(hashes, child.name_hash) if _is_sorted(hashes) else len(hashes)
    node.child_table.insert(position, child)
    node.child_map[child.name_hash] = child
    _sync_counts(node)
    return position


def node_remove_prop(node: RtpcNode, name: NameLike) -> RtpcProperty:
    h = name_hash(name)
    for i, p in enumerate(node.prop_table):
        if p.name_hash == h:
            del node.prop_table[i]
            node.prop_map.pop(h, None)
            _sync_counts(node)
            return p
    raise KeyError('propriete 0x{:08x} absente du noeud 0x{:08x}'.format(h, node.name_hash))


def clone_node(node: RtpcNode) -> RtpcNode:
    """Copie profonde d'un noeud (proprietes et enfants), sans les positions du fichier d'origine."""
    n = make_node(node.name_hash)
    for p in node.prop_table:
        data = p.data
        if isinstance(data, list):
            data = list(data)
        c = RtpcProperty()
        c.name_hash, c.type, c.data, c.data_raw = p.name_hash, p.type, data, p.data_raw
        n.prop_table.append(c)
        n.prop_map[c.name_hash] = c
    for ch in node.child_table:
        cc = clone_node(ch)
        n.child_table.append(cc)
        n.child_map[cc.name_hash] = cc
    _sync_counts(n)
    return n
