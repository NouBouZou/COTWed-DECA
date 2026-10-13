#!/usr/bin/env python3
"""
voisins_orphelins.py -- fichiers sans nom et leurs voisins nommes dans les archives (lecture seule, DECA ferme).

Pourquoi (2026-10-13) : la lecture est complete (185 726 / 185 751), il reste 5 051 hashes sans nom. Un fichier sans
nom est range dans son .arc entre d'autres fichiers ; si l'outil de construction des archives ecrit les fichiers
par dossier ou par paquet de ressources, ses voisins nommes disent ou il est (le dossier) et souvent comment il
s'appelle (meme motif, numero suivant). Ce rapport donne, pour chaque hash sans nom, ses voisins les plus proches par
position dans l'archive, et par type les dossiers voisins les plus frequents : la matiere du prochain generateur de
noms.

Sorties (dossier --sortie) :
    voisins_orphelins.csv  hash ; archive ; position ; taille ; type ; nommes avant (du plus proche) ; nommes apres ;
                           sans nom entre le precedent nomme et lui
    voisins_orphelins.md   par type : dossiers des voisins les plus frequents, runs de fichiers sans nom
                           (suites contigues dans une archive) et quelques exemples

Usage (DECA ferme) :
    python cotw\\voisins_orphelins.py D:\\dev\\work\\hp --sortie D:\\dev\\audits\\10-voisins
"""
import argparse
import collections
import posixpath
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import deca_audit as DA  # noqa: E402  (base en lecture seule, formats des hashes et des archives)

RAYON_DEFAUT = 3


class Entree:
    __slots__ = ('node_id', 'offset', 'v_hash', 'path', 'file_type', 'size', 'flags')

    def __init__(self, node_id, offset, v_hash, path, file_type, size, flags):
        self.node_id, self.offset, self.v_hash, self.path = node_id, offset, v_hash, path
        self.file_type, self.size, self.flags = file_type, size, flags


def charger(conn) -> Tuple[Dict[int, str], Dict[int, List[Entree]], set]:
    """(archive de chaque TAB, entrees directes de chaque TAB triees par position, hashes portes par un nom)."""
    tabs = {nid: DA.archive_label(DA.to_text(p)) for nid, p in conn.execute(
        'SELECT node_id, p_path FROM core_nodes WHERE file_type = ?', (DA.FTYPE_TAB,))}
    par_tab: Dict[int, List[Entree]] = collections.defaultdict(list)
    nommes = set()
    q = ('SELECT node_id, parent_id, parent_offset, v_hash, v_path, file_type, size_u, flags FROM core_nodes '
         'WHERE parent_id IS NOT NULL')
    for nid, pid, off, vh, vp, ft, size, flags in conn.execute(q):
        path = DA.to_text(vp)
        if path and vh is not None:
            nommes.add(vh)
        if pid in tabs and off is not None and not (flags or 0) & DA.FLAG_TEMPORARY_FILE:
            par_tab[pid].append(Entree(nid, off, vh, path, DA.to_text(ft), size or 0, flags or 0))
    for lst in par_tab.values():
        lst.sort(key=lambda e: (e.offset, e.node_id))
    return tabs, par_tab, nommes


def voisins(lst: List[Entree], i: int, rayon: int, nommes: set) -> Tuple[List[str], List[str], int]:
    """(nommes avant, du plus proche ; nommes apres ; sans nom entre le precedent nomme et l'entree i)."""
    avant, apres, trou = [], [], 0
    j = i - 1
    while j >= 0 and len(avant) < rayon:
        if lst[j].path:
            avant.append(lst[j].path)
        elif not avant and lst[j].v_hash not in nommes:
            trou += 1
        j -= 1
    j = i + 1
    while j < len(lst) and len(apres) < rayon:
        if lst[j].path:
            apres.append(lst[j].path)
        j += 1
    return avant, apres, trou


def runs(lst: List[Entree], nommes: set) -> List[Tuple[int, int]]:
    """(debut, longueur) des suites contigues de fichiers sans nom (hash porte par aucun nom)."""
    out, start = [], None
    for i, e in enumerate(lst + [None]):
        orphan = e is not None and not e.path and e.v_hash not in nommes
        if orphan and start is None:
            start = i
        elif not orphan and start is not None:
            out.append((start, i - start))
            start = None
    return out


def analyser(conn, rayon: int = RAYON_DEFAUT):
    tabs, par_tab, nommes = charger(conn)
    lignes, dossiers, exemples, longueurs = [], collections.defaultdict(collections.Counter), \
        collections.defaultdict(list), collections.defaultdict(collections.Counter)
    vus = set()
    for tab_id, lst in sorted(par_tab.items(), key=lambda kv: tabs[kv[0]]):
        archive = tabs[tab_id]
        for start, n in runs(lst, nommes):
            types = collections.Counter(lst[k].file_type or '(aucun type)' for k in range(start, start + n))
            longueurs[types.most_common(1)[0][0]][n] += 1
        for i, e in enumerate(lst):
            if e.path or e.v_hash is None or e.v_hash in nommes or e.v_hash in vus:
                continue
            vus.add(e.v_hash)
            avant, apres, trou = voisins(lst, i, rayon, nommes)
            t = e.file_type or '(aucun type)'
            for p in avant[:1] + apres[:1]:
                dossiers[t][posixpath.dirname(p)] += 1
            if len(exemples[t]) < 8:
                exemples[t].append((DA.fmt_hash(e.v_hash, e.flags), archive, avant[:1], apres[:1]))
            lignes.append((DA.fmt_hash(e.v_hash, e.flags), archive, e.offset, e.size, t, ' | '.join(avant),
                           ' | '.join(apres), trou))
    return lignes, dossiers, exemples, longueurs


def ecrire(sortie: Path, lignes, dossiers, exemples, longueurs, sep=';'):
    sortie.mkdir(parents=True, exist_ok=True)
    DA.write_csv(sortie / 'voisins_orphelins.csv',
                 ['hash', 'archive', 'position', 'taille', 'type', 'nommes_avant', 'nommes_apres',
                  'sans_nom_depuis_le_precedent'], lignes, sep)
    par_type = collections.Counter(l[4] for l in lignes)
    L = ['# Fichiers sans nom et leurs voisins nommes', '',
         '{} hashes sans nom (entrees directes des .tab), par type :'.format(len(lignes)), '']
    L += ['| Type | Sans nom | Dossiers voisins les plus frequents | Suites contigues (longueur : nombre) |',
          '|---|---:|---|---|']
    for t, n in par_type.most_common():
        top = ', '.join('`{}` ({})'.format(d or '(racine)', c) for d, c in dossiers[t].most_common(4))
        lg = ', '.join('{} : {}'.format(k, v) for k, v in sorted(longueurs[t].items())[:6])
        L.append('| `{}` | {} | {} | {} |'.format(t, n, top or '-', lg or '-'))
    for t, n in par_type.most_common():
        L += ['', '## `{}`'.format(t), '']
        for h, archive, av, ap in exemples[t]:
            L.append('- `{}` ({}) : apres `{}`, avant `{}`'.format(h, archive, av[0] if av else '-',
                                                                    ap[0] if ap else '-'))
    (sortie / 'voisins_orphelins.md').write_text('\n'.join(L) + '\n', encoding='utf-8')
    return par_type


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('projet', help='projet DECA (dossier contenant db\\core.db), project.json ou core.db')
    ap.add_argument('--sortie', required=True, help='dossier des resultats')
    ap.add_argument('--rayon', type=int, default=RAYON_DEFAUT, help='voisins nommes de chaque cote (defaut 3)')
    ap.add_argument('--sep', default=';', help='separateur CSV (defaut ;)')
    args = ap.parse_args(argv)
    db_path, _projet_dir = DA.resolve_paths(args.projet)
    conn = DA.open_readonly(db_path)
    try:
        lignes, dossiers, exemples, longueurs = analyser(conn, args.rayon)
    finally:
        conn.close()
    par_type = ecrire(Path(args.sortie), lignes, dossiers, exemples, longueurs, args.sep)
    print('{} hashes sans nom : {}'.format(len(lignes), ', '.join('{} {}'.format(n, t) for t, n in
                                                                  par_type.most_common())))
    print('Resultats : {}'.format(Path(args.sortie).resolve()))
    return 0


if __name__ == '__main__':
    sys.exit(main())
