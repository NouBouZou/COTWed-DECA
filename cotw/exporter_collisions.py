#!/usr/bin/env python3
"""
exporter_collisions.py -- exporte en OBJ les formes de collision Havok (.pfxc, .pfs, .physicsc) pour les voir dans
Blender. Lecture seule du projet, DECA ferme.

Pourquoi (2026-10-29) : deca/ff_havok_physics.py decode les formes hknp de COTW (polytopes convexes, capsules,
spheres, formes composees, hkGeometry). Un fichier OBJ par fichier physique, un objet par forme convexe, place dans le
repere du fichier (corps : position et orientation ; formes composees : transformation de chaque instance). Les
capsules et les spheres sont refaites a partir de leur rayon ; les polytopes gardent leurs faces (triangulees en
eventail).

Repere : celui du jeu (y vers le haut, metres) ; l'import OBJ de Blender le convertit (axe avant -Z, haut Y).

Usage (DECA ferme) :
    python cotw\\exporter_collisions.py D:\\dev\\work\\hp --fichiers "models/hp_vegetation/z01/aspen/%.pfxc" ^
        --max 5 --sortie D:\\dev\\audits\\29-collisions
    python cotw\\exporter_collisions.py --local fichier.pfxc --sortie dossier      (fichier deja extrait)
"""
import argparse
import sys
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _projet  # noqa: E402


def ecrire_obj(chemin: Path, maillages: List[Tuple[str, np.ndarray, np.ndarray]], titre: str = '') -> Tuple[int, int]:
    """Un objet OBJ par maillage (nom nettoye, numero ajoute) ; renvoie (objets, triangles)."""
    chemin.parent.mkdir(parents=True, exist_ok=True)
    lignes = ['# COTWed DECA : formes de collision de {}'.format(titre) if titre else '# COTWed DECA']
    base, tris = 1, 0
    for k, (nom, v, t) in enumerate(maillages):
        propre = ''.join(c if c.isalnum() or c in '._-/' else '_' for c in (nom or 'forme'))
        lignes.append('o {:04d}_{}'.format(k, propre.replace('/', '_')))
        lignes += ['v {:.6f} {:.6f} {:.6f}'.format(*p) for p in v]
        lignes += ['f {} {} {}'.format(a + base, b + base, c + base) for a, b, c in t]
        base += len(v)
        tris += len(t)
    chemin.write_text('\n'.join(lignes) + '\n', encoding='utf-8')
    return len(maillages), tris


def exporter_octets(data: bytes, sortie: Path, nom: str) -> str:
    from deca.ff_havok_physics import scene_meshes
    m = scene_meshes(data)
    n, t = ecrire_obj(sortie / (nom + '.obj'), m, nom)
    return '{} : {} formes, {} triangles'.format(nom, n, t)


def chercher(vfs, motifs: List[str], maximum: int) -> List[Tuple[int, str]]:
    out = []
    for m in motifs:
        rows = vfs.db_query_all("SELECT MIN(node_id), v_path FROM core_nodes WHERE CAST(v_path AS TEXT) LIKE ? "
                                "GROUP BY v_path ORDER BY v_path", [m], dbg='cotw_exporter_collisions')
        out += [(uid, _projet.to_text(p)) for uid, p in rows]
    return out[:maximum] if maximum else out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('projet', nargs='?', help='projet DECA (dossier contenant db\\core.db)')
    ap.add_argument('--fichiers', action='append', default=[], help='chemins ou motifs SQL LIKE (%%), repetable')
    ap.add_argument('--local', action='append', default=[], help='fichier physique deja extrait, repetable')
    ap.add_argument('--max', type=int, default=20, help='nombre maximal de fichiers du projet (0 = tous)')
    ap.add_argument('--sortie', required=True, help='dossier des .obj')
    args = ap.parse_args(argv)
    sortie = Path(args.sortie)
    lignes = []
    for p in args.local:
        lignes.append(exporter_octets(Path(p).read_bytes(), sortie, Path(p).name))
    if args.projet and args.fichiers:
        vfs = _projet.open_project(args.projet)
        for uid, chemin in chercher(vfs, args.fichiers, args.max):
            try:
                data = _projet.read_node(vfs, vfs.node_where_uid(uid))
                lignes.append(exporter_octets(data, sortie, chemin.replace('/', '__')))
            except Exception as exc:  # noqa: BLE001 - un fichier en echec n'arrete pas l'export
                lignes.append('{} : ECHEC {}: {}'.format(chemin, type(exc).__name__, exc))
    for l in lignes:
        print('  ' + l)
    print('Resultat : {}'.format(sortie))
    return 0


if __name__ == '__main__':
    sys.exit(main())
