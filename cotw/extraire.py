#!/usr/bin/env python3
"""
extraire.py -- copie des fichiers du jeu, octet pour octet, depuis les archives d'un projet DECA.

Pourquoi : pour etudier un format (ex : les .stringlookup de traduction) il faut ses octets bruts, que seule
l'interface de DECA savait exporter, un fichier a la fois. Le fichier est ecrit sous son chemin virtuel, dans
SORTIE ; rien n'est ecrit dans le projet ni dans le jeu.

Selection : chemins virtuels donnes en argument, ou --motif (motif SQL LIKE sur le chemin, ex : "ui/%.stringlookup").
Un chemin present dans plusieurs archives avec des contenus differents est signale (_projet.read_by_path).

Usage, depuis la racine du depot, environnement virtuel active, DECA ferme :
    python cotw\\extraire.py D:\\dev\\work\\hp ui/ui_localization.stringlookup settings/hp_settings/string_ids.stringlookup ^
        --sortie D:\\dev\\audits\\loc
    python cotw\\extraire.py D:\\dev\\work\\hp --motif "settings/hp_settings/%.stringlookup" --sortie D:\\dev\\audits\\loc
"""
import argparse
import sys
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _projet  # noqa: E402

MAX_DEFAULT = 200


def kind_of(data: bytes) -> str:
    if data[:4] == b' FDA' or data[5:9] == b' FDA':
        return 'ADF'
    if data[:4] == b'RTPC':
        return 'RTPC'
    if data[4:8] == b'SARC':
        return 'SARC'
    return 'autre ({})'.format(data[:4].hex(' '))


def matching_paths(vfs, pattern: str, limit: int) -> List[str]:
    rows = vfs.db_query_all('SELECT DISTINCT v_path FROM core_nodes WHERE v_path LIKE ? AND size_u > 0 '
                            'ORDER BY v_path', [pattern], dbg='cotw_extraire')
    paths = [_projet.to_text(r[0]) for r in rows if r[0] is not None]
    if len(paths) > limit:
        raise SystemExit('ERREUR : {} fichiers correspondent a "{}" (plus que --max {}) : affiner le motif'.format(
            len(paths), pattern, limit))
    return paths


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(errors='replace')
    ap = argparse.ArgumentParser(description='Copie des fichiers du jeu depuis les archives d\'un projet DECA.')
    ap.add_argument('projet', help='projet DECA (dossier contenant db\\core.db)')
    ap.add_argument('chemins', nargs='*', help='chemins virtuels, ex : ui/ui_localization.stringlookup')
    ap.add_argument('--motif', help='motif SQL LIKE sur le chemin virtuel, ex : "ui/%%.stringlookup"')
    ap.add_argument('--max', type=int, default=MAX_DEFAULT, help='nombre maximal de fichiers pour --motif')
    ap.add_argument('--sortie', required=True, help='dossier ou ecrire les fichiers, sous leur chemin virtuel')
    args = ap.parse_args(argv)
    if not args.chemins and not args.motif:
        raise SystemExit('ERREUR : donner des chemins virtuels ou --motif')

    vfs = _projet.open_project(args.projet)
    paths = list(args.chemins) + (matching_paths(vfs, args.motif, args.max) if args.motif else [])
    out_root = Path(args.sortie).expanduser().resolve()
    for v_path in paths:
        data = _projet.read_by_path(vfs, v_path)
        target = out_root / Path(v_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        print('{} : {} octets, {} -> {}'.format(v_path, len(data), kind_of(data), target))
    print('{} fichier(s) ecrit(s) dans {}'.format(len(paths), out_root))
    return 0


if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    sys.exit(main())
