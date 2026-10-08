#!/usr/bin/env python3
"""
audit_noms_internes.py -- part des noms internes resolus dans les RTPC et les ADF d'un projet DECA (critere N2).

Pourquoi : deca_audit.py mesure les noms de FICHIERS. A l'interieur des fichiers, beaucoup de noms sont aussi des
hashes : noms de proprietes et de noeuds RTPC, valeurs StringHash des ADF, evenements et identifiants d'objets.
Tant qu'on ne les compte pas, on ne sait ni ou en est la lecture, ni quels noms chercher en premier.

Ce que l'audit compte, une fois par fichier unique (une copie par hash, comme le scan de DECA) :

| Categorie | Ce qui est compte | Resolu si |
|---|---|---|
| rtpc_noeud | nom (hash32) de chaque noeud RTPC, sauf 0 | chaine connue, ou hash32 d'une chaine du meme fichier |
| rtpc_propriete | nom (hash32) de chaque propriete RTPC, sauf 0 | idem |
| rtpc_objid, rtpc_event | valeurs objid et event (48 bits bas, puis 32) | chaine connue (hash48, puis hash32) |
| adf_stringhash_32/48/64 | champs StringHash des instances ADF, sauf 0 et 0xdeadbeef | table StringHash du fichier, ou chaine connue |
| adf_enum | champs d'enumeration ADF | valeur parmi celles des membres |

"Chaine connue" = table core_strings du projet ou dictionnaire du fork (cotw/dictionnaires/hp_noms.tsv).

Il compte aussi :
- les enumerations dont DECA affiche un libelle faux (deca.ff_adf.read_instance prend le membre de RANG v au lieu
  du membre de VALEUR v) ;
- les pointeurs ADF (metatype 2), que DECA lit sans les suivre ;
- les types manquants des valeurs differees.

Les objid ne sont souvent pas des hashes de chaines (identifiants tires au hasard, 16 bits hauts a 0xffff) : leur
taux de resolution est donne a part et ne doit pas etre lu comme un manque de noms.

Sorties : rapport.md, inconnus.csv (les hashes non resolus, du plus frequent au moins frequent : l'entree de
resoudre_hashes.py) et metrics.json. La base est ouverte en lecture seule.

Usage, depuis la racine du depot, environnement virtuel active, DECA ferme :
    python cotw\\audit_noms_internes.py D:\\dev\\work\\hp --out D:\\dev\\audits\\02-noms-internes
    python cotw\\audit_noms_internes.py D:\\dev\\work\\hp --out ... --echantillon 2000      (essai rapide)
    python cotw\\audit_noms_internes.py D:\\dev\\work\\hp --out D:\\dev\\audits\\03-noms-internes ^
        --compare D:\\dev\\audits\\02-noms-internes\\metrics.json     (evolution par categorie)
"""
import argparse
import collections
import csv
import json
import multiprocessing
import os
import random
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _projet  # noqa: E402  (installe le substitut de zugbruecke sous Linux)
import noms_fichiers as NF  # noqa: E402

TYPES_RTPC = ('rtpc',)
TYPES_ADF = ('adf', 'adfb', 'adf0', 'adf5')
SENTINELLES = {0, 0xdeadbeef}
MASK48 = 0xFFFFFFFFFFFF
CATEGORIES = ('rtpc_noeud', 'rtpc_propriete', 'rtpc_event', 'rtpc_objid',
              'adf_stringhash_32', 'adf_stringhash_48', 'adf_stringhash_64')


# ---------------------------------------------------------------------------------------------------------
# Lecture d'un fichier (processus de travail)
# ---------------------------------------------------------------------------------------------------------

class Collecte:
    """Resultat d'un lot de fichiers. occ : (categorie, hash) -> [occurrences, fichiers, exemple, resolues sur place].
    'resolues sur place' = resolues par les noms du fichier lui-meme (table StringHash, chaines RTPC)."""

    def __init__(self):
        self.fichiers = collections.Counter()
        self.echecs: List[Tuple[str, str]] = []
        self.occ: Dict[Tuple[str, int], list] = {}
        self.enums: Dict[int, list] = {}          # type -> [occurrences, hors membres, libelle DECA faux, nom]
        self.pointeurs = 0
        self.types_manquants = collections.Counter()
        self.familles: Dict[Tuple[str, str], int] = collections.Counter()   # (categorie, extension) -> non resolus sur place

    def ajouter(self, cat: str, h: int, chemin: str, sur_place: bool, vus: set):
        key = (cat, h)
        e = self.occ.get(key)
        if e is None:
            e = self.occ[key] = [0, 0, chemin, 0]
        e[0] += 1
        if key not in vus:
            vus.add(key)
            e[1] += 1
        if sur_place:
            e[3] += 1
        else:
            self.familles[(cat, NF.extension_complete(chemin))] += 1

    def fusionner(self, autre: 'Collecte'):
        self.fichiers.update(autre.fichiers)
        self.echecs += autre.echecs
        for key, (n, f, ex, sp) in autre.occ.items():
            e = self.occ.get(key)
            if e is None:
                self.occ[key] = [n, f, ex, sp]
            else:
                e[0] += n
                e[1] += f
                e[3] += sp
        for t, v in autre.enums.items():
            e = self.enums.setdefault(t, [0, 0, 0, v[3]])
            for i in range(3):
                e[i] += v[i]
        self.pointeurs += autre.pointeurs
        self.types_manquants.update(autre.types_manquants)
        self.familles.update(autre.familles)


def collecter_rtpc(data: bytes, chemin: str, c: Collecte):
    from deca.ff_rtpc_write import rtpc_from_bytes
    from deca.ff_rtpc import k_type_str, k_type_objid, k_type_event
    from deca.hashes import hash32_func
    rtpc = rtpc_from_bytes(data)
    noeuds = []
    pile = [rtpc.root_node]
    chaines = set()
    while pile:
        n = pile.pop()
        noeuds.append(n)
        pile.extend(n.child_table)
        for p in n.prop_table:
            if p.type == k_type_str and isinstance(p.data, (bytes, bytearray)):
                chaines.add(int(hash32_func(bytes(p.data))) & 0xFFFFFFFF)
    vus = set()
    for i, n in enumerate(noeuds):
        if i and n.name_hash:     # la racine n'a pas de nom
            c.ajouter('rtpc_noeud', n.name_hash & 0xFFFFFFFF, chemin, (n.name_hash & 0xFFFFFFFF) in chaines, vus)
        for p in n.prop_table:
            if p.name_hash:
                c.ajouter('rtpc_propriete', p.name_hash & 0xFFFFFFFF, chemin, (p.name_hash & 0xFFFFFFFF) in chaines, vus)
            if p.type == k_type_objid and p.data:
                c.ajouter('rtpc_objid', p.data & MASK48, chemin, False, vus)
            elif p.type == k_type_event and p.data:
                for ev in p.data:
                    if ev:
                        c.ajouter('rtpc_event', ev & MASK48, chemin, False, vus)


def collecter_adf(adf, chemin: str, c: Collecte):
    from deca.ff_adf import AdfValue
    types = adf.extended_map_typedef
    vus = set()
    pile = [v for v in adf.table_instance_full_values if v is not None]
    while pile:
        v = pile.pop()
        if isinstance(v, AdfValue):
            td = types.get(v.type_id)
            meta = td.metatype if td is not None else None
            if meta == 9 and isinstance(v.value, int):
                largeur = {4: 32, 6: 48, 8: 64}.get(td.size)
                if largeur and v.value not in SENTINELLES:
                    c.ajouter('adf_stringhash_{}'.format(largeur), v.value, chemin, v.hash_string is not None, vus)
            elif meta == 8 and isinstance(v.value, int):
                membres = td.members or []
                e = c.enums.setdefault(v.type_id, [0, 0, 0, _projet.to_text(td.name)])
                e[0] += 1
                valeurs = {m.value: m.name for m in membres}
                if v.value not in valeurs:
                    e[1] += 1
                if v.value < len(membres) and membres[v.value].value != v.value:
                    e[2] += 1       # DECA affiche le membre de rang v, pas celui de valeur v
            pile.append(v.value)
        elif isinstance(v, dict):
            pile.extend(v.values())
        elif isinstance(v, list):
            if v and not isinstance(v[0], (int, float)):
                pile.extend(v)
        elif isinstance(v, tuple) and len(v) == 2 and isinstance(v[1], str) and v[1].startswith('NOTE:'):
            c.pointeurs += 1
        elif isinstance(v, str) and v.startswith('!!!MISSING TYPE'):
            c.types_manquants[v.split(' in ')[0].split()[-1]] += 1


_VFS = None
_ADF_DB = None


def _init_travail(projet: str):
    global _VFS, _ADF_DB
    from deca.ff_adf import AdfDatabase
    _VFS = _projet.open_project(projet)
    _ADF_DB = AdfDatabase(_VFS)


def _lot(lot: List[Tuple[int, str, str]]) -> Collecte:
    c = Collecte()
    for uid, chemin, file_type in lot:
        try:
            node = _VFS.node_where_uid(uid)
            if file_type in TYPES_RTPC:
                collecter_rtpc(_projet.read_node(_VFS, node), chemin, c)
            else:
                adf = _ADF_DB.read_node(_VFS, node)
                if adf is None:
                    raise ValueError('ADF illisible')
                collecter_adf(adf, chemin, c)
            c.fichiers[file_type] += 1
        except Exception as exc:  # noqa: BLE001 - un fichier illisible est compte, pas fatal
            c.echecs.append((chemin, '{}: {}'.format(type(exc).__name__, exc)))
    return c


# ---------------------------------------------------------------------------------------------------------
# Selection, resolution, rapport
# ---------------------------------------------------------------------------------------------------------

def selectionner(vfs, types: Tuple[str, ...], echantillon: int = 0, graine: int = 1) -> List[Tuple[int, str, str]]:
    """Un noeud par hash (le plus petit numero) pour les types demandes ; chemin ou '(sans nom) hash'."""
    rows = vfs.db_query_all(
        'SELECT MIN(node_id), v_hash, file_type, MAX(v_path) FROM core_nodes WHERE file_type IN ({}) '
        'AND parent_offset IS NOT NULL GROUP BY v_hash, file_type'.format(','.join('?' * len(types))), list(types),
        dbg='cotw_audit_noms_internes_select')
    out = []
    for uid, v_hash, file_type, v_path in rows:
        chemin = _projet.to_text(v_path) or '(sans nom) {:08x}'.format((v_hash or 0) & 0xFFFFFFFF)
        out.append((uid, chemin, _projet.to_text(file_type)))
    out.sort()
    if echantillon and echantillon < len(out):
        out = sorted(random.Random(graine).sample(out, echantillon))
    return out


def connus(vfs, largeur: int, hashes: List[int]) -> set:
    """Hashes de la largeur donnee qui ont une chaine connue (core_strings, dictionnaire du fork)."""
    col = {32: 'hash32', 48: 'hash48', 64: 'hash64'}[largeur]
    trouves = set()
    for (w, h) in _projet.load_dictionary():
        if w == largeur:
            trouves.add(h)
    wanted = sorted(set(hashes))
    if largeur == 64:
        # core_strings stocke hash64 signe (np.int64), comme DbWrap
        def signe(v):
            return v - (1 << 64) if v >= 1 << 63 else v
        conv = {signe(h): h for h in wanted}
        signes = list(conv)
        for i in range(0, len(signes), 500):
            chunk = signes[i:i + 500]
            for (v,) in vfs.db_query_all('SELECT {} FROM core_strings WHERE {} IN ({})'.format(
                    col, col, ','.join('?' * len(chunk))), chunk, dbg='cotw_audit_connus'):
                trouves.add(conv[v])
    else:
        for i in range(0, len(wanted), 500):
            chunk = wanted[i:i + 500]
            for (v,) in vfs.db_query_all('SELECT {} FROM core_strings WHERE {} IN ({})'.format(
                    col, col, ','.join('?' * len(chunk))), chunk, dbg='cotw_audit_connus'):
                trouves.add(v)
    return trouves & set(hashes)


def resoudre(vfs, c: Collecte) -> Dict[Tuple[str, int], str]:
    """Statut de chaque (categorie, hash) : 'sur place', 'connu' ou 'inconnu'."""
    statut = {}
    a_chercher: Dict[int, List[Tuple[str, int]]] = collections.defaultdict(list)
    for (cat, h), (n, _f, _ex, sp) in c.occ.items():
        if sp == n:
            statut[(cat, h)] = 'sur place'
            continue
        if cat in ('rtpc_noeud', 'rtpc_propriete', 'adf_stringhash_32'):
            a_chercher[32].append((cat, h))
        elif cat in ('rtpc_objid', 'rtpc_event', 'adf_stringhash_48'):
            a_chercher[48].append((cat, h))
        else:
            a_chercher[64].append((cat, h))
    trouves = {w: connus(vfs, w, [h for _c, h in keys]) for w, keys in a_chercher.items()}
    # objid et event : DECA essaie aussi le hash32 des 32 bits bas
    bas = [(cat, h) for cat, h in a_chercher.get(48, []) if cat in ('rtpc_objid', 'rtpc_event') and h not in trouves[48]]
    trouves32_bas = connus(vfs, 32, [h & 0xFFFFFFFF for _c, h in bas]) if bas else set()
    for w, keys in a_chercher.items():
        for cat, h in keys:
            ok = h in trouves[w] or (w == 48 and cat in ('rtpc_objid', 'rtpc_event') and (h & 0xFFFFFFFF) in trouves32_bas)
            statut[(cat, h)] = 'connu' if ok else 'inconnu'
    return statut


def bilan(c: Collecte, statut: Dict[Tuple[str, int], str]) -> Dict[str, dict]:
    out = {}
    for cat in CATEGORIES:
        d = {'occurrences': 0, 'distincts': 0, 'occ_resolues': 0, 'distincts_resolus': 0, 'occ_sur_place': 0}
        for (k, h), (n, _f, _ex, sp) in c.occ.items():
            if k != cat:
                continue
            d['occurrences'] += n
            d['distincts'] += 1
            s = statut[(k, h)]
            if s == 'sur place':
                d['occ_resolues'] += n
                d['distincts_resolus'] += 1
                d['occ_sur_place'] += n
            elif s == 'connu':
                d['occ_resolues'] += n
                d['distincts_resolus'] += 1
        out[cat] = d
    return out


def pct(a, b):
    return '{:.1f} %'.format(100.0 * a / b) if b else '-'


def comparer(ancien: Path, b: Dict[str, dict], echantillon: int) -> List[str]:
    """Evolution par categorie depuis un metrics.json precedent : occurrences et hashes distincts resolus."""
    old = json.loads(Path(ancien).read_text(encoding='utf-8'))
    oc = old.get('categories', {})
    L = ['## Evolution depuis `{}`{}'.format(ancien, ' ({})'.format(old['date']) if old.get('date') else ''), '']
    if old.get('echantillon', 0) != echantillon or echantillon:
        L += ['**Attention** : echantillon {} avant, {} maintenant ; les ecarts ne sont comparables que sur deux '
              'audits complets (echantillon 0).'.format(old.get('echantillon', 0), echantillon), '']
    L += ['| Categorie | Occ. resolues avant | apres | ecart | Distincts resolus avant | apres | ecart | '
          'Distincts restants |', '|---|---:|---:|---:|---:|---:|---:|---:|']
    for cat in CATEGORIES:
        o, n = oc.get(cat, {}), b[cat]
        po = 100.0 * o.get('occ_resolues', 0) / o['occurrences'] if o.get('occurrences') else 0.0
        pn = 100.0 * n['occ_resolues'] / n['occurrences'] if n['occurrences'] else 0.0
        do = 100.0 * o.get('distincts_resolus', 0) / o['distincts'] if o.get('distincts') else 0.0
        dn = 100.0 * n['distincts_resolus'] / n['distincts'] if n['distincts'] else 0.0
        L.append('| {} | {:.1f} % | {:.1f} % | {:+.1f} | {:.1f} % | {:.1f} % | {:+.1f} | {} ({:+d}) |'.format(
            cat, po, pn, pn - po, do, dn, dn - do, n['distincts'] - n['distincts_resolus'],
            (n['distincts'] - n['distincts_resolus']) - (o.get('distincts', 0) - o.get('distincts_resolus', 0))))
    L.append('')
    return L


def ecrire_sorties(out_dir: Path, c: Collecte, statut, b, duree: float, n_select: int, echantillon: int,
                   limite: int = 40, ancien: Optional[Path] = None):
    out_dir.mkdir(parents=True, exist_ok=True)
    inconnus = [((cat, h), v) for (cat, h), v in c.occ.items() if statut[(cat, h)] == 'inconnu']
    inconnus.sort(key=lambda kv: (-kv[1][0], kv[0]))
    with open(out_dir / 'inconnus.csv', 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f, delimiter=';')
        w.writerow(['categorie', 'largeur', 'hash', 'occurrences', 'fichiers', 'exemple'])
        for (cat, h), (n, nf, ex, _sp) in inconnus:
            largeur = 32 if cat in ('rtpc_noeud', 'rtpc_propriete', 'adf_stringhash_32') else (
                64 if cat == 'adf_stringhash_64' else 48)
            w.writerow([cat, largeur, '{:0{}x}'.format(h, largeur // 4), n, nf, ex])

    enums_faux = {t: e for t, e in c.enums.items() if e[2]}
    evolution = comparer(ancien, b, echantillon) if ancien else []
    metrics = {'date': time.strftime('%Y-%m-%dT%H:%M:%S'),
               'fichiers': dict(c.fichiers), 'selectionnes': n_select, 'echantillon': echantillon,
               'echecs': len(c.echecs), 'categories': b, 'pointeurs_adf': c.pointeurs,
               'types_manquants': dict(c.types_manquants),
               'enums': {'types': len(c.enums), 'occurrences': sum(e[0] for e in c.enums.values()),
                         'hors_membres': sum(e[1] for e in c.enums.values()),
                         'types_libelle_faux': len(enums_faux),
                         'occurrences_libelle_faux': sum(e[2] for e in enums_faux.values())},
               'duree_s': round(duree, 1)}
    (out_dir / 'metrics.json').write_text(json.dumps(metrics, indent=1, ensure_ascii=False), encoding='utf-8')

    L = ['# Audit des noms internes (N2)', '',
         '- Fichiers lus : {} ({}) sur {} selectionnes{} ; echecs : {} ; duree : {:.0f} s'.format(
             sum(c.fichiers.values()), ', '.join('{} {}'.format(n, t) for t, n in sorted(c.fichiers.items())),
             n_select, ' (echantillon)' if echantillon else '', len(c.echecs), duree), '',
         '| Categorie | Occurrences | Resolues | Distincts | Distincts resolus | dont sur place (occ.) |',
         '|---|---:|---:|---:|---:|---:|']
    for cat in CATEGORIES:
        d = b[cat]
        L.append('| {} | {} | {} | {} | {} | {} |'.format(
            cat, d['occurrences'], pct(d['occ_resolues'], d['occurrences']), d['distincts'],
            pct(d['distincts_resolus'], d['distincts']), d['occ_sur_place']))
    L += ['', '*Sur place* : resolu par les noms du fichier lui-meme (table StringHash de l\'ADF, chaines du RTPC). '
          'Les `rtpc_objid` sont souvent des identifiants tires au hasard, pas des hashes de chaines.', '']
    L += evolution
    m = metrics['enums']
    L += ['## Enumerations ADF', '',
          '- {} types d\'enumeration rencontres, {} valeurs lues ; {} hors des valeurs des membres.'.format(
              m['types'], m['occurrences'], m['hors_membres']),
          '- **Libelle faux dans DECA** (membre de rang v au lieu de valeur v) : {} type(s), {} valeur(s).'.format(
              m['types_libelle_faux'], m['occurrences_libelle_faux'])]
    for t, e in sorted(enums_faux.items(), key=lambda kv: -kv[1][2])[:15]:
        L.append('  - `{}` ({:08x}) : {} valeur(s) sur {}'.format(e[3], t, e[2], e[0]))
    L += ['', '## ADF : pointeurs et types manquants', '',
          '- Pointeurs (metatype 2, lus sans etre suivis) : {}'.format(c.pointeurs),
          '- Valeurs differees de type manquant : {}'.format(sum(c.types_manquants.values()) or 0)]
    for t, n in c.types_manquants.most_common(10):
        L.append('  - {} : {}'.format(t, n))
    L += ['', '## Familles de fichiers les plus touchees (occurrences non resolues sur place)', '',
          '| Categorie | Extension | Occurrences |', '|---|---|---:|']
    for (cat, ext), n in c.familles.most_common(25):
        L.append('| {} | `{}` | {} |'.format(cat, ext or '(sans)', n))
    L += ['', '## Hashes inconnus les plus frequents (liste complete : `inconnus.csv`)', '',
          '| Categorie | Hash | Occurrences | Fichiers | Exemple |', '|---|---|---:|---:|---|']
    for (cat, h), (n, nf, ex, _sp) in inconnus[:limite]:
        L.append('| {} | `{:x}` | {} | {} | `{}` |'.format(cat, h, n, nf, ex))
    if c.echecs:
        L += ['', '## Fichiers illisibles', '']
        L += ['- `{}` : {}'.format(p, e) for p, e in c.echecs[:limite]]
    (out_dir / 'rapport.md').write_text('\n'.join(L) + '\n', encoding='utf-8')
    return L


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(errors='replace')
    ap = argparse.ArgumentParser(description='Part des noms internes resolus dans les RTPC et ADF d\'un projet DECA.')
    ap.add_argument('projet', help='projet DECA (dossier contenant db\\core.db)')
    ap.add_argument('--out', required=True, help='dossier des resultats')
    ap.add_argument('--types', default='rtpc,adf', help='rtpc, adf ou rtpc,adf (defaut)')
    ap.add_argument('--echantillon', type=int, default=0, help='nombre de fichiers tires au hasard (0 = tous)')
    ap.add_argument('--processus', type=int, default=max(1, (os.cpu_count() or 2) * 3 // 4),
                    help='processus de lecture (defaut : 3/4 des coeurs, comme DECA)')
    ap.add_argument('--lot', type=int, default=200, help='fichiers par lot envoye a un processus')
    ap.add_argument('--compare', help='metrics.json d\'un audit precedent : evolution par categorie dans le rapport')
    args = ap.parse_args(argv)

    types = []
    if 'rtpc' in args.types:
        types += TYPES_RTPC
    if 'adf' in args.types:
        types += TYPES_ADF
    vfs = _projet.open_project(args.projet)
    choix = selectionner(vfs, tuple(types), args.echantillon)
    lots = [choix[i:i + args.lot] for i in range(0, len(choix), args.lot)]
    t0 = time.time()
    total = Collecte()
    print('{} fichiers en {} lots, {} processus'.format(len(choix), len(lots), args.processus), flush=True)
    if args.processus <= 1:
        _init_travail(args.projet)
        for i, lot in enumerate(lots):
            total.fusionner(_lot(lot))
    else:
        with multiprocessing.Pool(args.processus, initializer=_init_travail, initargs=(args.projet,)) as pool:
            for i, c in enumerate(pool.imap_unordered(_lot, lots)):
                total.fusionner(c)
                if (i + 1) % 25 == 0 or i + 1 == len(lots):
                    print('  {} / {} lots ({:.0f} s)'.format(i + 1, len(lots), time.time() - t0), flush=True)
    statut = resoudre(vfs, total)
    b = bilan(total, statut)
    L = ecrire_sorties(Path(args.out), total, statut, b, time.time() - t0, len(choix), args.echantillon,
                       ancien=Path(args.compare) if args.compare else None)
    print('\n'.join(L[:L.index('## Enumerations ADF')]))
    print('\nResultats : {}'.format(Path(args.out)))
    return 0


if __name__ == '__main__':
    multiprocessing.freeze_support()
    sys.exit(main())
