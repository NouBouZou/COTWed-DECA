#!/usr/bin/env python3
"""
extraire.py -- copie des fichiers du jeu, octet pour octet, depuis les archives d'un projet DECA.

Pourquoi : pour etudier un format (ex : les .stringlookup de traduction) il faut ses octets bruts, que seule
l'interface de DECA savait exporter, un fichier a la fois. Le fichier est ecrit sous son chemin virtuel, dans
SORTIE ; rien n'est ecrit dans le projet ni dans le jeu.

Selection :
  - chemins virtuels donnes en argument ;
  - --motif : motif SQL LIKE sur le chemin (ex : "ui/%.stringlookup") ;
  - --extension : extension complete, a partir du premier point du nom (".nl.mdic" ne prend pas les ".mdic"),
    comme la compte deca_audit.py ;
  - --noeuds : numeros de noeuds (fichiers sans nom, listes par deca_audit.py), ecrits sous _noeuds\\.
Avec --motif ou --extension, --echantillons N prend N fichiers repartis par taille (le plus petit, le plus grand
et des intermediaires : des cas varies pour etudier un format) au lieu de refuser au-dela de --max.
Un chemin present dans plusieurs archives avec des contenus differents est signale (_projet.read_by_path).
--a-plat ecrit chaque fichier directement dans SORTIE, son chemin virtuel aplati ("/" -> "__") : le pont du
bureau Claude ne copie pas un fichier a plus de 7 dossiers sous un dossier connecte.

Usage, depuis la racine du depot, environnement virtuel active, DECA ferme :
    python cotw\\extraire.py D:\\dev\\work\\hp ui/ui_localization.stringlookup settings/hp_settings/string_ids.stringlookup ^
        --sortie D:\\dev\\audits\\loc
    python cotw\\extraire.py D:\\dev\\work\\hp --motif "settings/hp_settings/%.stringlookup" --sortie D:\\dev\\audits\\loc
    python cotw\\extraire.py D:\\dev\\work\\hp --extension .ban --echantillons 5 --sortie D:\\dev\\audits\\echantillons\\ban
    python cotw\\extraire.py D:\\dev\\work\\hp --noeuds 123456 123789 --sortie D:\\dev\\audits\\echantillons\\sans_nom
    python cotw\\extraire.py D:\\dev\\work\\hp --extension .btc --echantillons 4 --a-plat --sortie D:\\dev\\audits\\echantillons\\btc
"""
import argparse
import sys
from pathlib import Path
from typing import List, Sequence, Tuple

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


SUFFIX = {'ADF': '.adf', 'RTPC': '.rtpc', 'SARC': '.sarc'}


def extension_of(path: str) -> str:
    """Extension complete a partir du premier point du nom de fichier (meme regle que deca_audit.py)."""
    name = path.replace('\\', '/').rsplit('/', 1)[-1]
    if '.' not in name:
        return '(sans extension)'
    return '.' + name.split('.', 1)[1].lower()


def candidates(vfs, like: str) -> List[Tuple[str, int]]:
    """(chemin, taille) des fichiers dont le chemin correspond au motif LIKE, tries par chemin."""
    rows = vfs.db_query_all('SELECT v_path, MAX(size_u) FROM core_nodes WHERE v_path LIKE ? AND size_u > 0 '
                            'GROUP BY v_path ORDER BY v_path', [like], dbg='cotw_extraire')
    return [(_projet.to_text(r[0]), r[1] or 0) for r in rows if r[0] is not None]


def with_extension(rows: Sequence[Tuple[str, int]], ext: str) -> List[Tuple[str, int]]:
    ext = ext.lower() if ext.startswith('.') else '.' + ext.lower()
    return [r for r in rows if extension_of(r[0]) == ext]


def spread(rows: Sequence[Tuple[str, int]], n: int) -> List[Tuple[str, int]]:
    """n fichiers repartis par taille, du plus petit au plus grand (deterministe : meme choix a chaque appel)."""
    ordered = sorted(rows, key=lambda r: (r[1], r[0]))
    if n <= 0 or len(ordered) <= n:
        return ordered
    if n == 1:
        return [ordered[len(ordered) // 2]]
    picks = sorted({round(i * (len(ordered) - 1) / (n - 1)) for i in range(n)})
    return [ordered[i] for i in picks]


def select(rows: Sequence[Tuple[str, int]], label: str, limit: int, samples: int) -> List[str]:
    if samples:
        return [r[0] for r in spread(rows, samples)]
    if len(rows) > limit:
        raise SystemExit('ERREUR : {} fichiers correspondent a {} (plus que --max {}) : affiner, ou --echantillons N'
                         .format(len(rows), label, limit))
    return [r[0] for r in rows]


def matching_paths(vfs, pattern: str, limit: int, samples: int = 0) -> List[str]:
    return select(candidates(vfs, pattern), '"{}"'.format(pattern), limit, samples)


def extension_paths(vfs, ext: str, limit: int, samples: int = 0) -> List[str]:
    ext = ext if ext.startswith('.') else '.' + ext
    # LIKE est insensible a la casse ASCII et "_" y est un joker : le filtre exact est fait en Python
    return select(with_extension(candidates(vfs, '%' + ext), ext), 'l\'extension {}'.format(ext), limit, samples)


def target_of(out_root: Path, v_path: str, flat: bool) -> Path:
    """Fichier a ecrire : sous son chemin virtuel, ou a plat ('a/b/c.obc' -> 'a__b__c.obc')."""
    v_path = v_path.replace('\\', '/')
    return out_root / (v_path.replace('/', '__') if flat else Path(v_path))


def write_node(vfs, uid: int, out_root: Path, flat: bool = False) -> Path:
    node = vfs.node_where_uid(uid)
    if node is None:
        raise SystemExit('ERREUR : aucun noeud {} dans le projet'.format(uid))
    if node.offset is None:
        raise SystemExit('ERREUR : le noeud {} n\'a pas de contenu (lien)'.format(uid))
    data = _projet.read_node(vfs, node)
    path = _projet.to_text(node.v_path)
    if path:
        target = target_of(out_root, path, flat)
    else:
        kind = kind_of(data)
        target = out_root / '_noeuds' / '{}_{:08x}{}'.format(uid, (node.v_hash or 0) & 0xFFFFFFFFFFFFFFFF,
                                                             SUFFIX.get(kind, '.bin'))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    print('noeud {} : {} octets, {} -> {}'.format(uid, len(data), kind_of(data), target))
    return target


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(errors='replace')
    ap = argparse.ArgumentParser(description='Copie des fichiers du jeu depuis les archives d\'un projet DECA.')
    ap.add_argument('projet', help='projet DECA (dossier contenant db\\core.db)')
    ap.add_argument('chemins', nargs='*', help='chemins virtuels, ex : ui/ui_localization.stringlookup')
    ap.add_argument('--motif', help='motif SQL LIKE sur le chemin virtuel, ex : "ui/%%.stringlookup"')
    ap.add_argument('--extension', help='extension complete, ex : .nl.mdic (meme regle que deca_audit.py)')
    ap.add_argument('--noeuds', nargs='+', type=int, default=[], help='numeros de noeuds (fichiers sans nom)')
    ap.add_argument('--echantillons', type=int, default=0,
                    help='avec --motif / --extension : N fichiers repartis par taille')
    ap.add_argument('--max', type=int, default=MAX_DEFAULT, help='nombre maximal de fichiers pour --motif')
    ap.add_argument('--sortie', required=True, help='dossier ou ecrire les fichiers, sous leur chemin virtuel')
    ap.add_argument('--a-plat', dest='a_plat', action='store_true',
                    help='ecrire les fichiers directement dans --sortie, chemin aplati ("/" -> "__")')
    args = ap.parse_args(argv)
    if not args.chemins and not args.motif and not args.extension and not args.noeuds:
        raise SystemExit('ERREUR : donner des chemins virtuels, --motif, --extension ou --noeuds')

    vfs = _projet.open_project(args.projet)
    paths = list(args.chemins)
    if args.motif:
        paths += matching_paths(vfs, args.motif, args.max, args.echantillons)
    if args.extension:
        found = extension_paths(vfs, args.extension, args.max, args.echantillons)
        if not found:
            print('Aucun fichier nomme avec l\'extension {}'.format(args.extension))
        paths += found
    out_root = Path(args.sortie).expanduser().resolve()
    for v_path in paths:
        data = _projet.read_by_path(vfs, v_path)
        target = target_of(out_root, v_path, args.a_plat)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        print('{} : {} octets, {} -> {}'.format(v_path, len(data), kind_of(data), target))
    for uid in args.noeuds:
        write_node(vfs, uid, out_root, args.a_plat)
    print('{} fichier(s) ecrit(s) dans {}'.format(len(paths) + len(args.noeuds), out_root))
    return 0


if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    sys.exit(main())
