#!/usr/bin/env python3
"""
deca_refs.py -- tout ce qui, dans le jeu, mentionne un objet (ex : equipment_ammo_243_sp_01).

Trois sources, de la plus rapide a la plus complete :
  1. chemins : fichiers dont le chemin virtuel contient le terme ;
  2. chaines : chaines trouvees par le scan DECA qui contiennent le terme, et les fichiers ou elles
     apparaissent (table core_string_references) ;
  3. octets (option --octets) : recherche directe dans le contenu des fichiers (RTPC et ADF par defaut)
     du terme en clair ET de son hash32, ainsi que du hash32 de chaque chemin et chaine trouves aux
     etapes 1 et 2. C'est la seule facon de voir les references PAR HASH, que le scan n'enregistre pas.

Usage, depuis la racine du depot, environnement virtuel active, DECA ferme :
    python cotw\\deca_refs.py D:\\dev\\work\\hp equipment_ammo_243_sp_01 --out D:\\dev\\audits\\refs_243.txt
    python cotw\\deca_refs.py D:\\dev\\work\\hp equipment_ammo_243_sp_01 --octets --out D:\\dev\\audits\\refs_243_octets.txt
"""
import argparse
import collections
import re
import struct
import sys
import time
from pathlib import Path
from typing import Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _projet  # noqa: E402

from deca.hashes import hash32_func  # noqa: E402

MAX_STRINGS = 300
MAX_FILES_PER_STRING = 50


def h32(text: str) -> int:
    return int(hash32_func(text.encode('utf-8')))


def find_paths(vfs, term: str) -> List[Tuple[str, str, int, int]]:
    """(chemin, type, taille, nombre de copies) des fichiers dont le chemin contient le terme."""
    rows = vfs.db_query_all(
        'SELECT v_path, file_type, size_u, COUNT(*) FROM core_nodes WHERE v_path LIKE ? '
        'GROUP BY v_path, file_type, size_u ORDER BY v_path',
        ['%' + term + '%'], dbg='cotw_refs_paths')
    return [(_projet.to_text(p), t or '(aucun type)', s or 0, n) for p, t, s, n in rows]


def find_strings(vfs, term: str) -> Dict[str, List[Tuple[str, str]]]:
    """Chaine -> [(fichier, type)] des fichiers ou le scan a trouve la chaine."""
    strings = vfs.db_query_all(
        'SELECT rowid, string FROM core_strings WHERE string LIKE ? LIMIT ?',
        ['%' + term + '%', MAX_STRINGS], dbg='cotw_refs_strings')
    result: Dict[str, List[Tuple[str, str]]] = collections.OrderedDict()
    for rowid, s in strings:
        refs = vfs.db_query_all(
            'SELECT DISTINCT n.v_path, n.v_hash, n.file_type FROM core_string_references r '
            'JOIN core_nodes n ON n.node_id = r.node_id_src WHERE r.string_rowid = ? LIMIT ?',
            [rowid, MAX_FILES_PER_STRING], dbg='cotw_refs_string_refs')
        files = []
        for v_path, v_hash, ftype in refs:
            label = _projet.to_text(v_path) or ('(sans nom) {:08x}'.format(v_hash & 0xFFFFFFFF) if v_hash is not None
                                                else '(fichier du jeu)')
            files.append((label, ftype or '(aucun type)'))
        result[_projet.to_text(s)] = files
    return result


def build_patterns(term: str, paths, strings) -> Dict[bytes, str]:
    """Motifs binaires a chercher -> description."""
    patterns: Dict[bytes, str] = {}
    patterns[term.encode('utf-8')] = 'texte "{}"'.format(term)
    patterns[struct.pack('<I', h32(term))] = 'hash32 de "{}" (0x{:08x})'.format(term, h32(term))
    for text in [p for p, _, _, _ in paths] + list(strings.keys()):
        if text and text != term:
            patterns.setdefault(struct.pack('<I', h32(text)), 'hash32 de "{}" (0x{:08x})'.format(text, h32(text)))
    return patterns


def scan_contents(vfs, patterns: Dict[bytes, str], types: List[str], max_size: int):
    """Cherche les motifs dans le contenu des fichiers (une copie par hash). Renvoie la liste des trouvailles."""
    rx = re.compile(b'|'.join(re.escape(p) for p in sorted(patterns, key=len, reverse=True)))
    hash_patterns = {p for p, desc in patterns.items() if desc.startswith('hash32')}
    rows = vfs.db_query_all(
        'SELECT node_id, v_hash, size_u FROM core_nodes WHERE file_type IN ({}) ORDER BY node_id'.format(
            ','.join('?' * len(types))), types, dbg='cotw_refs_scan')
    seen, selected = set(), []
    for node_id, v_hash, size in rows:
        key = v_hash if v_hash is not None else ('uid', node_id)
        if key in seen or not size or size > max_size:
            continue
        seen.add(key)
        selected.append(node_id)
    hits = []
    errors = 0
    t0 = time.time()
    for i, node_id in enumerate(selected, 1):
        node = vfs.node_where_uid(node_id)
        try:
            data = _projet.read_node(vfs, node)
        except Exception:
            errors += 1
            continue
        found = collections.defaultdict(list)
        for m in rx.finditer(data):
            # Un hash32 stocke dans une structure (RTPC, ADF) est aligne sur 4 octets. Une correspondance
            # non alignee est presque toujours une coincidence dans des donnees binaires (maillages,
            # terrain) : sur le premier essai COTW, la plupart des faux positifs etaient non alignes.
            if m.group(0) in hash_patterns and m.start() % 4:
                continue
            found[m.group(0)].append(m.start())
        if found:
            hits.append((_projet.node_label(node), node.file_type, dict(found)))
        if i % 2000 == 0 or i == len(selected):
            print('  {}/{} fichiers lus, {} avec correspondance ({:.0f} s)'.format(
                i, len(selected), len(hits), time.time() - t0))
    return hits, len(selected), errors


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(errors='replace')
    ap = argparse.ArgumentParser(description='Tout ce qui mentionne un objet dans le jeu.')
    ap.add_argument('projet', help='projet DECA (dossier contenant db\\core.db)')
    ap.add_argument('terme', help='texte a chercher, ex : equipment_ammo_243_sp_01')
    ap.add_argument('--octets', action='store_true', help='chercher aussi dans le contenu des fichiers (plus lent)')
    ap.add_argument('--types', default='rtpc,adf', help='types de fichiers lus par --octets (defaut : rtpc,adf)')
    ap.add_argument('--taille-max', type=float, default=16, help='taille maximale lue par --octets, en Mo (defaut 16)')
    ap.add_argument('--out', help='fichier texte de sortie (sinon : affichage a l\'ecran)')
    args = ap.parse_args(argv)

    vfs = _projet.open_project(args.projet)
    term = args.terme
    L = ['# References a "{}"  (hash32 = 0x{:08x})'.format(term, h32(term)), '']

    paths = find_paths(vfs, term)
    L.append('## 1. Chemins contenant le terme : {}'.format(len(paths)))
    for p, t, s, n in paths:
        L.append('  {:<8} {:>10} o  x{}  {}'.format(t, s, n, p))
    L.append('')

    strings = find_strings(vfs, term)
    L.append('## 2. Chaines contenant le terme (trouvees par le scan) : {}{}'.format(
        len(strings), ' (limite atteinte)' if len(strings) >= MAX_STRINGS else ''))
    for s, files in strings.items():
        L.append('  "{}"'.format(s))
        for label, ftype in files:
            L.append('      dans {:<8} {}'.format(ftype, label))
        if not files:
            L.append('      (aucun fichier source enregistre : chaine issue d\'un dictionnaire)')
    L.append('')

    if args.octets:
        patterns = build_patterns(term, paths, strings)
        types = [t.strip() for t in args.types.split(',') if t.strip()]
        print('Recherche de {} motifs dans les fichiers {}...'.format(len(patterns), types))
        hits, n_files, errors = scan_contents(vfs, patterns, types, int(args.taille_max * 1024 * 1024))
        L.append('## 3. Contenu des fichiers ({} fichiers {} lus, {} illisibles) : {} avec correspondance'.format(
            n_files, types, errors, len(hits)))
        L.append('Motifs cherches :')
        for pat, desc in patterns.items():
            L.append('  {:<24} {}'.format(pat.hex(' ') if len(pat) == 4 else '(texte)', desc))
        L.append('')
        for label, ftype, found in sorted(hits):
            L.append('  {:<8} {}'.format(ftype, label))
            for pat, offsets in found.items():
                shown = ', '.join('0x{:x}'.format(o) for o in offsets[:5])
                more = ' ...' if len(offsets) > 5 else ''
                L.append('      {} x{} a {}{}'.format(patterns[pat], len(offsets), shown, more))
        L.append('')
        L.append('Note : seules les correspondances de hash32 alignees sur 4 octets sont gardees. Un hash32')
        L.append('peut encore apparaitre par hasard dans de gros fichiers binaires (terrain .streampatch,')
        L.append('maillages) : une correspondance isolee y est a verifier avant d\'en tirer une conclusion.')

    text = '\n'.join(L) + '\n'
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding='utf-8')
        print('Ecrit : {}'.format(args.out))
    else:
        sys.stdout.write(text)
    return 0


if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    sys.exit(main())
