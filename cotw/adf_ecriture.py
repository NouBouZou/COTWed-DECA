"""
Ecriture d'un fichier ADF dont des tableaux grandissent, sans deplacer aucune donnee existante.

Pourquoi : DECA lit les ADF mais ne sait pas les ecrire. Ajouter un objet au jeu demande d'ajouter des elements
a des tableaux : par exemple une ligne a equipment_stats_ui.bin (XLSBook), qui porte les stats affichees et le
logo de possession au magasin. adf_remplacer_chaine.py ne sait que modifier sur place, sans changer la taille.

Disposition d'un ADF, telle que la lit deca.ff_adf.Adf.deserialize (offsets relatifs au debut de l'ADF, c'est-a-
dire a ' FDA', qui est en 0 ou en 5 dans le fichier) :
  0x00  ' FDA', u32 version, puis (u32 nombre, u32 offset) des instances, des types, des StringHash et des noms,
        u32 taille totale, 5 u32 inconnus ; en 0x40, un commentaire termine par 0.
  table des instances : 24 octets par instance : u32 hash du nom, u32 type, u32 OFFSET ABSOLU, u32 taille,
        u64 index du nom.
  types, StringHash, noms : aucun offset absolu.
  donnees d'une instance : [offset, offset + taille). A l'interieur, chaines et tableaux sont designes par des
        CHAMPS POINTEURS de 8 octets : u32 offset de la cible, RELATIF au debut de l'instance, puis u32 LIEN :
          chaine (String, 8 octets) : [u32 cible][u32 lien] ; le texte, termine par 0, est a la cible ;
          tableau (Array, 16 octets): [u32 cible][u32 lien][u32 nombre d'elements][u32 0] ; les elements se
                                     suivent sans bourrage, chacun de la taille de son type.

Chaine de relocation (constat sur le vrai equipment_stats_ui.bin, 2026-10-03) : le lien d'un champ pointeur est la
distance, en octets, jusqu'au champ pointeur suivant ; 0 termine la chaine. Elle part du premier champ pointeur et
visite, en ordre croissant, les 545 champs pointeurs de l'instance dont la cible est non nulle (un tableau vide, de
cible 0, n'en fait pas partie). DECA appelle ce lien "longueur" pour une chaine et "flags" pour un tableau, et
l'ignore. [hypothese forte] Le jeu convertit les offsets en adresses en suivant cette chaine : un champ pointeur
absent de la chaine resterait un offset brut.

Methode :
  1. La chaine d'origine est controlee (read_chain) : elle doit partir du premier champ pointeur, croitre et
     contenir tous les champs pointeurs a cible non nulle que donnent les types. Sinon, refus (disposition
     inconnue). Dans un fichier deja produit par cet outil, elle contient en plus les anciennes copies.
  2. Tout ajout va a la FIN de l'instance. Un tableau qui grandit est recopie en entier a la fin, suivi des
     nouveaux elements, puis son en-tete est repointe (cible et nombre). Les elements recopies gardent leurs
     cibles, relatives a l'instance : ils designent toujours les memes sous-donnees. L'ancienne copie reste en
     place, inutilisee ; ses champs pointeurs restent dans la chaine (cibles valides).
  3. Les champs pointeurs des octets ajoutes (copies d'elements, nouveaux en-tetes de chaine) sont rattaches a
     la chaine : tous les liens sont recalcules sur l'ensemble trie des champs a cible non nulle. Les liens de la
     partie d'origine ne changent pas, sauf celui de la fin (0 -> premier ajout) et ceux qui encadrent un tableau
     auparavant vide. La tete de la chaine ne doit pas changer : refus sinon (on ne sait pas ou elle est notee).
  4. Les octets ajoutes sont inseres juste apres l'instance, completes a un multiple de 16 : tout ce qui suit
     garde son alignement. Les octets qui suivaient l'instance avant la zone suivante (04 00 00 00 00 00 dans
     equipment_stats_ui.bin, role inconnu) restent colles a sa nouvelle fin ; le bourrage va apres eux. Les
     donnees de tableau ajoutees sont alignees sur 8, comme dans les fichiers du jeu. Chaque offset absolu situe apres le point d'insertion (offsets des tables dans
     l'en-tete, offsets des autres instances) est decale d'autant ; la taille de l'instance et la taille totale
     grandissent.
Refus aussi : taille totale differente de la taille de l'ADF, table situee a l'interieur de l'instance, instances
qui se chevauchent, tableau en ligne (InlineArray, taille fixe), instance raccourcie, types Pointer ou Deferred
(champs pointeurs de forme inconnue).
"""
import struct
from typing import Dict, List, Optional, Tuple

STRING_TYPE = 0x8955583e
HEADER = struct.Struct('<4sIIIIIIIIII')   # magic, version, (nombre, offset) x 4, taille totale
INSTANCE_ENTRY = 24
ARRAY_HEADER = 16                          # u32 offset, u32 flags, u32 nombre, u32 inconnu
INSERT_ALIGN = 16
ARRAY_DATA_ALIGN = 8                       # alignement des donnees de tableau observe dans les fichiers du jeu
META_ARRAY, META_INLINE_ARRAY, META_STRUCT = 3, 4, 1


class EAdfEcriture(Exception):
    pass


def adf_base(data: bytes) -> int:
    """Position de ' FDA' dans le fichier : 0, ou 5 pour les ADF precedes d'un petit en-tete (adf5)."""
    if data[:4] == b' FDA':
        return 0
    if data[5:9] == b' FDA':
        return 5
    raise EAdfEcriture('pas un fichier ADF (en-tete " FDA" absent)')


def prim_sizes() -> Dict[int, int]:
    from deca import ff_adf as A
    return {A.typedef_u8: 1, A.typedef_s8: 1, A.typedef_u16: 2, A.typedef_s16: 2, A.typedef_u32: 4,
            A.typedef_s32: 4, A.typedef_f32: 4, A.typedef_u64: 8, A.typedef_s64: 8, A.typedef_f64: 8}


class Header:
    """Champs de l'en-tete ADF qui contiennent des offsets absolus."""
    TABLES = ('instances', 'types', 'stringhash', 'noms')

    def __init__(self, data: bytes, base: int):
        if len(data) < base + 0x40:
            raise EAdfEcriture('fichier ADF trop court')
        f = HEADER.unpack_from(data, base)
        self.version = f[1]
        self.tables = {name: (f[2 + 2 * i], f[3 + 2 * i]) for i, name in enumerate(self.TABLES)}
        self.total = f[10]
        # 5 u32 inconnus (0x2c..0x40) : laisses tels quels. S'ils contenaient un offset, insert() ne le
        # decalerait pas ; adf_ecriture_controle.py releve leurs valeurs sur les fichiers du jeu.
        self.unknown = struct.unpack_from('<5I', data, base + 0x2c)

    @staticmethod
    def offset_position(base: int, table_index: int) -> int:
        """Position, dans le fichier, du u32 offset de la table numero table_index."""
        return base + 4 + 4 + 8 * table_index + 4

    TOTAL_POSITION = 4 + 4 + 8 * 4


def instance_entries(data: bytes, base: int, header: Header) -> List[Tuple[int, int, int]]:
    """[(position de l'entree dans le fichier, offset, taille)] de la table des instances."""
    count, offset = header.tables['instances']
    out = []
    for i in range(count):
        pos = base + offset + INSTANCE_ENTRY * i
        if pos + INSTANCE_ENTRY > len(data):
            raise EAdfEcriture('table des instances hors du fichier')
        _h, _t, ins_off, ins_size = struct.unpack_from('<IIII', data, pos)
        out.append((pos, ins_off, ins_size))
    return out


def insert(data: bytes, index: int, new_instance: bytes) -> bytes:
    """Remplace l'instance numero index par new_instance, au moins aussi longue (les en-tetes repointes et les
    compteurs sont modifies sur place, le reste est ajoute a la fin). Renvoie le nouveau fichier. Le contenu
    n'est pas controle ici : l'appelant relit le resultat avec DECA."""
    base = adf_base(data)
    header = Header(data, base)
    entries = instance_entries(data, base, header)
    if not 0 <= index < len(entries):
        raise EAdfEcriture('instance {} absente ({} instances)'.format(index, len(entries)))
    _pos, start, size = entries[index]
    end = start + size
    if base + end > len(data):
        raise EAdfEcriture('instance {} hors du fichier'.format(index))
    if len(new_instance) < size:
        raise EAdfEcriture('insert : instance raccourcie ({} -> {} octets)'.format(size, len(new_instance)))
    if header.total != len(data) - base:
        raise EAdfEcriture('taille totale de l\'en-tete ({}) differente de la taille de l\'ADF ({}) : disposition '
                           'inconnue, refus'.format(header.total, len(data) - base))
    for name, (count, offset) in header.tables.items():
        if count and start <= offset < end:
            raise EAdfEcriture('la table "{}" est a l\'interieur de l\'instance {} : refus'.format(name, index))
    for j, (_p, o, s) in enumerate(entries):
        if j != index and s and o < end and start < o + s:
            raise EAdfEcriture('les instances {} et {} se chevauchent : refus'.format(index, j))

    delta = len(new_instance) - size
    shift = -(-delta // INSERT_ALIGN) * INSERT_ALIGN
    # octets entre la fin de l'instance et la zone suivante (table ou instance) : gardes COLLES a la fin de
    # l'instance, le bourrage d'alignement va apres eux. Dans equipment_stats_ui.bin ils valent 04 00 00 00 00 00 ;
    # role inconnu : le jeu pourrait les lire a offset + taille.
    following = [o for _n, (c, o) in header.tables.items() if c and o >= end]
    following += [o for j, (_p, o, s) in enumerate(entries) if j != index and o >= end]
    next_start = min(following) if following else len(data) - base
    gap = data[base + end:base + next_start]
    out = (bytearray(data[:base + start]) + new_instance + gap + b'\0' * (shift - delta)
           + data[base + next_start:])

    def moved(offset: int) -> int:
        return offset + shift if offset >= end else offset

    for i, name in enumerate(Header.TABLES):
        _count, offset = header.tables[name]
        struct.pack_into('<I', out, Header.offset_position(base, i), moved(offset))
    struct.pack_into('<I', out, base + Header.TOTAL_POSITION, header.total + shift)
    table = base + moved(header.tables['instances'][1])
    for j, (_p, o, s) in enumerate(entries):
        pos = table + INSTANCE_ENTRY * j
        struct.pack_into('<I', out, pos + 8, moved(o) if j != index else o)
        struct.pack_into('<I', out, pos + 12, s + delta if j == index else s)
    return bytes(out)


DEFERRED_TYPE = 0xdefe88ed
META_POINTER = 2
CHAIN_LINK = 4                             # position du lien dans un champ pointeur


class InstanceEditor:
    """Octets d'une instance, avec des ajouts en fin d'instance. Les positions sont relatives a l'instance.
    result() rattache les nouveaux champs pointeurs a la chaine de relocation et renvoie le fichier complet."""

    def __init__(self, adf, data: bytes, index: int = 0):
        self.adf = adf
        self.data = data
        self.base = adf_base(data)
        self.index = index
        ins = adf.table_instance[index]
        self.start = ins.offset
        self.size = ins.size
        self.buf = bytearray(data[self.base + ins.offset:self.base + ins.offset + ins.size])
        if len(self.buf) != ins.size:
            raise EAdfEcriture('instance {} tronquee'.format(index))
        self.types = adf.extended_map_typedef
        self._prims = prim_sizes()
        self.fields = pointer_fields(adf, index)          # champs pointeurs d'origine (positions relatives)
        self.chain, self.dead = read_chain(self.buf, self.fields)   # chaine d'origine, controlee
        self.new_fields: List[int] = []
        self.final_chain: List[int] = []

    # --- octets ---
    def rel(self, abs_offset: int) -> int:
        r = abs_offset - self.start
        if not 0 <= r < self.size:
            raise EAdfEcriture('offset 0x{:x} hors de l\'instance {}'.format(abs_offset, self.index))
        return r

    def read_uint(self, pos: int, size: int) -> int:
        return int.from_bytes(self.buf[pos:pos + size], 'little')

    def write_uint(self, pos: int, size: int, value: int):
        if not 0 <= value < (1 << (8 * size)):
            raise EAdfEcriture('valeur {} hors limites pour {} octet(s)'.format(value, size))
        self.buf[pos:pos + size] = value.to_bytes(size, 'little')

    def append(self, payload: bytes, align: int) -> int:
        """Ajoute payload a la fin de l'instance, aligne ; renvoie sa position."""
        self.buf += b'\0' * ((-len(self.buf)) % align)
        pos = len(self.buf)
        self.buf += payload
        return pos

    def result(self) -> bytes:
        """Rattache les nouveaux champs pointeurs a la chaine, puis renvoie le fichier complet."""
        self.link()
        return insert(self.data, self.index, bytes(self.buf))

    def link(self) -> List[int]:
        """Recalcule les liens sur l'ensemble trie des champs pointeurs a cible non nulle ; renvoie la chaine."""
        candidates = set(self.fields) | set(self.new_fields)
        chain = sorted(p for p in candidates if struct.unpack_from('<I', self.buf, p)[0] != 0)
        if self.chain and chain and chain[0] != self.chain[0]:
            raise EAdfEcriture('la tete de la chaine de relocation changerait (0x{:x} -> 0x{:x}) : on ne sait pas '
                               'ou elle est notee, refus'.format(self.chain[0], chain[0]))
        if not self.chain and chain:
            raise EAdfEcriture('instance sans chaine de relocation : impossible d\'y ajouter des champs pointeurs')
        for i, p in enumerate(chain):
            struct.pack_into('<I', self.buf, p + CHAIN_LINK, chain[i + 1] - p if i + 1 < len(chain) else 0)
        self.final_chain = chain
        return chain

    # --- types ---
    def type_layout(self, type_hash: int) -> Tuple[int, int]:
        """(taille, alignement) d'une valeur du type."""
        return type_layout(self.types, self._prims, type_hash)

    def member(self, struct_type: int, name: str):
        td = self.types.get(struct_type)
        if td is None or td.metatype != META_STRUCT:
            raise EAdfEcriture('type 0x{:08x} : pas une structure'.format(struct_type))
        for m in td.members:
            if m.name_utf8 == name:
                return m
        raise EAdfEcriture('structure {} : pas de membre "{}"'.format(td.name.decode('utf-8', 'replace'), name))

    def member_pos(self, struct_value, name: str) -> Tuple[int, object]:
        """(position du membre dans l'instance, MemberDef) pour une structure lue par DECA (AdfValue)."""
        m = self.member(struct_value.type_id, name)
        return self.rel(struct_value.info_offset) + m.offset, m

    # --- tableaux ---
    def array(self, array_value) -> Tuple[int, int, int, int]:
        """(position de l'en-tete, type des elements, taille d'un element, alignement)."""
        td = self.types.get(array_value.type_id)
        if td is None or td.metatype not in (META_ARRAY, META_INLINE_ARRAY):
            raise EAdfEcriture('pas un tableau (type 0x{:08x})'.format(array_value.type_id))
        if td.metatype == META_INLINE_ARRAY:
            raise EAdfEcriture('tableau en ligne (taille fixe) : impossible a agrandir')
        size, align = self.type_layout(td.element_type_hash)
        return self.rel(array_value.info_offset), td.element_type_hash, size, align

    def array_state(self, header_pos: int) -> Tuple[int, int]:
        """(cible, nombre) actuels du tableau, lus dans les octets (un tableau deja deplace est suivi)."""
        offset, _link, count = struct.unpack_from('<III', self.buf, header_pos)
        return offset, count

    def element(self, array_value, i: int) -> Tuple[int, bytes]:
        """(position, octets) de l'element i, a sa place actuelle."""
        header, _t, size, _a = self.array(array_value)
        offset, count = self.array_state(header)
        if not 0 <= i < count:
            raise EAdfEcriture('element {} absent ({} elements)'.format(i, count))
        pos = offset + i * size
        return pos, bytes(self.buf[pos:pos + size])

    def grow(self, array_value, new_elements: List[bytes]) -> int:
        """Recopie le tableau a la fin de l'instance, suivi de new_elements, et repointe son en-tete. Les champs
        pointeurs des elements recopies et ajoutes sont notes pour la chaine. Renvoie l'index du premier nouvel
        element. Avec new_elements vide : simple deplacement."""
        header, elem_type, size, align = self.array(array_value)
        offset, count = self.array_state(header)
        for e in new_elements:
            if len(e) != size:
                raise EAdfEcriture('element de {} octets pour un tableau d\'elements de {} octets'.format(len(e), size))
        old = bytes(self.buf[offset:offset + count * size]) if count else b''
        if len(old) != count * size:
            raise EAdfEcriture('tableau hors de l\'instance')
        # donnees de tableau alignees sur 8 (au moins), comme tous les tableaux des fichiers du jeu
        new_offset = self.append(old + b''.join(new_elements), max(align, ARRAY_DATA_ALIGN))
        inner = type_pointer_fields(self.types, self._prims, elem_type)
        for i in range(count + len(new_elements)):
            self.new_fields += [new_offset + i * size + f for f in inner]
        struct.pack_into('<I', self.buf, header, new_offset)
        struct.pack_into('<I', self.buf, header + 8, count + len(new_elements))
        if header not in self.fields:
            self.fields.append(header)
        return count

    # --- chaines ---
    def new_string(self, text: bytes) -> bytes:
        """Ecrit le texte (termine par 0, aligne sur 8 comme dans les fichiers du jeu) a la fin de l'instance ;
        renvoie l'en-tete de chaine (8 octets), lien a 0 : result() le rattache a la chaine."""
        if b'\0' in text:
            raise EAdfEcriture('chaine contenant un octet nul')
        pos = self.append(text + b'\0', 8)
        return struct.pack('<II', pos, 0)


def type_layout(types, prims, type_hash: int) -> Tuple[int, int]:
    if type_hash in prims:
        return prims[type_hash], prims[type_hash]
    if type_hash == STRING_TYPE:
        return 8, 8
    td = types.get(type_hash)
    if td is None:
        raise EAdfEcriture('type 0x{:08x} inconnu'.format(type_hash))
    return td.size, max(1, td.alignment)


def type_pointer_fields(types, prims, type_hash: int) -> List[int]:
    """Positions des champs pointeurs a l'interieur d'une valeur du type (sans suivre les cibles)."""
    if type_hash in prims:
        return []
    if type_hash == STRING_TYPE:
        return [0]
    if type_hash == DEFERRED_TYPE:
        raise EAdfEcriture('type Deferred : forme du champ pointeur inconnue, refus')
    td = types.get(type_hash)
    if td is None:
        raise EAdfEcriture('type 0x{:08x} inconnu'.format(type_hash))
    if td.metatype == META_STRUCT:
        return [m.offset + f for m in td.members for f in type_pointer_fields(types, prims, m.type_hash)]
    if td.metatype == META_ARRAY:
        return [0]
    if td.metatype == META_INLINE_ARRAY:
        size, _a = type_layout(types, prims, td.element_type_hash)
        inner = type_pointer_fields(types, prims, td.element_type_hash)
        return [i * size + f for i in range(td.element_length) for f in inner]
    if td.metatype == META_POINTER:
        raise EAdfEcriture('type Pointer : forme du champ pointeur inconnue, refus')
    return []


def pointer_fields(adf, index: int) -> List[int]:
    """Champs pointeurs (chaines et tableaux) de l'instance, atteints par les types depuis la racine, positions
    relatives a l'instance, d'apres la lecture de DECA."""
    from deca.ff_adf import AdfValue
    types = adf.extended_map_typedef
    start = adf.table_instance[index].offset
    root = adf.table_instance_full_values[index]
    if root is None:
        raise EAdfEcriture('instance {} illisible par DECA'.format(index))
    found = set()

    def walk(v):
        if not isinstance(v, AdfValue):
            return
        if v.type_id in (DEFERRED_TYPE,):
            raise EAdfEcriture('type Deferred dans l\'instance : forme du champ pointeur inconnue, refus')
        td = types.get(v.type_id)
        if td is not None and td.metatype == META_POINTER:
            raise EAdfEcriture('type Pointer dans l\'instance : forme du champ pointeur inconnue, refus')
        if v.type_id == STRING_TYPE or (td is not None and td.metatype == META_ARRAY):
            found.add(v.info_offset - start)
        val = v.value
        if isinstance(val, dict):
            for x in val.values():
                walk(x)
        elif isinstance(val, list):
            for x in val:
                walk(x)

    walk(root)
    return sorted(found)


def walk_chain(buf: bytes, head: int) -> List[int]:
    """Positions visitees en suivant les liens depuis head, jusqu'au lien 0."""
    chain, pos = [], head
    while True:
        if not 0 <= pos <= len(buf) - 8:
            raise EAdfEcriture('chaine de relocation : champ 0x{:x} hors de l\'instance'.format(pos))
        chain.append(pos)
        link = struct.unpack_from('<I', buf, pos + CHAIN_LINK)[0]
        if link == 0:
            return chain
        if link % 4 or len(chain) > len(buf) // 8:
            raise EAdfEcriture('chaine de relocation : lien {} invalide en 0x{:x}'.format(link, pos))
        pos += link


def read_chain(buf: bytes, fields: List[int]) -> Tuple[List[int], List[int]]:
    """Lit et controle la chaine de relocation d'une instance. fields : champs pointeurs atteignables par les types.
    La chaine part du premier champ a cible non nulle, croit strictement, contient tous les champs atteignables a
    cible non nulle, et chacun de ses champs a une cible dans l'instance. Renvoie (chaine, champs en trop) : les
    champs en trop sont d'anciennes copies laissees par une ecriture precedente (inutilisees, cibles valides) ; un
    fichier du jeu n'en a pas. Instance sans champ pointeur a cible non nulle : ([], [])."""
    targets = sorted(p for p in fields if struct.unpack_from('<I', buf, p)[0] != 0)
    if not targets:
        return [], []
    chain = walk_chain(buf, targets[0])
    if any(b <= a for a, b in zip(chain, chain[1:])):
        raise EAdfEcriture('chaine de relocation non croissante : disposition inconnue, refus')
    missing = sorted(set(targets) - set(chain))
    if missing:
        raise EAdfEcriture('chaine de relocation absente ou incomplete : {} champ(s) pointeur(s) non rattache(s) '
                           'sur {}, ex. 0x{:x} : disposition inconnue, refus'.format(len(missing), len(targets), missing[0]))
    for p in chain:
        if not 0 < struct.unpack_from('<I', buf, p)[0] < len(buf):
            raise EAdfEcriture('chaine de relocation : cible invalide en 0x{:x}'.format(p))
    return chain, sorted(set(chain) - set(targets))


def verify_chain(adf, data: bytes, index: int, head: Optional[int] = None, exact: bool = False) -> List[int]:
    """Chaine de relocation d'une instance (adf : la lecture de data par DECA), controlee par read_chain ; head :
    tete attendue (celle du fichier d'origine) ; exact : aucun champ en trop (fichier du jeu)."""
    ins = adf.table_instance[index]
    base = adf_base(data)
    buf = data[base + ins.offset:base + ins.offset + ins.size]
    chain, extras = read_chain(buf, pointer_fields(adf, index))
    if head is not None and chain and chain[0] != head:
        raise EAdfEcriture('tete de la chaine de relocation changee (0x{:x} -> 0x{:x})'.format(head, chain[0]))
    if exact and extras:
        raise EAdfEcriture('chaine de relocation : {} champ(s) en trop, ex. 0x{:x}'.format(len(extras), extras[0]))
    return chain


def normalize(v):
    """Valeurs lues par DECA (adf_value_extract), rendues comparables : tableaux et scalaires numpy par leurs
    octets (un NaN reste egal a lui-meme), flottants Python par leurs octets."""
    import numpy as np
    if isinstance(v, dict):
        return {k: normalize(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [normalize(x) for x in v]
    if isinstance(v, np.ndarray):
        return ('nd', v.dtype.str, v.tobytes())
    if isinstance(v, np.generic):
        return ('np', v.dtype.str, v.tobytes())
    if isinstance(v, float):
        return ('f', struct.pack('<d', v))
    if isinstance(v, (bytes, bytearray)):
        return bytes(v)
    return v


def plain_values(adf, index: int = 0):
    from deca.ff_adf import adf_value_extract
    return adf_value_extract(adf.table_instance_full_values[index])
