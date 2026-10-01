"""
Lecture et ecriture des archives SARC (.ee, .bl, .nl...) de COTW, sur la base de deca.ff_sarc.

DECA sait deja lire et ecrire l'en-tete d'un SARC (FileSarc.header_deserialize / header_serialize) ;
son constructeur (deca/builder.py) relit cependant le contenu des entrees dans la base et refuse un chemin
qu'elle ne connait pas. Ce module travaille directement sur les octets d'un SARC : on peut donc fabriquer
une archive dont les entrees portent des noms nouveaux (l'entite d'un objet ajoute).

Format (deca.ff_sarc) :
  0x00  u32 version (4), 'SARC', u32 sous-version (2 ou 3), u32 longueur du bloc de repertoire
  v2 :  entrees a la suite : u32 longueur du chemin (multiple de 4), chemin complete par des 0, u32 offset,
        u32 taille
  v3 :  u32 longueur de la table des chemins, table, puis entrees de 20 octets : offset du chemin dans la
        table, offset, taille, hash32 du chemin, hash32 de l'extension
  donnees : a partir de 16 + longueur du bloc, chaque entree alignee sur 4, sans franchir de frontiere de
        32 Mo. Une entree d'offset 0 est un lien (le fichier est ailleurs, sa taille est donnee).

La disposition de deca.ff_sarc n'est pas garantie identique a celle du jeu : sarc_roundtrip.py la compare
a l'octet pres sur tous les SARC d'un projet avant qu'on s'en serve pour produire des fichiers.
"""
import io
import sys
from dataclasses import dataclass
from typing import List, Optional, Tuple

if not sys.platform.startswith('win'):
    import ctypes
    sys.modules.setdefault('zugbruecke.ctypes', ctypes)

from deca.file import ArchiveFile  # noqa: E402
from deca.ff_sarc import EntrySarc, FileSarc  # noqa: E402

HEADER = 16


class ESarc(Exception):
    pass


@dataclass
class Entry:
    path: str
    data: Optional[bytes]     # None pour un lien
    length: int               # taille des donnees (ou du fichier lie)
    offset: int = 0           # offset dans l'archive lue (0 pour un lien)

    @property
    def is_link(self) -> bool:
        return self.data is None


@dataclass
class Sarc:
    version: int              # sous-version : 2 ou 3
    entries: List[Entry]
    dir_block_len: int = 0


def parse(data: bytes) -> Sarc:
    if len(data) < HEADER or data[4:8] != b'SARC':
        raise ESarc('pas un SARC (magic "SARC" absent)')
    raw = FileSarc()
    try:
        raw.header_deserialize(io.BytesIO(data))
    except AssertionError as e:
        raise ESarc('en-tete SARC refuse par DECA ({})'.format(e or 'assertion'))
    entries = []
    for e in raw.entries:
        path = e.v_path.decode('utf-8', 'replace') if isinstance(e.v_path, bytes) else str(e.v_path)
        if e.is_symlink:
            entries.append(Entry(path, None, e.length, 0))
        else:
            if e.offset + e.length > len(data):
                raise ESarc('entree {} hors du fichier'.format(path))
            entries.append(Entry(path, data[e.offset:e.offset + e.length], e.length, e.offset))
    return Sarc(raw.ver2, entries, raw.dir_block_len)


def build(sarc: Sarc) -> Tuple[bytes, List[int]]:
    """Octets de l'archive (disposition de deca.ff_sarc) et offset de chaque entree."""
    raw = FileSarc()
    raw.version, raw.magic, raw.ver2 = 4, b'SARC', sarc.version
    raw.entries = []
    for i, e in enumerate(sarc.entries):
        es = EntrySarc(index=i, v_path=e.path.encode('utf-8'))
        es.is_symlink = e.is_link
        es.length = e.length if e.is_link else len(e.data)
        raw.entries.append(es)
    out = io.BytesIO()
    raw.header_serialize(ArchiveFile(out))  # pas de "with" : ArchiveFile fermerait le BytesIO
    buf = bytearray(out.getvalue())
    for e, es in zip(sarc.entries, raw.entries):
        if e.is_link:
            continue
        if len(buf) < es.offset:
            buf += b'\0' * (es.offset - len(buf))
        buf[es.offset:es.offset + len(e.data)] = e.data
    return bytes(buf), [es.offset for es in raw.entries]


@dataclass
class RoundTrip:
    identical: bool
    category: str             # identique, decalage, fin, en-tete, donnees
    detail: str
    version: int
    entries: int
    links: int


def roundtrip(data: bytes) -> RoundTrip:
    s = parse(data)
    rebuilt, offsets = build(s)
    links = sum(1 for e in s.entries if e.is_link)
    if rebuilt == data:
        return RoundTrip(True, 'identique', '', s.version, len(s.entries), links)
    for e, off in zip(s.entries, offsets):
        if not e.is_link and e.offset != off:
            return RoundTrip(False, 'decalage', '{} : offset original 0x{:x}, reecrit 0x{:x} (original multiple de {})'.format(
                e.path, e.offset, off, largest_pow2_divisor(e.offset)), s.version, len(s.entries), links)
    common = min(len(data), len(rebuilt))
    first = next((i for i in range(common) if data[i] != rebuilt[i]), None)
    if first is None:
        tail = data[common:] if len(data) > len(rebuilt) else rebuilt[common:]
        who = 'original' if len(data) > len(rebuilt) else 'reecriture'
        values = sorted(set(tail))
        return RoundTrip(False, 'fin', '{} plus long de {} octets (valeurs {}) ; original multiple de {}'.format(
            who, len(tail), values[:4], largest_pow2_divisor(len(data))), s.version, len(s.entries), links)
    data_start = HEADER + s.dir_block_len
    category = 'en-tete' if first < data_start else 'donnees'
    return RoundTrip(False, category, 'premier ecart a 0x{:x} : original {:02x}, reecrit {:02x}'.format(
        first, data[first], rebuilt[first]), s.version, len(s.entries), links)


def largest_pow2_divisor(v: int) -> int:
    if v == 0:
        return 0
    p = 1
    while v % (p * 2) == 0 and p < 1 << 20:
        p *= 2
    return p
