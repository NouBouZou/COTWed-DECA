#!/usr/bin/env python3
"""
indexer_refs.py -- index de toutes les references aux objets d'equipement, dans tous les fichiers du jeu.

Pourquoi : le scan de DECA n'enregistre que les chaines trouvees dans les formats qu'il sait lire. Une table
qui designe les objets par hash (le registre qui decide de leur disponibilite, par exemple) lui echappe.
Cet index releve chaque citation d'un objet, quel que soit son encodage et le format du fichier, pour que
differentiel.py puisse ensuite comparer les objets presents en jeu et les objets absents.

Objets : tous ceux de settings/hp_settings/equipment_data.bin (toutes categories) et des
settings/spawn_*_defs.bin. Pour chaque objet, on cherche son nom, ses chaines derivees (cles de
traduction, icone, entite), sous quatre encodages : le texte, et les hash32 (Jenkins), hash48 et hash64
(murmur3) de DECA.

Lecture selon le format :
  RTPC : par structure. Chaque nom de noeud, nom de propriete, valeur u32, element de tableau u32, objid,
         event et chaine est examine. C'est necessaire, et pas seulement plus precis : une propriete RTPC
         fait 9 octets, donc la plupart des valeurs u32 inline ne sont pas alignees sur 4 octets.
  ADF, exe, fichiers sans type ou d'un format inconnu, fichiers externes (sauvegarde...) : octets bruts.
         On teste les u32 alignes sur 4 (et tous les decalages avec --non-aligne), les u64 alignes sur 8,
         et le hash48 sous la forme lue par DECA dans les ADF (trois u16, poids fort d'abord).
         Pour chaque fichier, le nombre de positions examinees est enregistre : differentiel.py en deduit
         combien de coincidences le hasard explique (positions x motifs / 2^32).
  Texte (tous formats) : les sequences qui commencent comme un identifiant connu sont comparees aux noms,
         en entier, segment par segment ('/') et par prefixes ('_', '.'). Les antislashs sont lus comme '/'.

L'index est construit une fois, dans <projet>\\cotw_refs.db, et repris la ou il s'est arrete si on relance
la commande. Il ne modifie pas la base de DECA.

Usage, depuis la racine du depot, environnement virtuel active, DECA ferme :
    python cotw\\indexer_refs.py D:\\dev\\work\\hp
    python cotw\\indexer_refs.py D:\\dev\\work\\hp --fichier "%USERPROFILE%\\Documents\\...\\sauvegarde"
"""
import argparse
import collections
import hashlib
import json
import multiprocessing
import os
import re
import sqlite3
import struct
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _projet  # noqa: E402
import cloner_munition as C  # noqa: E402

import deca.ff_rtpc_write as W  # noqa: E402
from deca.ff_rtpc import (k_type_str, k_type_u32, k_type_array_u32, k_type_objid,  # noqa: E402
                          k_type_event)
from deca.hashes import hash32_func_bytes  # noqa: E402

INDEX_NAME = 'cotw_refs.db'
SCHEMA_VERSION = 1
M32 = 0xFFFFFFFF
M48 = 0xFFFFFFFFFFFF
M64 = 0xFFFFFFFFFFFFFFFF
MAX_OFFSETS = 5
MAX_DETAILS = 3
RAW_CHUNK = 4 * 1024 * 1024  # multiple de 8 : les u32 et u64 restent alignes d'un bloc a l'autre

CONTAINER_TYPES = {'arc', 'tab', 'sarc', 'aaf', 'gt0c', 'garc', 'gdc', 'symlink', 'exe'}
MEDIA_TYPES = {'bmp', 'dds', 'avtx', 'atx', 'hmddsc', 'riff', 'FSB5c', 'ogg', 'kb2', 'bik', 'rbmdl', 'h2014',
               'tag0', 'mdi', 'pfx'}
IDENT_CHARS = rb'A-Za-z0-9_./\\-'
IDENT_RE = re.compile(rb'^[' + IDENT_CHARS + rb']{6,}$')


# ---------------------------------------------------------------------------------------------------------
# Univers : les objets et les chaines qui les designent
# ---------------------------------------------------------------------------------------------------------

@dataclass
class Ident:
    id: int
    item: str          # nom de l'objet (ou hash en hexadecimal s'il n'a pas de nom)
    category: str
    role: str          # nom, display_name, display_icon, entite...
    text: str          # chaine, chemins avec '/'


def normalize(text: str) -> str:
    return text.replace('\\', '/')


def is_identifier(s: bytes) -> bool:
    return bool(IDENT_RE.match(s)) and (b'_' in s or b'/' in s or b'\\' in s)


def build_universe(vfs, base: Optional[Path] = None) -> List[Ident]:
    """Objets d'equipment_data.bin (et chaines derivees), plus les entites declarees dans les spawn defs."""
    idents: List[Ident] = []
    seen: Set[Tuple[str, str]] = set()

    def add(item, category, role, text):
        text = normalize(text)
        if (item, text) in seen:
            return
        seen.add((item, text))
        idents.append(Ident(len(idents) + 1, item, category, role, text))

    data, _src = C.load(vfs, base, C.EQUIPMENT_DATA)
    tree = W.rtpc_from_bytes(data)
    hashes = set()

    def gather(node):
        hashes.add(node.name_hash)
        for p in node.prop_table:
            hashes.add(p.name_hash)
        for c in node.child_table:
            gather(c)
    gather(tree.root_node)
    names = _projet.resolve_hash32(vfs, hashes)

    def label(h):
        n = names.get(h & M32)
        return n[0] if n else '0x{:08x}'.format(h & M32)

    items = {}

    def walk(node, category):
        name_prop = W.node_get_prop(node, C.H_NAME)
        if name_prop is not None and name_prop.type == k_type_str:
            item = bytes(name_prop.data).decode('utf-8', 'replace')
            items[item] = category
            add(item, category, 'nom', item)
            for p in node.prop_table:
                if p.type == k_type_str and p.name_hash != C.H_NAME:
                    value = bytes(p.data)
                    if is_identifier(value) and value.decode('utf-8', 'replace') != item:
                        add(item, category, label(p.name_hash), value.decode('utf-8', 'replace'))
        for c in node.child_table:
            walk(c, category)

    for cat in tree.root_node.child_table:
        walk(cat, label(cat.name_hash))

    spawn_paths = sorted({_projet.to_text(r[0]) for r in vfs.db_query_all(
        "SELECT DISTINCT v_path FROM core_nodes WHERE v_path LIKE 'settings/spawn_%_defs.bin'", [],
        dbg='cotw_index_spawn')})
    for v in spawn_paths:
        try:
            sdata, _ = C.load(vfs, base, v)
        except SystemExit:
            continue
        if sdata[:4] != W.RTPC_MAGIC:
            continue
        stree = W.rtpc_from_bytes(sdata)
        category = 'spawn:' + Path(v).stem

        def walk_spawn(node):
            name_prop = W.node_get_prop(node, C.H_SPAWN_NAME)
            if name_prop is not None and name_prop.type == k_type_str:
                item = bytes(name_prop.data).decode('utf-8', 'replace')
                add(item, items.get(item, category), 'nom', item)
                ent = W.node_get_prop(node, C.H_ENTITY)
                if ent is not None and ent.type == k_type_str and is_identifier(bytes(ent.data)):
                    add(item, items.get(item, category), 'entite', bytes(ent.data).decode('utf-8', 'replace'))
            for c in node.child_table:
                walk_spawn(c)
        walk_spawn(stree.root_node)
    return idents


def hash_variants(text: str) -> List[bytes]:
    """Formes hachees : la chaine, et pour un chemin ses deux separateurs."""
    b = text.encode('utf-8')
    out = [b]
    if b'/' in b:
        out.append(b.replace(b'/', b'\\'))
    return out


# ---------------------------------------------------------------------------------------------------------
# Recherche dans un fichier
# ---------------------------------------------------------------------------------------------------------

class Matcher:
    """Tables de recherche construites a partir de l'univers. Construit une fois par processus."""

    def __init__(self, idents: Sequence[Ident]):
        import mmh3
        self.text: Dict[bytes, List[int]] = collections.defaultdict(list)
        h32: Dict[int, List[int]] = collections.defaultdict(list)
        h48: Dict[int, List[int]] = collections.defaultdict(list)
        h64: Dict[int, List[int]] = collections.defaultdict(list)
        for ident in idents:
            self.text[ident.text.encode('utf-8')].append(ident.id)
            for b in hash_variants(ident.text):
                v32 = int(hash32_func_bytes(b)) & M32
                if ident.id not in h32[v32]:
                    h32[v32].append(ident.id)
                v = mmh3.hash128(b, x64arch=True)
                for d, hv in ((h48, (v >> 16) & M48), (h64, v & M64)):
                    if ident.id not in d[hv]:
                        d[hv].append(ident.id)
        self.h32, self.h48, self.h64 = dict(h32), dict(h48), dict(h64)
        self.k32 = np.array(sorted(self.h32), dtype=np.uint32)
        self.k48 = np.array(sorted(self.h48), dtype=np.uint64)
        self.k64 = np.array(sorted(self.h64), dtype=np.uint64)
        # Prefiltre : table de 2^24 booleens indexee par les 24 bits de poids fort de chaque hash. Une lecture
        # de tableau par position ecarte presque tout (taux de faux candidats ~ motifs / 2^24) avant la
        # comparaison exacte, bien plus couteuse : x5 a x10 sur les gros fichiers.
        self.f32 = self._prefilter(self.k32 >> np.uint32(8))
        self.f48 = self._prefilter(self.k48 >> np.uint64(24))
        self.f64 = self._prefilter(self.k64 >> np.uint64(40))
        prefixes = sorted({t[:4] for t in self.text if len(t) >= 4})
        self.text_re = re.compile(b'(?:' + b'|'.join(re.escape(p) for p in prefixes) + b')[' + IDENT_CHARS + b']*') \
            if prefixes else None
        self.sizes = {'texte': len(self.text), 'hash32': len(self.k32), 'hash48': len(self.k48), 'hash64': len(self.k64)}

    # -- texte --------------------------------------------------------------------------------------------
    def text_candidates(self, run: bytes) -> Set[bytes]:
        run = run.replace(b'\\', b'/').rstrip(b'.-/')
        out = {run}
        for segment in [run] + run.split(b'/'):
            stem = segment.split(b'.')[0]
            out.add(segment)
            out.add(stem)
            for i, ch in enumerate(stem):
                if ch == 0x5F:  # '_'
                    out.add(stem[:i])
        return {c for c in out if c in self.text}

    def match_text(self, data: bytes, hits):
        if self.text_re is None:
            return
        for m in self.text_re.finditer(data):
            for c in self.text_candidates(m.group(0)):
                for ident_id in self.text[c]:
                    hits.add(ident_id, 'texte', m.start(), None)

    # -- hashes dans des octets bruts ----------------------------------------------------------------------
    @staticmethod
    def _prefilter(top: np.ndarray) -> np.ndarray:
        table = np.zeros(1 << 24, dtype=bool)
        table[top.astype(np.int64)] = True
        return table

    @staticmethod
    def _find(values: np.ndarray, keys: np.ndarray, table: Optional[np.ndarray] = None,
              shift: int = 0) -> np.ndarray:
        """Indices des valeurs presentes dans keys (trie). table/shift : prefiltre sur les bits de poids fort."""
        if not len(keys) or not len(values):
            return np.zeros(0, dtype=np.int64)
        if table is not None:
            idx = np.nonzero(table[(values >> values.dtype.type(shift)).astype(np.int64)])[0]
            if not len(idx):
                return idx
            sub = values[idx]
        else:
            idx, sub = None, values
        pos = np.searchsorted(keys, sub)
        pos[pos >= len(keys)] = 0
        match = np.nonzero(keys[pos] == sub)[0]
        return match if idx is None else idx[match]

    def match_raw(self, data: bytes, hits, unaligned: bool = False) -> Dict[str, int]:
        """Hashes dans des octets bruts, par blocs de RAW_CHUNK octets pour borner la memoire (exe).
        Renvoie le nombre de positions examinees par largeur."""
        counts = {'n32': 0, 'n48': 0, 'n64': 0}
        n = len(data)
        for start in range(0, n, RAW_CHUNK):
            self._raw_chunk(data, start, min(start + RAW_CHUNK, n), n, hits, unaligned, counts)
        return counts

    @staticmethod
    def _positions(first: int, end: int, n: int, size: int, step: int) -> int:
        """Nombre de positions p = first + k*step avec p < end et p + size <= n."""
        last = min(end - 1, n - size)
        return (last - first) // step + 1 if last >= first else 0

    def _raw_chunk(self, data, start, end, n, hits, unaligned, counts):
        for shift in (range(4) if unaligned else (0,)):
            first = start + shift
            count = self._positions(first, end, n, 4, 4)
            if count:
                arr = np.frombuffer(data, dtype='<u4', count=count, offset=first)
                counts['n32'] += count
                for i in self._find(arr, self.k32, self.f32, 8):
                    for ident_id in self.h32[int(arr[i])]:
                        hits.add(ident_id, 'hash32', first + int(i) * 4, None)
        count = self._positions(start, end, n, 8, 8)
        if count:
            arr = np.frombuffer(data, dtype='<u8', count=count, offset=start)
            counts['n64'] += count
            counts['n48'] += count
            for i in self._find(arr, self.k64, self.f64, 40):
                for ident_id in self.h64[int(arr[i])]:
                    hits.add(ident_id, 'hash64', start + int(i) * 8, None)
            low = arr & np.uint64(M48)
            for i in self._find(low, self.k48, self.f48, 24):
                for ident_id in self.h48[int(low[i])]:
                    hits.add(ident_id, 'hash48', start + int(i) * 8, 'u64 (48 bits de poids faible)')
        count = self._positions(start, end, n, 6, 2)
        if count and len(self.k48):
            a = np.frombuffer(data, dtype='<u2', count=count + 2, offset=start).astype(np.uint64)
            combined = (a[:-2] << np.uint64(32)) | (a[1:-1] << np.uint64(16)) | a[2:]
            counts['n48'] += count
            for i in self._find(combined, self.k48, self.f48, 24):
                for ident_id in self.h48[int(combined[i])]:
                    hits.add(ident_id, 'hash48', start + int(i) * 2, '3 x u16, poids fort d\'abord (lecture DECA)')

    # -- RTPC, par structure --------------------------------------------------------------------------------
    def match_rtpc(self, tree, hits) -> Dict[str, int]:
        counts = {'n32': 0, 'n48': 0, 'n64': 0}

        def check32(v, detail):
            counts['n32'] += 1
            for ident_id in self.h32.get(v & M32, ()):
                hits.add(ident_id, 'hash32', None, detail)

        def check64(v, detail):
            counts['n64'] += 1
            counts['n48'] += 1
            for ident_id in self.h64.get(v & M64, ()):
                hits.add(ident_id, 'hash64', None, detail)
            for ident_id in self.h48.get(v & M48, ()):
                hits.add(ident_id, 'hash48', None, detail)

        def walk(node, path, parent_hash):
            check32(node.name_hash, '{} : hash du noeud (parent 0x{:08x})'.format(path, parent_hash & M32))
            for p in node.prop_table:
                where = '{} : propriete 0x{:08x}'.format(path, p.name_hash & M32)
                check32(p.name_hash, where + ' (nom de la propriete)')
                if p.type == k_type_u32:
                    check32(p.data, where + ' (valeur u32)')
                elif p.type == k_type_array_u32:
                    for i, v in enumerate(p.data):
                        check32(v, '{} (tableau u32, element {})'.format(where, i))
                elif p.type == k_type_objid:
                    check64(p.data, where + ' (objid)')
                elif p.type == k_type_event:
                    for i, v in enumerate(p.data):
                        check64(v, '{} (event, element {})'.format(where, i))
                elif p.type == k_type_str and self.text_re is not None:
                    value = bytes(p.data)
                    for m in self.text_re.finditer(value):
                        for c in self.text_candidates(m.group(0)):
                            for ident_id in self.text[c]:
                                hits.add(ident_id, 'texte', None, where + ' (chaine)')
            for i, c in enumerate(node.child_table):
                walk(c, '{}.{}'.format(path, i), node.name_hash)

        walk(tree.root_node, '0', 0)
        return counts


class FileHits:
    """Citations trouvees dans un fichier : (identifiant, encodage) -> nombre, offsets, details."""

    def __init__(self):
        self.data: Dict[Tuple[int, str], list] = {}

    def add(self, ident_id: int, encoding: str, offset: Optional[int], detail: Optional[str]):
        rec = self.data.get((ident_id, encoding))
        if rec is None:
            rec = self.data[(ident_id, encoding)] = [0, [], []]
        rec[0] += 1
        if offset is not None and len(rec[1]) < MAX_OFFSETS:
            rec[1].append(offset)
        if detail is not None and len(rec[2]) < MAX_DETAILS and detail not in rec[2]:
            rec[2].append(detail)

    def rows(self):
        for (ident_id, encoding), (count, offsets, details) in self.data.items():
            yield ident_id, encoding, count, json.dumps(offsets), json.dumps(details, ensure_ascii=False)


def scan_bytes(matcher: Matcher, data: bytes, kind: str, unaligned: bool) -> Tuple[str, FileHits, Dict[str, int]]:
    """kind : 'rtpc' (essai par structure, repli en brut si illisible) ou 'brut'."""
    hits = FileHits()
    if kind == 'rtpc' and data[:4] == W.RTPC_MAGIC:
        try:
            tree = W.rtpc_from_bytes(data)
        except Exception:
            tree = None
        if tree is not None:
            return 'rtpc', hits, matcher.match_rtpc(tree, hits)
    matcher.match_text(data, hits)
    return 'brut', hits, matcher.match_raw(data, hits, unaligned)


# ---------------------------------------------------------------------------------------------------------
# Base de l'index
# ---------------------------------------------------------------------------------------------------------

SCHEMA = '''
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS identifiers (id INTEGER PRIMARY KEY, item TEXT, category TEXT, role TEXT, text TEXT);
CREATE TABLE IF NOT EXISTS files (
    file_id INTEGER PRIMARY KEY, source TEXT UNIQUE, node_id INTEGER, label TEXT, file_type TEXT, size INTEGER,
    mode TEXT, status TEXT, error TEXT, n32 INTEGER, n48 INTEGER, n64 INTEGER);
CREATE TABLE IF NOT EXISTS hits (
    file_id INTEGER, ident_id INTEGER, encoding TEXT, count INTEGER, offsets TEXT, details TEXT,
    PRIMARY KEY (file_id, ident_id, encoding));
CREATE INDEX IF NOT EXISTS hits_ident ON hits (ident_id);
'''


def universe_digest(idents: Sequence[Ident]) -> str:
    h = hashlib.sha1()
    for i in idents:
        h.update('{}\t{}\t{}\t{}\n'.format(i.item, i.category, i.role, i.text).encode('utf-8'))
    return h.hexdigest()


def open_index(path: Path, idents: Sequence[Ident], options: dict, rebuild: bool) -> sqlite3.Connection:
    if rebuild and path.exists():
        path.unlink()
    db = sqlite3.connect(str(path))
    db.executescript(SCHEMA)
    meta = dict(db.execute('SELECT key, value FROM meta').fetchall())
    digest = universe_digest(idents)
    if meta:
        if meta.get('universe') != digest or meta.get('options') != json.dumps(options, sort_keys=True):
            db.close()
            raise SystemExit('ERREUR : {} a ete construit avec d\'autres objets ou d\'autres options. '
                             'Relancer avec --reconstruire pour repartir de zero.'.format(path))
        return db
    db.executemany('INSERT INTO identifiers VALUES (?,?,?,?,?)',
                   [(i.id, i.item, i.category, i.role, i.text) for i in idents])
    db.executemany('INSERT INTO meta VALUES (?,?)', [
        ('schema', str(SCHEMA_VERSION)), ('universe', digest), ('options', json.dumps(options, sort_keys=True)),
        ('created', time.strftime('%Y-%m-%d %H:%M:%S'))])
    db.commit()
    return db


# ---------------------------------------------------------------------------------------------------------
# Selection des fichiers
# ---------------------------------------------------------------------------------------------------------

@dataclass
class Job:
    source: str               # 'node:<id>' ou 'fichier:<chemin>'
    node_id: Optional[int]
    path: Optional[str]
    label: str
    file_type: Optional[str]
    size: int


def select_jobs(vfs, scope: str, max_size: int, max_untyped: int, types: Optional[Set[str]],
                with_exe: bool, extra_files: List[str]) -> List[Job]:
    rows = vfs.db_query_all(
        'SELECT node_id, v_hash, v_path, file_type, size_u FROM core_nodes '
        'WHERE parent_offset IS NOT NULL AND size_u > 0 ORDER BY node_id', [], dbg='cotw_index_select')
    jobs, seen = [], set()
    for node_id, v_hash, v_path, file_type, size in rows:
        key = ('h', v_hash) if v_hash is not None else ('n', node_id)
        if key in seen:
            continue
        if file_type in CONTAINER_TYPES or size > max_size:
            continue
        if types is not None:
            if (file_type or 'sans-type') not in types:
                continue
        else:
            if scope == 'donnees' and file_type in MEDIA_TYPES:
                continue
            if file_type is None and size > max_untyped:
                continue
        seen.add(key)
        label = _projet.to_text(v_path) or ('(sans nom) {:08x}'.format(v_hash & M32) if v_hash is not None
                                            else '(noeud {})'.format(node_id))
        jobs.append(Job('node:{}'.format(node_id), node_id, None, label, file_type, size))
    paths = []
    if with_exe:
        paths += [(p, 'exe') for p in _scan_exe_paths(vfs)]
    paths += [(os.path.expandvars(p), 'externe') for p in extra_files]
    for p, kind in paths:
        if Path(p).is_file():
            jobs.append(Job('fichier:{}'.format(Path(p).resolve()), None, str(Path(p).resolve()),
                            '[{}] {}'.format(kind, Path(p).name), kind, Path(p).stat().st_size))
        else:
            print('ATTENTION : fichier introuvable, ignore : {}'.format(p))
    return jobs


def _scan_exe_paths(vfs) -> List[str]:
    rows = vfs.db_query_all("SELECT DISTINCT p_path FROM core_nodes WHERE file_type = 'exe'", [],
                            dbg='cotw_index_exe')
    return [_projet.to_text(r[0]) for r in rows if r[0]]


# ---------------------------------------------------------------------------------------------------------
# Processus de travail
# ---------------------------------------------------------------------------------------------------------

_W = {}


def _worker_init(project_dir: str, idents_payload, unaligned: bool):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(errors='replace')
    _W['vfs'] = _projet.open_project(project_dir)
    _W['matcher'] = Matcher([Ident(*t) for t in idents_payload])
    _W['unaligned'] = unaligned


def _worker_scan(job: Job):
    try:
        if job.path is not None:
            data = Path(job.path).read_bytes()
        else:
            node = _W['vfs'].node_where_uid(job.node_id)
            data = _projet.read_node(_W['vfs'], node)
        mode, hits, counts = scan_bytes(_W['matcher'], data, 'rtpc' if job.file_type == 'rtpc' else 'brut',
                                        _W['unaligned'])
        return job, 'ok', None, mode, list(hits.rows()), counts
    except Exception as e:
        return job, 'erreur', '{}: {}'.format(type(e).__name__, e), None, [], {'n32': 0, 'n48': 0, 'n64': 0}


def run_index(project_dir: str, db: sqlite3.Connection, idents: Sequence[Ident], jobs: List[Job],
              processes: int, unaligned: bool, commit_every: int = 500):
    done = {r[0] for r in db.execute("SELECT source FROM files WHERE status = 'ok'")}
    todo = [j for j in jobs if j.source not in done]
    print('{} fichiers a lire ({} deja indexes)'.format(len(todo), len(jobs) - len(todo)))
    if not todo:
        return
    payload = [(i.id, i.item, i.category, i.role, i.text) for i in idents]
    total_bytes = sum(j.size for j in todo)
    t0, last, read_bytes, n = time.time(), 0.0, 0, 0

    def store(result):
        job, status, error, mode, rows, counts = result
        db.execute(
            'INSERT INTO files (source, node_id, label, file_type, size, mode, status, error, n32, n48, n64) '
            'VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(source) DO UPDATE SET mode=excluded.mode, '
            'status=excluded.status, error=excluded.error, n32=excluded.n32, n48=excluded.n48, n64=excluded.n64',
            (job.source, job.node_id, job.label, job.file_type, job.size, mode, status, error,
             counts['n32'], counts['n48'], counts['n64']))
        file_id = db.execute('SELECT file_id FROM files WHERE source = ?', (job.source,)).fetchone()[0]
        db.execute('DELETE FROM hits WHERE file_id = ?', (file_id,))
        db.executemany('INSERT INTO hits VALUES (?,?,?,?,?,?)', [(file_id,) + r for r in rows])

    if processes <= 1:
        _worker_init(project_dir, payload, unaligned)
        results = map(_worker_scan, todo)
        pool = None
    else:
        pool = multiprocessing.Pool(processes, initializer=_worker_init, initargs=(project_dir, payload, unaligned))
        results = pool.imap_unordered(_worker_scan, todo, chunksize=16)
    try:
        for result in results:
            store(result)
            n += 1
            read_bytes += result[0].size
            if n % commit_every == 0:
                db.commit()
            now = time.time()
            if now - last > 10 or n == len(todo):
                last = now
                rate = read_bytes / max(now - t0, 1e-6)
                eta = (total_bytes - read_bytes) / rate if rate else 0
                print('  {}/{} fichiers, {:.0f}/{:.0f} Mo, {:.0f} s ecoulees, reste ~{:.0f} s'.format(
                    n, len(todo), read_bytes / 1e6, total_bytes / 1e6, now - t0, eta))
    finally:
        db.commit()
        if pool is not None:
            pool.close()
            pool.join()


def summary(db: sqlite3.Connection, top: int = 15) -> List[str]:
    L = []
    n_files, n_err = db.execute("SELECT COUNT(*), SUM(status = 'erreur') FROM files").fetchone()
    L.append('{} fichiers indexes, {} en erreur'.format(n_files, n_err or 0))
    rows = db.execute(
        'SELECT f.label, f.file_type, COUNT(DISTINCT i.item) FROM hits h JOIN files f ON f.file_id = h.file_id '
        'JOIN identifiers i ON i.id = h.ident_id GROUP BY h.file_id ORDER BY 3 DESC LIMIT ?', (top,)).fetchall()
    L.append('Fichiers qui citent le plus d\'objets distincts :')
    for label, ftype, k in rows:
        L.append('  {:>6}  {:<8} {}'.format(k, ftype or '-', label))
    return L


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(errors='replace')
    ap = argparse.ArgumentParser(description='Index de toutes les references aux objets d\'equipement.')
    ap.add_argument('projet', help='projet DECA (dossier contenant db\\core.db)')
    ap.add_argument('--index', help='fichier de l\'index (defaut : <projet>\\cotw_refs.db)')
    ap.add_argument('--portee', choices=['donnees', 'tout'], default='donnees',
                    help='donnees (defaut) : tout sauf textures, sons, videos et modeles ; tout : tous les fichiers')
    ap.add_argument('--types', help='liste de types a lire, separes par des virgules (sans-type pour les fichiers '
                                    'sans type) ; remplace --portee')
    ap.add_argument('--taille-max', type=float, default=32, help='taille maximale d\'un fichier lu, en Mo (defaut 32)')
    ap.add_argument('--taille-max-sans-type', type=float, default=512,
                    help='taille maximale d\'un fichier sans type, en Ko (defaut 512) : ce sont surtout des textures')
    ap.add_argument('--non-aligne', action='store_true', help='tester les u32 a tous les decalages (4 fois plus de bruit)')
    ap.add_argument('--sans-exe', action='store_true', help='ne pas lire l\'exe du jeu')
    ap.add_argument('--fichier', action='append', default=[], help='fichier externe a lire aussi (sauvegarde, profil...)')
    ap.add_argument('--processus', type=int, default=max(1, min(8, (os.cpu_count() or 2) - 1)),
                    help='nombre de processus de lecture (defaut : coeurs - 1, 8 au plus)')
    ap.add_argument('--base', help='dossier au format dropzone dont lire equipment_data et les spawn defs')
    ap.add_argument('--reconstruire', action='store_true', help='effacer l\'index existant et repartir de zero')
    ap.add_argument('--limite', type=int, help='ne lire que les N premiers fichiers (essai)')
    args = ap.parse_args(argv)

    project_dir = str(Path(args.projet).expanduser().resolve())
    vfs = _projet.open_project(project_dir)
    base = Path(args.base).expanduser().resolve() if args.base else None
    idents = build_universe(vfs, base)
    n_items = len({i.item for i in idents})
    print('{} objets, {} chaines a chercher'.format(n_items, len(idents)))

    types = {t.strip() for t in args.types.split(',')} if args.types else None
    options = {'portee': args.portee, 'types': sorted(types) if types else None, 'taille_max': args.taille_max,
               'taille_max_sans_type': args.taille_max_sans_type, 'non_aligne': args.non_aligne,
               'exe': not args.sans_exe, 'fichiers': sorted(args.fichier)}
    index_path = Path(args.index) if args.index else Path(project_dir) / INDEX_NAME
    db = open_index(index_path, idents, options, args.reconstruire)
    matcher_sizes = Matcher(idents).sizes
    db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', ('motifs', json.dumps(matcher_sizes)))
    db.commit()

    jobs = select_jobs(vfs, args.portee, int(args.taille_max * 1024 * 1024), int(args.taille_max_sans_type * 1024),
                       types, not args.sans_exe, args.fichier)
    if args.limite:
        jobs = jobs[:args.limite]
    vfs.db_conn.close()
    run_index(project_dir, db, idents, jobs, args.processus, args.non_aligne)
    for line in summary(db):
        print(line)
    db.close()
    print('Index : {}'.format(index_path))
    return 0


if __name__ == '__main__':
    multiprocessing.freeze_support()
    sys.exit(main())
