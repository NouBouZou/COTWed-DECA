#!/usr/bin/env python3
"""
sarc_dump.py -- contenu d'une archive SARC du jeu (ex : l'entite .ee d'une munition).

Pour chaque entree : chemin, taille, inclus ou lien (fichier ailleurs dans le jeu), type d'apres l'en-tete.
Avec --rtpc, les RTPC inclus (le .epe d'une entite) sont affiches comme par rtpc_dump.py, noms resolus.

Usage, depuis la racine du depot, environnement virtuel active, DECA ferme :
    python cotw\\sarc_dump.py D:\\dev\\work\\hp editor/entities/hp_weapons/ammunition/rifles/equipment_ammo_243_sp_01.ee --rtpc
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _projet  # noqa: E402
import cloner_munition as C  # noqa: E402
import rtpc_dump as D  # noqa: E402
import sarc as S  # noqa: E402

import deca.ff_rtpc_write as W  # noqa: E402


def kind_of(data) -> str:
    if data is None:
        return 'lien'
    if data[:4] == W.RTPC_MAGIC:
        return 'RTPC'
    if data[:4] == b' FDA' or data[5:9] == b' FDA':
        return 'ADF'
    if data[4:8] == b'SARC':
        return 'SARC'
    return 'autre ({})'.format(data[:4].hex(' '))


def describe(vfs, sarc: S.Sarc, label: str, with_rtpc: bool):
    L = ['# {} : SARC v{}, {} entrées ({} liens)'.format(
        label, sarc.version, len(sarc.entries), sum(1 for e in sarc.entries if e.is_link)), '',
        '| # | Chemin | Taille | Contenu |', '|---:|---|---:|---|']
    for i, e in enumerate(sarc.entries):
        L.append('| {} | `{}` | {} | {} |'.format(i, e.path, e.length, kind_of(e.data)))
    if with_rtpc:
        for e in sarc.entries:
            if e.data is not None and e.data[:4] == W.RTPC_MAGIC:
                tree = W.rtpc_from_bytes(e.data)
                names = _projet.resolve_hash32(vfs, D.collect_hashes(tree))
                L += ['', '## {}'.format(e.path), '', '```']
                lines = []
                D.format_node(tree.root_node, names, '0', 0, None, lines)
                L += lines + ['```']
    return L


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(errors='replace')
    ap = argparse.ArgumentParser(description='Contenu d\'une archive SARC du jeu.')
    ap.add_argument('projet', help='projet DECA (dossier contenant db\\core.db)')
    ap.add_argument('fichier', help='chemin virtuel du SARC (ex : ...equipment_ammo_243_sp_01.ee) ou fichier local')
    ap.add_argument('--rtpc', action='store_true', help='afficher aussi les RTPC inclus (le .epe)')
    ap.add_argument('--base', help='dossier au format dropzone dont lire le fichier s\'il y est')
    ap.add_argument('--out', help='fichier de sortie (sinon : affichage a l\'ecran)')
    args = ap.parse_args(argv)

    vfs = _projet.open_project(args.projet)
    local = Path(args.fichier)
    if local.is_file():
        data, label = local.read_bytes(), str(local)
    else:
        base = Path(args.base).expanduser().resolve() if args.base else None
        data, label = C.load(vfs, base, args.fichier)
    try:
        sarc = S.parse(data)
    except S.ESarc as e:
        raise SystemExit('ERREUR : {} : {}'.format(args.fichier, e))
    text = '\n'.join(describe(vfs, sarc, label, args.rtpc)) + '\n'
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
