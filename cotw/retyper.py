#!/usr/bin/env python3
"""
retyper.py -- donne leur type aux fichiers qu'un projet DECA deja scanne a laisses sans type, sans rescanner.

Pourquoi : le type d'un fichier (core_nodes.file_type) est fixe au scan par deca.ff_determine. Le fork y a ajoute
des formats de COTW (2026-10-09) : RTPC en ligne (world.bin, .smodc), OBC version 1, .fmod_guids, police TrueType
(.ttfc), tableaux bruts .rawc (par leur nom). Un projet scanne avant ne les connait pas ; le rescanner coute
64 minutes. Ce script relit seulement les fichiers sans type (environ 11 000 noeuds sur COTW) avec la detection
actuelle et ecrit le type trouve. L'interface choisit alors la bonne visionneuse et deca_audit.py les compte
lisibles.

Le test "texte" de ff_determine (qui lit tout le fichier) est saute : le scan l'a deja fait sur ces fichiers.
Tout est journalise dans <projet>\\cotw_retypages.tsv ; "annuler" remet les types d'origine. DECA doit etre ferme.

Usage, depuis la racine du depot, environnement virtuel active :
    python cotw\\retyper.py ajouter D:\\dev\\work\\hp [--simulation] [--rapport D:\\dev\\audits\\05-lecture\\retyper.md]
    python cotw\\retyper.py annuler D:\\dev\\work\\hp
"""
import argparse
import collections
import datetime
import io
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _projet  # noqa: E402  (installe le substitut de zugbruecke sous Linux)
import noms_fichiers as NF  # noqa: E402

JOURNAL = 'cotw_retypages.tsv'
FLAG_TEMPORARY_FILE = 1 << 20
SANS_TYPE = (None, 'NOT')


def noeuds_sans_type(vfs) -> List[Tuple[int, Optional[str], Optional[str], Optional[int]]]:
    """(node_id, chemin, type, sous-type) des fichiers sans type, hors liens, temporaires et racines."""
    rows = vfs.db_query_all(
        "SELECT node_id, v_path, file_type, file_sub_type, flags FROM core_nodes "
        "WHERE (file_type IS NULL OR file_type = 'NOT') AND parent_offset IS NOT NULL", [],
        dbg='cotw_retyper_select')
    return [(nid, _projet.to_text(v), _projet.to_text(t), st) for nid, v, t, st, flags in rows
            if not (flags or 0) & FLAG_TEMPORARY_FILE]


def type_de(data: bytes, chemin: Optional[str]) -> Tuple[Optional[str], Optional[int]]:
    """(type, sous-type) par la detection de DECA et du fork ; .rawc par le nom (pas d'en-tete)."""
    from deca.ff_determine import determine_file_type_and_size
    from deca.ff_types import FTYPE_RAWC
    file_type, _size, _magic, sub = determine_file_type_and_size(io.BytesIO(data), len(data), check_text=False)
    if file_type is None and chemin and chemin.lower().endswith('.rawc'):
        file_type = FTYPE_RAWC
    return file_type, sub


class Plan:
    def __init__(self):
        self.changements: List[Tuple[int, Optional[str], Optional[str], str, Optional[int], Optional[int]]] = []
        self.restants = collections.Counter()      # famille -> noeuds toujours sans type
        self.erreurs: List[Tuple[int, str]] = []
        self.examines = 0
        self.secondes = 0.0


def preparer(vfs, limite: Optional[int] = None, log=print) -> Plan:
    plan = Plan()
    t0 = time.time()
    noeuds = noeuds_sans_type(vfs)
    if limite:
        noeuds = noeuds[:limite]
    for i, (node_id, chemin, ancien, ancien_sub) in enumerate(noeuds):
        try:
            data = _projet.read_node(vfs, vfs.node_where_uid(node_id))
            nouveau, sub = type_de(data, chemin)
        except Exception as exc:  # noqa: BLE001 - un fichier illisible reste sans type, il est signale
            plan.erreurs.append((node_id, '{}: {}'.format(type(exc).__name__, exc)))
            continue
        plan.examines += 1
        if nouveau is None or nouveau == ancien:
            plan.restants[famille(chemin)] += 1
        else:
            plan.changements.append((node_id, chemin, ancien, nouveau, ancien_sub, sub))
        if (i + 1) % 2000 == 0:
            log('  {} / {} fichiers examines ({:.0f} s)'.format(i + 1, len(noeuds), time.time() - t0))
    plan.secondes = time.time() - t0
    return plan


def famille(chemin: Optional[str]) -> str:
    return NF.extension_complete(chemin) or '(sans extension)' if chemin else '(sans nom)'


def appliquer(vfs, plan: Plan, journal_path: Path) -> int:
    if not plan.changements:
        return 0
    conn = vfs.db_conn
    cur = conn.cursor()
    stamp = datetime.datetime.now().isoformat(timespec='seconds')
    with open(journal_path, 'a', encoding='utf-8', newline='\n') as journal:
        journal.write('# session\t{}\t{} noeuds\n'.format(stamp, len(plan.changements)))
        for node_id, _chemin, ancien, nouveau, ancien_sub, sub in plan.changements:
            cur.execute('UPDATE core_nodes SET file_type = ?, file_sub_type = ? WHERE node_id = ?',
                        (nouveau, sub, node_id))
            journal.write('noeud\t{}\t{}\t{}\t{}\t{}\n'.format(
                node_id, ancien or '', nouveau, '' if ancien_sub is None else ancien_sub, '' if sub is None else sub))
    conn.commit()
    return len(plan.changements)


def annuler(vfs, journal_path: Path) -> Dict[str, int]:
    """Remet le type d'origine (celui de la premiere session) des noeuds qui portent encore le type ecrit."""
    origine: Dict[int, Tuple[Optional[str], Optional[int]]] = {}
    ecrit: Dict[int, str] = {}
    for line in journal_path.read_text(encoding='utf-8').splitlines():
        cols = line.split('\t')
        if cols[0] != 'noeud':
            continue
        node_id = int(cols[1])
        if node_id not in origine:
            origine[node_id] = (cols[2] or None, int(cols[4]) if cols[4] else None)
        ecrit[node_id] = cols[3]
    conn = vfs.db_conn
    cur = conn.cursor()
    bilan = {'noeuds': 0, 'modifies_depuis': 0}
    for node_id, (ancien, ancien_sub) in origine.items():
        row = cur.execute('SELECT file_type FROM core_nodes WHERE node_id = ?', (node_id,)).fetchone()
        if row is None or _projet.to_text(row[0]) != ecrit[node_id]:
            bilan['modifies_depuis'] += 1
            continue
        cur.execute('UPDATE core_nodes SET file_type = ?, file_sub_type = ? WHERE node_id = ?',
                    (ancien, ancien_sub, node_id))
        bilan['noeuds'] += 1
    conn.commit()
    journal_path.rename(journal_path.with_name(
        journal_path.name + '.annule-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S')))
    return bilan


def rapport(plan: Plan, simulation: bool, limite: int = 30) -> List[str]:
    par = collections.Counter((famille(c), n) for _id, c, _a, n, _s1, _s2 in plan.changements)
    par_type = collections.Counter(n for _id, _c, _a, n, _s1, _s2 in plan.changements)
    L = ['# retyper.py -- {}'.format('simulation' if simulation else 'application'), '',
         '| Mesure | Valeur |', '|---|---:|',
         '| Fichiers sans type examines | {} |'.format(plan.examines),
         '| **Fichiers types** | {} |'.format(len(plan.changements)),
         '| Toujours sans type | {} |'.format(sum(plan.restants.values())),
         '| Illisibles (signales) | {} |'.format(len(plan.erreurs)),
         '| Duree | {:.0f} s |'.format(plan.secondes), '']
    if par_type:
        L += ['## Types donnes', '', '| Type | Noeuds |', '|---|---:|']
        L += ['| `{}` | {} |'.format(t, n) for t, n in par_type.most_common()]
        L += ['', '| Famille | Type | Noeuds |', '|---|---|---:|']
        L += ['| `{}` | `{}` | {} |'.format(f, t, n) for (f, t), n in par.most_common(limite)]
        L.append('')
    if plan.restants:
        L += ['## Toujours sans type', '', '| Famille | Noeuds |', '|---|---:|']
        L += ['| `{}` | {} |'.format(f, n) for f, n in plan.restants.most_common(limite)]
        L.append('')
    if plan.erreurs:
        L += ['## Illisibles', '']
        L += ['- noeud {} : {}'.format(n, e) for n, e in plan.erreurs[:limite]]
        L.append('')
    return L


def cmd_ajouter(args) -> int:
    vfs = _projet.open_project(args.projet)
    plan = preparer(vfs, args.limite)
    n = 0 if args.simulation else appliquer(vfs, plan, Path(args.projet) / JOURNAL)
    texte = '\n'.join(rapport(plan, args.simulation)) + '\n'
    if args.rapport:
        Path(args.rapport).parent.mkdir(parents=True, exist_ok=True)
        Path(args.rapport).write_text(texte, encoding='utf-8')
    print(texte)
    if n:
        print('Journal : {}'.format(Path(args.projet) / JOURNAL))
        print('Pour revenir en arriere : python cotw\\retyper.py annuler {}'.format(args.projet))
    return 0


def cmd_annuler(args) -> int:
    vfs = _projet.open_project(args.projet)
    journal = Path(args.projet) / JOURNAL
    if not journal.is_file():
        print('Rien a annuler ({} absent).'.format(journal))
        return 0
    b = annuler(vfs, journal)
    print('{} noeud(s) remis a leur type d\'origine ({} modifie(s) depuis, laisses tels quels).'.format(
        b['noeuds'], b['modifies_depuis']))
    return 0


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(errors='replace')
    ap = argparse.ArgumentParser(description='Type les fichiers sans type d\'un projet DECA, sans rescanner.')
    sub = ap.add_subparsers(dest='commande', required=True)
    a = sub.add_parser('ajouter', help='typer les fichiers sans type avec la detection actuelle')
    a.add_argument('projet', help='projet DECA (dossier contenant db\\core.db)')
    a.add_argument('--simulation', action='store_true', help='rapport seulement, rien n\'est ecrit')
    a.add_argument('--rapport', help='ecrire aussi le rapport dans ce fichier (markdown)')
    a.add_argument('--limite', type=int, help='n\'examiner que les N premiers fichiers (essai)')
    a.set_defaults(func=cmd_ajouter)
    u = sub.add_parser('annuler', help='remettre les types d\'origine')
    u.add_argument('projet')
    u.set_defaults(func=cmd_annuler)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    sys.exit(main())
