#!/usr/bin/env python3
"""
separateurs.py -- quels champs d'equipment_data.bin distinguent les objets absents du jeu des objets presents ?

Complement de differentiel.py. Celui-ci cherche un FICHIER qui citerait les presents sans les absents. Si aucun
n'existe, la disponibilite se decide a l'interieur des fichiers qui citent tout le monde : equipment_data.bin
en premier lieu. Cet outil compare, champ par champ, les valeurs des absents a celles des presents :
  separateur parfait : aucun present n'a la valeur d'un absent (ex : seuls les absents ont locked = 5) ;
  separateur partiel : certains presents partagent la valeur d'un absent (ils sont listes : ce sont peut-etre
                       d'autres objets absents que la communaute n'a pas signales).
Les enfants comptent comme des champs (present ou absent, et resume de leur contenu).

Il affiche aussi la repartition de quelques champs sur toutes les categories (--champ pour en choisir, avec
enfant/champ pour un champ d'un enfant, ex : secondary_ids/0xa4f0837b), avec la liste des objets pour les
valeurs rares.

Usage, depuis la racine du depot, environnement virtuel active, DECA ferme :
    python cotw\\separateurs.py D:\\dev\\work\\hp --out D:\\dev\\audits\\separateurs.md
    python cotw\\separateurs.py D:\\dev\\work\\hp --champ locked --champ 0x4923c2e2 --out ...
"""
import argparse
import collections
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _projet  # noqa: E402
import cloner_munition as C  # noqa: E402
import comparer_objets as CO  # noqa: E402
import differentiel as DF  # noqa: E402
import rtpc_dump as D  # noqa: E402

import deca.ff_rtpc_write as W  # noqa: E402
from deca.ff_rtpc import k_type_none, k_type_str, k_type_u32, k_type_f32  # noqa: E402

MISSING = '—'
DEFAULT_FIELDS = ['locked', '0x4923c2e2', '0xcff03e33', '0x3c814744', 'received', 'is_featured',
                  'secondary_ids/0xa4f0837b']
RARE = 12


def value_text(p) -> str:
    if p is None or p.type == k_type_none:
        return MISSING
    if p.type == k_type_u32:
        return str(p.data)
    if p.type == k_type_f32:
        return '{:.7g}'.format(p.data)
    if p.type == k_type_str:
        return '"{}"'.format(bytes(p.data).decode('utf-8', 'replace'))
    return D.format_value(p, {})


def load_items(tree, names) -> Dict[str, Tuple[str, object]]:
    """nom de l'objet -> (categorie, noeud), pour tous les noeuds qui portent une propriete name."""
    items = {}

    def walk(node, category):
        p = W.node_get_prop(node, C.H_NAME)
        if p is not None and p.type == k_type_str:
            items[bytes(p.data).decode('utf-8', 'replace')] = (category, node)
        for c in node.child_table:
            walk(c, category)

    for cat in tree.root_node.child_table:
        walk(cat, D.name_of(cat.name_hash, names) if names.get(cat.name_hash) else '0x{:08x}'.format(cat.name_hash))
    return items


def fields_of(node, names) -> Dict[str, str]:
    """Champ -> valeur affichable. Les enfants deviennent 'enfant <nom>' -> resume."""
    out = {}
    for p in node.prop_table:
        if p.type != k_type_none:
            out['0x{:08x} {}'.format(p.name_hash, D.name_of(p.name_hash, names))] = value_text(p)
    for c in node.child_table:
        out['enfant 0x{:08x} {}'.format(c.name_hash, D.name_of(c.name_hash, names))] = CO.child_summary(c, names)
    return out


def separators(items, presents: Sequence[str], absents: Sequence[str], names):
    P = {n: fields_of(items[n][1], names) for n in presents}
    U = {n: fields_of(items[n][1], names) for n in absents}
    all_fields = sorted({f for d in list(P.values()) + list(U.values()) for f in d})
    perfect, partial = [], []
    for f in all_fields:
        pv = collections.Counter(d.get(f, MISSING) for d in P.values())
        uv = {n: d.get(f, MISSING) for n, d in U.items()}
        shared = {v for v in uv.values() if v in pv}
        if not shared:
            perfect.append((f, uv, pv))
        elif len(shared) < len(set(uv.values())):
            partial.append((f, uv, pv, sorted(n for n, d in P.items() if d.get(f, MISSING) in set(uv.values()))))
        else:
            # toutes les valeurs des absents existent chez des presents : on garde si peu de presents la partagent
            sharing = sorted(n for n, d in P.items() if d.get(f, MISSING) in set(uv.values()))
            if 0 < len(sharing) <= max(3, len(P) // 20):
                partial.append((f, uv, pv, sharing))
    return perfect, partial


def field_value(node, field: str, names) -> str:
    """Valeur d'un champ de l'objet, ou d'un champ d'un de ses enfants avec la syntaxe enfant/champ
    (ex : secondary_ids/0xa4f0837b)."""
    if '/' in field:
        child_name, _, prop_name = field.partition('/')
        child = W.node_get_child(node, C.field_hash(child_name))
        if child is None:
            return MISSING
        return value_text(W.node_get_prop(child, C.field_hash(prop_name)))
    return value_text(W.node_get_prop(node, C.field_hash(field)))


def field_label(field: str, names) -> str:
    parts = []
    for part in field.split('/'):
        h = C.field_hash(part)
        parts.append('0x{:08x} {}'.format(h, D.name_of(h, names)))
    return ' / '.join(parts)


def distribution(items, field: str, names) -> List[str]:
    by_value: Dict[str, List[Tuple[str, str]]] = collections.defaultdict(list)
    for name, (category, node) in items.items():
        by_value[field_value(node, field, names)].append((category, name))
    L = ['### {}'.format(field_label(field, names)), '',
         '| Valeur | Objets | Par catégorie | Objets (valeurs rares) |', '|---|---:|---|---|']
    for value, lst in sorted(by_value.items(), key=lambda kv: -len(kv[1])):
        cats = collections.Counter(c for c, _n in lst)
        names_rare = ', '.join('`{}`'.format(n) for _c, n in sorted(lst)) if len(lst) <= RARE else ''
        L.append('| {} | {} | {} | {} |'.format(value, len(lst), ', '.join('{} {}'.format(c, k) for c, k in cats.most_common()),
                                               names_rare))
    return L + ['']


def short_counter(c: collections.Counter, limit: int = 6) -> str:
    parts = ['{} ×{}'.format(DF.cell(v, 40), n) for v, n in c.most_common(limit)]
    return ', '.join(parts) + (' …' if len(c) > limit else '')


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(errors='replace')
    ap = argparse.ArgumentParser(description='Champs d\'equipment_data.bin qui distinguent les objets absents.')
    ap.add_argument('projet', help='projet DECA (dossier contenant db\\core.db)')
    ap.add_argument('--absents', help='objets absents, separes par des virgules (defaut : placeholders du Mod Builder)')
    ap.add_argument('--presents', help='objets presents, separes par des virgules')
    ap.add_argument('--presents-fichier', help='fichier : un objet present par ligne (ex : vus au magasin)')
    ap.add_argument('--champ', action='append', default=[],
                    help='champ dont afficher la repartition (repetable) ; enfant/champ pour le champ d\'un enfant')
    ap.add_argument('--base', help='dossier au format dropzone dont lire equipment_data.bin s\'il y est')
    ap.add_argument('--out', help='rapport markdown (sinon : affichage a l\'ecran)')
    args = ap.parse_args(argv)

    vfs = _projet.open_project(args.projet)
    base = Path(args.base).expanduser().resolve() if args.base else None
    data, src = C.load(vfs, base, C.EQUIPMENT_DATA)
    tree = W.rtpc_from_bytes(data)
    names = _projet.resolve_hash32(vfs, D.collect_hashes(tree))
    items = load_items(tree, names)

    absents = [a.strip() for a in args.absents.split(',')] if args.absents else list(DF.DEFAULT_ABSENTS)
    ignored = [a for a in absents if a not in items]
    absents = [a for a in absents if a in items]
    if not absents:
        raise SystemExit('ERREUR : aucun des objets absents n\'est dans equipment_data.bin ({})'.format(', '.join(ignored)))
    if args.presents_fichier or args.presents:
        presents = (DF.read_names(Path(args.presents_fichier)) if args.presents_fichier
                    else [p.strip() for p in args.presents.split(',')])
        presents = [p for p in presents if p in items and p not in absents]
    else:
        cats = {items[a][0] for a in absents}
        presents = sorted(n for n, (c, _node) in items.items() if c in cats and n not in absents)

    perfect, partial = separators(items, presents, absents, names)
    L = ['# Champs qui distinguent les absents des présents', '',
         '- equipment_data : {}'.format(src),
         '- Absents ({}) : {}'.format(len(absents), ', '.join('`{}`'.format(a) for a in absents)),
         '- Ignorés (pas dans equipment_data.bin) : {}'.format(', '.join('`{}`'.format(a) for a in ignored) or 'aucun'),
         '- Présents : {} objets ({})'.format(len(presents), 'liste fournie' if (args.presents or args.presents_fichier)
                                              else 'autres objets des mêmes catégories'), '',
         '## 1. Séparateurs parfaits : aucun présent n\'a la valeur d\'un absent', '',
         '| Champ | Valeur des absents | Valeurs des présents |', '|---|---|---|']
    for f, uv, pv in perfect:
        L.append('| {} | {} | {} |'.format(DF.cell(f, 50), '; '.join('{} = {}'.format(n, DF.cell(v, 50)) for n, v in uv.items()),
                                          short_counter(pv)))
    if not perfect:
        L.append('| aucun | | |')
    L += ['', '## 2. Séparateurs partiels : peu de présents partagent la valeur d\'un absent', '',
          '| Champ | Valeur des absents | Valeurs des présents | Présents concernés |', '|---|---|---|---|']
    for f, uv, pv, sharing in partial:
        L.append('| {} | {} | {} | {} |'.format(DF.cell(f, 50), '; '.join('{} = {}'.format(n, DF.cell(v, 50)) for n, v in uv.items()),
                                               short_counter(pv), ', '.join('`{}`'.format(n) for n in sharing[:12])
                                               + (' …' if len(sharing) > 12 else '')))
    if not partial:
        L.append('| aucun | | | |')
    L += ['', '## 3. Répartition de quelques champs sur tous les objets ({} objets)'.format(len(items)), '']
    for field in args.champ or DEFAULT_FIELDS:
        L += distribution(items, field, names)

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
