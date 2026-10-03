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
        offsets RELATIFS au debut de l'instance :
          chaine (String, 8 octets) : u32 offset, u32 second champ (longueur pour DECA ; vaut 8 dans les XLSBook
                                     de COTW, quelle que soit la chaine) ;
          tableau (Array)           : u32 offset, u32 flags, u32 nombre d'elements, u32 inconnu ; les elements
                                     se suivent sans bourrage, chacun de la taille de son type.

Methode :
  1. Tout ajout va a la FIN de l'instance. Un tableau qui grandit est recopie en entier a la fin, suivi des
     nouveaux elements, puis son en-tete est repointe (offset et nombre ; flags et 4e champ gardes). Les
     elements recopies gardent leurs offsets internes, qui sont relatifs a l'instance : ils designent toujours
     les memes sous-donnees. L'ancienne copie reste en place, inutilisee. Aucun offset relatif existant ne change.
  2. Les octets ajoutes sont inseres juste apres l'instance, completes a un multiple de 16 : tout ce qui suit
     garde son alignement. Chaque offset absolu situe apres le point d'insertion (offsets des tables dans
     l'en-tete, offsets des autres instances) est decale d'autant ; la taille de l'instance et la taille totale
     grandissent.
Refus : taille totale differente de la taille de l'ADF, table situee a l'interieur de l'instance, instances qui
se chevauchent, tableau en ligne (InlineArray, taille fixe), instance raccourcie.

[hypothese] Le moteur suit les offsets d'apres les types, sans table de relocation : un tableau deplace est lu a sa
nouvelle place. A verifier en jeu (essai test9) ; adf_ecriture_controle.py le verifie d'abord avec DECA sur les
fichiers reels.
"""
import struct
from typing import Callable, Dict, List, Optional, Tuple

STRING_TYPE = 0x8955583e
HEADER = struct.Struct('<4sIIIIIIIIII')   # magic, version, (nombre, offset) x 4, taille totale
INSTANCE_ENTRY = 24
ARRAY_HEADER = 16                          # u32 offset, u32 flags, u32 nombre, u32 inconnu
INSERT_ALIGN = 16
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
    out = bytearray(data[:base + start]) + new_instance + b'\0' * (shift - delta) + data[base + end:]

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


class InstanceEditor:
    """Octets d'une instance, avec des ajouts en fin d'instance. Les positions sont relatives a l'instance."""

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
        """Le fichier complet, avec l'instance agrandie."""
        return insert(self.data, self.index, bytes(self.buf))

    # --- types ---
    def type_layout(self, type_hash: int) -> Tuple[int, int]:
        """(taille, alignement) d'une valeur du type."""
        if type_hash in self._prims:
            return self._prims[type_hash], self._prims[type_hash]
        if type_hash == STRING_TYPE:
            return 8, 8
        td = self.types.get(type_hash)
        if td is None:
            raise EAdfEcriture('type 0x{:08x} inconnu'.format(type_hash))
        return td.size, max(1, td.alignment)

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
        """(offset, nombre) actuels du tableau, lus dans les octets (un tableau deja deplace est suivi)."""
        offset, _flags, count = struct.unpack_from('<III', self.buf, header_pos)
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
        """Recopie le tableau a la fin de l'instance, suivi de new_elements, et repointe son en-tete.
        Renvoie l'index du premier nouvel element. Avec new_elements vide : simple deplacement."""
        header, _t, size, align = self.array(array_value)
        offset, count = self.array_state(header)
        for e in new_elements:
            if len(e) != size:
                raise EAdfEcriture('element de {} octets pour un tableau d\'elements de {} octets'.format(len(e), size))
        old = bytes(self.buf[offset:offset + count * size]) if count else b''
        if len(old) != count * size:
            raise EAdfEcriture('tableau hors de l\'instance')
        new_offset = self.append(old + b''.join(new_elements), max(align, 4))
        struct.pack_into('<I', self.buf, header, new_offset)
        struct.pack_into('<I', self.buf, header + 8, count + len(new_elements))
        return count

    # --- chaines ---
    def string_rule(self, array_value) -> Tuple[Callable[[int], int], str]:
        """Regle du second champ des en-tetes de chaine d'un tableau de String, deduite des chaines existantes :
        longueur, longueur + 1, ou constante. Refus si les chaines existantes ne suivent aucune regle.
        Renvoie (fonction longueur -> second champ, description)."""
        header, elem_type, size, _a = self.array(array_value)
        if elem_type != STRING_TYPE:
            raise EAdfEcriture('pas un tableau de chaines')
        offset, count = self.array_state(header)
        pairs = []
        for i in range(count):
            text_off, second = struct.unpack_from('<II', self.buf, offset + i * size)
            end = self.buf.find(b'\0', text_off)
            pairs.append((end - text_off, second))
        return string_rule_from(pairs)

    def new_string(self, text: bytes, second: int) -> bytes:
        """Ecrit le texte (termine par 0) a la fin de l'instance ; renvoie l'en-tete de chaine (8 octets)."""
        if b'\0' in text:
            raise EAdfEcriture('chaine contenant un octet nul')
        pos = self.append(text + b'\0', 1)
        return struct.pack('<II', pos, second)


def _exact_rule(pairs: List[Tuple[int, int]]) -> Optional[Tuple[Callable[[int], int], str]]:
    if all(s == n for n, s in pairs):
        return (lambda n: n), 'longueur'
    if all(s == n + 1 for n, s in pairs):
        return (lambda n: n + 1), 'longueur + 1'
    if len({s for _n, s in pairs}) == 1:
        c = pairs[0][1]
        return (lambda n: c), 'constante {}'.format(c)
    return None


def describe_seconds(pairs: List[Tuple[int, int]], top: int = 8) -> str:
    """Repartition du second champ : valeur x nombre (longueurs min..max des textes)."""
    by_value = {}
    for n, s in pairs:
        by_value.setdefault(s, []).append(n)
    parts = []
    for s, ns in sorted(by_value.items(), key=lambda kv: -len(kv[1]))[:top]:
        parts.append('{} x{} (longueurs {}..{})'.format(s, len(ns), min(ns), max(ns)))
    if len(by_value) > top:
        parts.append('{} autres valeurs'.format(len(by_value) - top))
    return ', '.join(parts)


def string_rule_from(pairs: List[Tuple[int, int]]) -> Tuple[Callable[[int], int], str]:
    """pairs : (longueur du texte, second champ) des chaines existantes. Regles essayees : longueur, longueur + 1,
    constante, sur toutes les chaines, puis sur les seules chaines non vides (une chaine vide peut avoir un second
    champ different ; le nouveau nom n'est jamais vide). Si les chaines ont toutes la meme longueur, longueur et
    constante sont indiscernables : la longueur l'emporte (lecture de DECA). Sinon, refus avec la repartition
    observee : ecrire une valeur au hasard pourrait donner un fichier que le jeu lit mal sans que DECA, qui ignore
    ce champ, le voie."""
    if not pairs:
        raise EAdfEcriture('aucune chaine existante : regle du second champ inconnue')
    rule = _exact_rule(pairs)
    if rule:
        return rule
    nonempty = [(n, s) for n, s in pairs if n > 0]
    if nonempty and len(nonempty) < len(pairs):
        rule = _exact_rule(nonempty)
        if rule:
            empty = sorted({s for n, s in pairs if n == 0})
            return rule[0], '{} (chaines non vides ; {} chaine(s) vide(s) : {})'.format(
                rule[1], len(pairs) - len(nonempty), ', '.join(str(s) for s in empty))
    raise EAdfEcriture('second champ des chaines sans regle (ni longueur, ni constante) sur {} chaines : {}'.format(
        len(pairs), describe_seconds(pairs)))


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
