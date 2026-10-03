#!/usr/bin/env python3
"""
adf_ecriture_controle.py -- verifie adf_ecriture.py sur les vrais fichiers ADF du jeu, avant tout essai en jeu.

Pourquoi : adf_ecriture.py n'a ete teste que sur des ADF fabriques a la main. On ne sait pas encore comment le
jeu dispose ses ADF (tables avant ou apres les donnees, taille totale, alignements). Ce controle le mesure et
exerce l'ecriture sur chaque fichier, sans rien ecrire dans le jeu.

Pour chaque fichier :
  1. disposition : ordre des tables et des donnees, taille totale de l'en-tete comparee a la taille de l'ADF,
     valeurs des 5 u32 inconnus de l'en-tete (s'ils etaient des offsets, adf_ecriture ne les decalerait pas) ;
  2. pour chaque instance : jusqu'a --tableaux tableaux sont DEPLACES a la fin de l'instance (recopies, en-tete
     repointe, sans element ajoute), du plus profond au plus haut ; le fichier obtenu est relu par DECA et doit
     donner exactement les memes valeurs, pour toutes les instances. C'est la meme operation que pour ajouter des
     elements, sans changer le contenu : un ecart designe un defaut de l'ecriture ;
  3. chaines : pour chaque tableau de chaines (String), regle du second champ de l'en-tete (longueur, longueur
     + 1, constante) ou, a defaut, repartition des valeurs observees ;
  4. pour une feuille XLSBook : ajout d'une ligne copiee de la premiere ligne de donnees de la premiere feuille
     (nom de la copie : MODELE_cotwed_test), controle complet de xlsbook.verify.

Resultats : ok, refus (adf_ecriture refuse une disposition qu'il ne connait pas, avec la raison), ecart (la
relecture differe : defaut a corriger), erreur (exception inattendue), illisible (DECA ne lit pas l'original).

Usage, depuis la racine du depot, environnement virtuel active, DECA ferme :
    python cotw\\adf_ecriture_controle.py D:\\dev\\work\\hp --out D:\\dev\\audits\\adf_ecriture.md
    python cotw\\adf_ecriture_controle.py D:\\dev\\work\\hp --motif "settings/%" --out ...
    python cotw\\adf_ecriture_controle.py D:\\dev\\work\\hp --tous --out ...        (long : tous les ADF du jeu)
"""
import argparse
import collections
import sys
import time
import traceback
from pathlib import Path
from typing import List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _projet  # noqa: E402
import adf_ajouter_ligne as AL  # noqa: E402
import adf_ecriture as AE  # noqa: E402
import xlsbook as X  # noqa: E402

DEFAULT_PATTERN = 'settings/hp_settings/%'
ADF_TYPES = ('adf', 'adf5')
MAX_EXAMPLES = 15


def select_nodes(vfs, files: List[str], pattern: Optional[str], everything: bool, limit: Optional[int]):
    """[(node, v_path)] sans doublon de contenu (meme v_hash)."""
    if files:
        out = []
        for f in files:
            nodes = [n for n in _projet.nodes_by_path(vfs, f) if n.offset is not None]
            if not nodes:
                raise SystemExit('ERREUR : {} absent du projet'.format(f))
            out.append((nodes[0], f))
        return out
    sql = "SELECT node_id, v_hash, v_path FROM core_nodes WHERE file_type IN ('adf', 'adf5') AND size_u > 0"
    params = []
    if not everything:
        sql += ' AND v_path LIKE ?'
        params.append(pattern or DEFAULT_PATTERN)
    rows = vfs.db_query_all(sql + ' ORDER BY v_path IS NULL, v_path, node_id', params, dbg='cotw_adf_select')
    seen, out = set(), []
    for node_id, v_hash, v_path in rows:
        key = v_hash if v_hash is not None else ('uid', node_id)
        if key in seen:
            continue
        seen.add(key)
        out.append((vfs.node_where_uid(node_id), _projet.to_text(v_path) or '0x{:08x}'.format(v_hash or 0)))
    return out[:limit] if limit else out


def layout(data: bytes, adf) -> Tuple[str, bool, tuple]:
    """(ordre des zones, taille totale == taille de l'ADF, 5 u32 inconnus de l'en-tete)."""
    base = AE.adf_base(data)
    h = AE.Header(data, base)
    marks = [(0, 'en-tete')] + [(off, name) for name, (count, off) in h.tables.items() if count]
    marks += [(ins.offset, 'donnees') for ins in adf.table_instance]
    order = []
    for _o, name in sorted(marks):
        if not order or order[-1] != name:
            order.append(name)
    return ' < '.join(order), h.total == len(data) - base, h.unknown


def arrays_post_order(adf, root, limit: int):
    """Tableaux (Array, au moins un element) de l'instance, les plus profonds d'abord."""
    from deca.ff_adf import AdfValue
    types = adf.extended_map_typedef
    out = []

    def walk(v):
        if len(out) >= limit or not isinstance(v, AdfValue):
            return
        val = v.value
        if isinstance(val, AdfValue):
            walk(val)
        elif isinstance(val, dict):
            for x in val.values():
                walk(x)
        elif isinstance(val, list):
            for x in val:
                walk(x)
        td = types.get(v.type_id)
        if (td is not None and td.metatype == AE.META_ARRAY and isinstance(val, (list, bytes, bytearray))
                and len(val) and len(out) < limit):
            out.append(v)

    walk(root)
    return out


def same_values(old, new) -> Optional[int]:
    """None si toutes les instances relues sont identiques, sinon l'index de la premiere qui differe."""
    if len(old.table_instance) != len(new.table_instance):
        return -1
    for j in range(len(old.table_instance)):
        if AE.normalize(AE.plain_values(old, j)) != AE.normalize(AE.plain_values(new, j)):
            return j
    return None


def check_moves(decode, data: bytes, adf, max_arrays: int) -> Tuple[int, int]:
    """Deplace les tableaux de chaque instance, instance par instance ; (tableaux deplaces, octets ajoutes)."""
    moved = added = 0
    for i, ins in enumerate(adf.table_instance):
        root = adf.table_instance_full_values[i]
        if root is None or ins.size == 0:
            continue
        arrays = arrays_post_order(adf, root, max_arrays)
        if not arrays:
            continue
        ed = AE.InstanceEditor(adf, data, i)
        for a in arrays:
            ed.grow(a, [])
        out = ed.result()
        new = decode(out)
        if new is None:
            raise AssertionError('instance {} : fichier obtenu illisible par DECA'.format(i))
        diff = same_values(adf, new)
        if diff is not None:
            raise AssertionError('instance {} : apres deplacement de {} tableau(x), relecture differente '
                                 '(instance {})'.format(i, len(arrays), diff))
        moved += len(arrays)
        added += len(out) - len(data)
    return moved, added


def string_arrays(adf, data: bytes, limit: int = 8) -> List[Tuple[str, str]]:
    """[(chemin du tableau, regle du second champ ou repartition)] pour les tableaux de chaines."""
    from deca.ff_adf import AdfValue
    types = adf.extended_map_typedef
    out = []
    for i, ins in enumerate(adf.table_instance):
        root = adf.table_instance_full_values[i]
        if root is None or ins.size == 0:
            continue
        ed = AE.InstanceEditor(adf, data, i)

        def walk(v, path):
            if len(out) >= limit or not isinstance(v, AdfValue):
                return
            td = types.get(v.type_id)
            if td is not None and td.metatype == AE.META_ARRAY and td.element_type_hash == AE.STRING_TYPE:
                if isinstance(v.value, list) and v.value:
                    try:
                        out.append((path, ed.string_rule(v)[1]))
                    except AE.EAdfEcriture as e:
                        out.append((path, str(e)))
                return
            val = v.value
            if isinstance(val, AdfValue):
                walk(val, path)
            elif isinstance(val, dict):
                for k, x in val.items():
                    walk(x, '{}.{}'.format(path, k))
            elif isinstance(val, list):
                for k, x in enumerate(val[:4]):
                    walk(x, '{}[{}]'.format(path, k))

        walk(root, ins.name.decode('utf-8', 'replace'))
    return out


def check_xls(decode, data: bytes, adf) -> Optional[str]:
    for i, ins in enumerate(adf.table_instance):
        if ins.type_hash != X.XLSBOOK_TYPE:
            continue
        values = AE.plain_values(adf, i)
        for si, s in enumerate(values['Sheet']):
            rows = X.sheet_rows(values, si)
            for r in rows[1:]:
                for c in r:
                    if isinstance(c, str) and c and len(X.find_rows(values, c)) == 1:
                        new_name = c + '_cotwed_test'
                        out, plan = X.add_row(adf, data, c, new_name, instance=i)
                        X.verify(decode, data, out, plan, instance=i)
                        return 'ligne "{}" (feuille "{}") copiee : ok, chaines : {}'.format(
                            c, plan.match.sheet_name, plan.second_rule)
        return 'aucune ligne modele utilisable'
    return None


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(errors='replace')
    ap = argparse.ArgumentParser(description='Controle de adf_ecriture.py sur les ADF du jeu.')
    ap.add_argument('projet', help='projet DECA (dossier contenant db\\core.db)')
    ap.add_argument('--fichier', action='append', default=[], help='chemin virtuel (option repetable)')
    ap.add_argument('--motif', help='motif SQL LIKE sur le chemin (defaut : {})'.format(DEFAULT_PATTERN))
    ap.add_argument('--tous', action='store_true', help='tous les ADF du projet (long)')
    ap.add_argument('--limite', type=int, help='ne traiter que les N premiers fichiers')
    ap.add_argument('--tableaux', type=int, default=64, help='tableaux deplaces par instance (defaut 64)')
    ap.add_argument('--out', help='rapport markdown (sinon : affichage a l\'ecran)')
    args = ap.parse_args(argv)

    import resoudre_hashes as RH
    vfs = _projet.open_project(args.projet)
    decode = AL.decoder(RH.FileReader(vfs))
    selected = select_nodes(vfs, args.fichier, args.motif, args.tous, args.limite)
    print('{} fichier(s) ADF a controler'.format(len(selected)))
    results = collections.Counter()
    examples = collections.defaultdict(list)
    layouts = collections.Counter()
    totals = collections.Counter()
    unknowns = collections.Counter()
    string_rules = collections.Counter()
    string_examples = []
    rows_out = []
    moved_total = 0
    t0 = last = time.time()
    for k, (node, label) in enumerate(selected, 1):
        status, detail = 'ok', ''
        try:
            data = _projet.read_node(vfs, node)
            adf = decode(data)
            if adf is None:
                status, detail = 'illisible', 'DECA ne lit pas l\'original'
            else:
                order, total_ok, unknown = layout(data, adf)
                layouts[order] += 1
                unknowns[' '.join('0x{:x}'.format(u) for u in unknown)] += 1
                totals['taille totale = taille de l\'ADF' if total_ok else 'taille totale differente'] += 1
                moved, added = check_moves(decode, data, adf, args.tableaux)
                moved_total += moved
                detail = '{} tableau(x) deplace(s), +{} octets'.format(moved, added)
                for path, rule in string_arrays(adf, data):
                    key = rule if not rule.startswith('second champ') else 'sans regle'
                    string_rules[key.split(' (')[0]] += 1
                    if (key == 'sans regle' or '(' in rule) and len(string_examples) < MAX_EXAMPLES:
                        string_examples.append((label, path, rule))
                xls = check_xls(decode, data, adf)
                if xls:
                    detail += ' ; XLSBook : ' + xls
                rows_out.append((label, len(data), len(adf.table_instance), order, 'oui' if total_ok else 'non', detail))
        except (AE.EAdfEcriture, X.EXls) as e:
            status, detail = 'refus', str(e)
        except AssertionError as e:
            status, detail = 'ecart', str(e)
        except Exception as e:
            status, detail = 'erreur', '{}: {}'.format(type(e).__name__, e)
            if len(examples['erreur']) < 3:
                detail += '\n' + traceback.format_exc(limit=3)
        results[status] += 1
        if status != 'ok' and len(examples[status]) < MAX_EXAMPLES:
            examples[status].append((label, detail))
        if time.time() - last > 10 or k == len(selected):
            last = time.time()
            print('  {}/{} ({:.0f} s), {}'.format(k, len(selected), last - t0, dict(results)))

    L = ['# Controle de l\'ecriture ADF (adf_ecriture.py) sur les fichiers du jeu', '',
         'Fichiers : {} ; tableaux deplaces : {}.'.format(len(selected), moved_total), '',
         '| Resultat | Fichiers |', '|---|---:|']
    L += ['| {} | {} |'.format(s, n) for s, n in results.most_common()]
    L += ['', '## Dispositions', '', '| Ordre dans le fichier | Fichiers |', '|---|---:|']
    L += ['| {} | {} |'.format(o, n) for o, n in layouts.most_common()]
    L += [''] + ['- {} : {}'.format(t, n) for t, n in totals.most_common()]
    L += ['', '## Second champ des en-tetes de chaine', '', '| Regle | Tableaux de chaines |', '|---|---:|']
    L += ['| {} | {} |'.format(r, n) for r, n in string_rules.most_common()]
    if string_examples:
        L += [''] + ['- `{}` {} : {}'.format(label, path, rule) for label, path, rule in string_examples]
    L += ['', '## 5 u32 inconnus de l\'en-tete (0x2c..0x40)', '', '| Valeurs | Fichiers |', '|---|---:|']
    L += ['| `{}` | {} |'.format(u, n) for u, n in unknowns.most_common(15)]
    if len(unknowns) > 15:
        L.append('| ... {} autres combinaisons | |'.format(len(unknowns) - 15))
    for status in ('ecart', 'erreur', 'refus', 'illisible'):
        if examples[status]:
            L += ['', '## {} (exemples)'.format(status.capitalize()), '']
            L += ['- `{}` : {}'.format(label, d.replace('\n', '\n  ')) for label, d in examples[status]]
    if rows_out:
        L += ['', '## Fichiers controles', '', '| Fichier | Taille | Instances | Disposition | Taille totale exacte | Detail |',
              '|---|---:|---:|---|---|---|']
        L += ['| `{}` | {} | {} | {} | {} | {} |'.format(*r) for r in rows_out[:500]]
        if len(rows_out) > 500:
            L.append('| ... {} autres | | | | | |'.format(len(rows_out) - 500))
    text = '\n'.join(L) + '\n'
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding='utf-8')
        print('Ecrit : {}'.format(args.out))
    else:
        sys.stdout.write(text)
    return 0 if not (results['ecart'] or results['erreur']) else 1


if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    sys.exit(main())
