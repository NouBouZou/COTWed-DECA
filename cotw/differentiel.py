#!/usr/bin/env python3
"""
differentiel.py -- quels fichiers citent les objets presents en jeu, et pas les objets absents ?

Lit l'index construit par indexer_refs.py. Le registre qui decide de la disponibilite d'un objet doit citer
les objets presents en jeu et ne citer aucun des objets absents. equipment_data.bin et les spawn defs citent
les deux groupes : ils ne peuvent pas etre ce registre.

Groupes :
  absents  : par defaut, les munitions que cotw-mod-builder (name_map.yaml) signale comme presentes dans les
             fichiers mais absentes du jeu. --absents pour une autre liste.
  presents : par defaut, tous les autres objets des memes categories. C'est une approximation : un objet
             peut etre absent sans que la communaute l'ait signale. Pour un resultat exact, donner la liste
             des objets vus au magasin avec --presents-fichier (un nom par ligne).

Pour chaque fichier, deux probabilites :
  bruit         : probabilite que le hasard explique autant d'identifiants trouves. Elle tient compte du
                  nombre de positions examinees : positions x motifs / 2^32 coincidences attendues (loi de
                  Poisson). Un gros fichier binaire a toujours quelques coincidences.
  exclusion     : probabilite qu'un fichier citant k objets au hasard parmi presents + absents n'ait tire
                  aucun absent (loi hypergeometrique). Plus elle est faible, plus l'absence des absents est
                  significative : citer 113 presents sur 113 et 0 absent sur 5 donne 1 / C(118, 5).

Usage, depuis la racine du depot (DECA peut rester ouvert : seul l'index est lu) :
    python cotw\\differentiel.py D:\\dev\\work\\hp --out D:\\dev\\audits\\differentiel.md
    python cotw\\differentiel.py D:\\dev\\work\\hp --presents-fichier D:\\dev\\audits\\vus_au_magasin.txt --out ...
    python cotw\\differentiel.py D:\\dev\\work\\hp --objet equipment_ammo_243_sp_01 --out D:\\dev\\audits\\refs_243.md
"""
import argparse
import collections
import json
import math
import re
import sqlite3
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

# Source : cotw-mod-builder (xuexu), name_map.yaml, commit 3ac2b0c (2026-09-17) : munitions marquees
# "not available in-game" ou _PLACEHOLDER.
DEFAULT_ABSENTS = [
    'equipment_ammo_762x54r_fmj_01',
    'equipment_ammo_410_slug_01',
    'equipment_ammo_28ga_bird_01',
    'equipment_ammo_28ga_buck_01',
    'equipment_ammo_28ga_slug_01',
]
INDEX_NAME = 'cotw_refs.db'
LN10 = math.log(10)


# ---------------------------------------------------------------------------------------------------------
# Probabilites
# ---------------------------------------------------------------------------------------------------------

def poisson_sf_log10(k: int, lam: float) -> float:
    """log10 P(X >= k) pour X ~ Poisson(lam)."""
    if k <= 0:
        return 0.0
    if lam <= 0:
        return -math.inf
    if lam >= k:
        cdf = sum(math.exp(-lam + i * math.log(lam) - math.lgamma(i + 1)) for i in range(k))
        return math.log10(max(1.0 - cdf, 1e-300))
    terms = [-lam + i * math.log(lam) - math.lgamma(i + 1) for i in range(k, k + 400)]
    m = max(terms)
    return (m + math.log(sum(math.exp(t - m) for t in terms))) / LN10


def exclusion_log10(n_present: int, n_absent: int, k: int) -> float:
    """log10 de C(P, k) / C(P + U, k) : probabilite de ne tirer aucun absent en citant k objets au hasard."""
    if k > n_present:
        return -math.inf
    lc = lambda n, r: math.lgamma(n + 1) - math.lgamma(r + 1) - math.lgamma(n - r + 1)  # noqa: E731
    return (lc(n_present, k) - lc(n_present + n_absent, k)) / LN10


def fmt_p(log10p: float) -> str:
    if log10p == -math.inf:
        return '0'
    if log10p > -2:
        return '{:.2f}'.format(10 ** log10p)
    return '1e{:.0f}'.format(log10p)


# ---------------------------------------------------------------------------------------------------------
# Lecture de l'index
# ---------------------------------------------------------------------------------------------------------

class Index:
    def __init__(self, path: Path):
        if not path.is_file():
            raise SystemExit('ERREUR : index introuvable : {} (le construire avec indexer_refs.py)'.format(path))
        self.db = sqlite3.connect(str(path))
        self.meta = dict(self.db.execute('SELECT key, value FROM meta').fetchall())
        self.sizes = json.loads(self.meta.get('motifs', '{}'))
        self.idents = {r[0]: r[1:] for r in self.db.execute('SELECT id, item, category, role, text FROM identifiers')}
        self.items: Dict[str, str] = {}
        for item, category, _role, _text in self.idents.values():
            self.items.setdefault(item, category)
        self.files = {r[0]: r[1:] for r in self.db.execute(
            'SELECT file_id, label, file_type, size, mode, status, n32, n48, n64 FROM files')}

    def file_items(self, roles: Optional[Set[str]] = None):
        """file_id -> {objet: {(role, encodage)}}, et file_id -> identifiants distincts par encodage."""
        per_file: Dict[int, Dict[str, Set[Tuple[str, str]]]] = collections.defaultdict(lambda: collections.defaultdict(set))
        distinct: Dict[int, collections.Counter] = collections.defaultdict(collections.Counter)
        for file_id, ident_id, encoding in self.db.execute('SELECT file_id, ident_id, encoding FROM hits'):
            item, _category, role, _text = self.idents[ident_id]
            distinct[file_id][encoding] += 1
            if roles is not None and role not in roles:
                continue
            per_file[file_id][item].add((role, encoding))
        return per_file, distinct

    def noise_lambda(self, file_id: int) -> float:
        """Nombre d'identifiants que le hasard ferait trouver dans ce fichier (hashes ; texte : negligeable)."""
        _label, _ft, _size, _mode, _status, n32, n48, n64 = self.files[file_id]
        return ((n32 or 0) * self.sizes.get('hash32', 0) / 2 ** 32 + (n48 or 0) * self.sizes.get('hash48', 0) / 2 ** 48
                + (n64 or 0) * self.sizes.get('hash64', 0) / 2 ** 64)

    def details(self, file_id: int, items: Sequence[str], limit: int = 2) -> List[str]:
        wanted = {i for i, (item, *_r) in self.idents.items() if item in set(items)}
        out = []
        for ident_id, encoding, offsets, details in self.db.execute(
                'SELECT ident_id, encoding, offsets, details FROM hits WHERE file_id = ?', (file_id,)):
            if ident_id not in wanted:
                continue
            d = json.loads(details)
            o = json.loads(offsets)
            where = d[0] if d else ('offset 0x{:x}'.format(o[0]) if o else '')
            out.append('{} ({}, {}) : {}'.format(self.idents[ident_id][0], self.idents[ident_id][2], encoding, where))
            if len(out) >= limit:
                break
        return out


# ---------------------------------------------------------------------------------------------------------
# Rapport
# ---------------------------------------------------------------------------------------------------------

def read_names(path: Path) -> List[str]:
    return [line.strip() for line in path.read_text(encoding='utf-8').splitlines()
            if line.strip() and not line.startswith('#')]


def cell(text: str, limit: int = 80) -> str:
    text = str(text).replace('|', '/').replace('\n', ' ')
    return text if len(text) <= limit else text[:limit - 3] + '...'


def differential(index: Index, presents: List[str], absents: List[str], roles: Optional[Set[str]],
                 max_noise_log10: float, top: int) -> List[str]:
    P, U = set(presents), set(absents)
    per_file, distinct = index.file_items(roles)
    rows = []
    noise_only = []
    for file_id, items in per_file.items():
        # Une citation en texte est sure. Les citations par hash ne comptent que si leur nombre depasse
        # nettement ce que le hasard explique pour ce fichier (positions examinees x motifs / 2^32).
        lam = index.noise_lambda(file_id)
        k_hash = sum(n for enc, n in distinct[file_id].items() if enc != 'texte')
        noise = poisson_sf_log10(k_hash, lam) if k_hash else -math.inf
        hash_ok = noise <= max_noise_log10
        cited = {it for it, refs in items.items()
                 if any(enc == 'texte' or hash_ok for _r, enc in refs)}
        kp, ku = len(cited & P), len(cited & U)
        if kp == 0 and ku == 0:
            if items:
                noise_only.append(file_id)
            continue
        excl = exclusion_log10(len(P), len(U), kp) if ku == 0 else None
        encodings = sorted({enc for it in cited & (P | U) for _r, enc in items[it]
                            if enc == 'texte' or hash_ok})
        rows.append(dict(file_id=file_id, kp=kp, ku=ku, lam=lam, noise=noise if k_hash else None, excl=excl,
                         encodings=encodings, absents=sorted(cited & U), missing=sorted(P - cited)))

    def label(r):
        lbl, ftype, size, mode, *_ = index.files[r['file_id']]
        return lbl, ftype or 'sans type', size, mode

    candidates = [r for r in rows if r['ku'] == 0]
    candidates.sort(key=lambda r: (r['excl'], -r['kp']))
    leaks = [r for r in rows if r['ku'] > 0]
    leaks.sort(key=lambda r: (-r['kp'] / max(len(P), 1) + r['ku'] / max(len(U), 1), -r['kp']))

    L = ['# Différentiel : présents en jeu contre absents', '',
         '- Index : {} fichiers ; créé le {}'.format(len(index.files), index.meta.get('created', '?')),
         '- Absents ({}) : {}'.format(len(U), ', '.join('`{}`'.format(a) for a in sorted(U))),
         '- Présents : {} objets'.format(len(P)),
         '- Rôles comptés : {}'.format(', '.join(sorted(roles)) if roles else 'tous (nom, entité, icône, clés de traduction...)'),
         '- Fichiers dont les seules correspondances s\'expliquent par le hasard (probabilité > 10^{:.0f}), écartés : {}'.format(
             max_noise_log10, len(noise_only)),
         '',
         '## 1. Candidats : citent des présents et aucun absent', '',
         'Classés par probabilité d\'exclusion (plus elle est petite, plus l\'absence des absents est significative).', '',
         '| # | Fichier | Type | Lecture | Présents cités | Encodages | Hasard | Exclusion | Exemple |',
         '|---:|---|---|---|---:|---|---:|---:|---|']
    for n, r in enumerate(candidates[:top], 1):
        lbl, ftype, size, mode = label(r)
        sample = index.details(r['file_id'], sorted(P - set(r['missing']))[:1], 1)
        L.append('| {} | `{}` | {} | {} | {}/{} ({:.0%}) | {} | {} | {} | {} |'.format(
            n, cell(lbl, 70), ftype, mode or '-', r['kp'], len(P), r['kp'] / max(len(P), 1), ', '.join(r['encodings']),
            '-' if r['noise'] is None else fmt_p(r['noise']), fmt_p(r['excl']), cell(sample[0] if sample else '', 70)))
    if not candidates:
        L.append('| | aucun | | | | | | | |')
    L.append('')

    shown = [r for r in candidates[:top] if r['missing'] and r['kp'] >= 0.5 * len(P)]
    if shown:
        L += ['### Présents non cités par les meilleurs candidats', '',
              'Si un candidat couvre presque tous les présents, ceux qui manquent sont peut-être absents eux aussi, '
              'ou le registre est partiel (par catégorie, par DLC).', '']
        for r in shown[:5]:
            L.append('- `{}` : {}{}'.format(cell(label(r)[0], 70), ', '.join('`{}`'.format(m) for m in r['missing'][:25]),
                                           ' …' if len(r['missing']) > 25 else ''))
        L.append('')

    L += ['## 2. Non discriminants : citent aussi des absents', '',
          '| Fichier | Type | Présents cités | Absents cités | Lesquels |', '|---|---|---:|---:|---|']
    for r in leaks[:top]:
        lbl, ftype, size, mode = label(r)
        L.append('| `{}` | {} | {}/{} | {}/{} | {} |'.format(cell(lbl, 70), ftype, r['kp'], len(P), r['ku'], len(U),
                                                         ', '.join('`{}`'.format(a) for a in r['absents'])))
    L.append('')
    errors = [f for f in index.files.values() if f[4] == 'erreur']
    L += ['## 3. Couverture de l\'index', '',
          '- Fichiers lus : {} ; en erreur : {}'.format(len(index.files), len(errors)),
          '- Motifs : {}'.format(', '.join('{} {}'.format(n, k) for k, n in sorted(index.sizes.items()))),
          '- Options : `{}`'.format(index.meta.get('options', '')), '']
    return L


def object_report(index: Index, name: str) -> List[str]:
    ids = {i: v for i, v in index.idents.items() if v[0] == name}
    if not ids:
        raise SystemExit('ERREUR : objet inconnu de l\'index : {}'.format(name))
    L = ['# Citations de `{}`'.format(name), '', 'Chaînes cherchées :', '']
    for i, (_item, category, role, text) in sorted(ids.items()):
        L.append('- {} : `{}` ({})'.format(role, text, category))
    L += ['', '| Fichier | Type | Rôle | Encodage | Nombre | Où |', '|---|---|---|---|---:|---|']
    rows = index.db.execute(
        'SELECT h.file_id, h.ident_id, h.encoding, h.count, h.offsets, h.details FROM hits h WHERE h.ident_id IN ({}) '
        'ORDER BY h.file_id'.format(','.join('?' * len(ids))), list(ids)).fetchall()
    for file_id, ident_id, encoding, count, offsets, details in rows:
        lbl, ftype, size, mode, *_ = index.files[file_id]
        d, o = json.loads(details), json.loads(offsets)
        where = '; '.join(d) if d else ', '.join('0x{:x}'.format(x) for x in o)
        lam = index.noise_lambda(file_id)
        noise = ' (hasard attendu ~{:.2g} identifiant(s) dans ce fichier)'.format(lam) if encoding != 'texte' and lam > 0.01 else ''
        L.append('| `{}` | {} | {} | {} | {} | {}{} |'.format(cell(lbl, 70), ftype or 'sans type', ids[ident_id][2],
                                                          encoding, count, cell(where, 90), noise))
    if not rows:
        L.append('| aucun | | | | | |')
    return L


HASH_IN_DETAIL = re.compile(r'(propriete|parent) 0x([0-9a-f]{8})')


def annotate(text: str, project: str) -> str:
    """Ajoute le nom connu (DECA + dictionnaire du fork) apres les hashes de proprietes et de noeuds parents."""
    hashes = {int(m.group(2), 16) for m in HASH_IN_DETAIL.finditer(text)}
    if not hashes:
        return text
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import _projet
        names = _projet.resolve_hash32(_projet.open_project(project), hashes)
    except (SystemExit, Exception):  # projet DECA absent : on garde les hashes seuls
        return text

    def repl(m):
        n = names.get(int(m.group(2), 16))
        return m.group(0) + ' ({})'.format('|'.join(n[:2])) if n else m.group(0)
    return HASH_IN_DETAIL.sub(repl, text)


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(errors='replace')
    ap = argparse.ArgumentParser(description='Fichiers qui citent les objets presents et pas les absents.')
    ap.add_argument('projet', help='projet DECA (dossier contenant cotw_refs.db)')
    ap.add_argument('--index', help='index (defaut : <projet>\\cotw_refs.db)')
    ap.add_argument('--absents', help='objets absents du jeu, separes par des virgules (defaut : placeholders du Mod Builder)')
    ap.add_argument('--absents-fichier', help='fichier : un objet absent par ligne')
    ap.add_argument('--presents', help='objets presents, separes par des virgules')
    ap.add_argument('--presents-fichier', help='fichier : un objet present par ligne (ex : vus au magasin)')
    ap.add_argument('--categorie', action='append', default=[],
                    help='categories dont les objets non absents sont presents par defaut (defaut : celles des absents)')
    ap.add_argument('--roles', help='roles comptes, ex : nom,entite (defaut : tous)')
    ap.add_argument('--bruit-max', type=float, default=-3, help='log10 de la probabilite de hasard maximale (defaut -3)')
    ap.add_argument('--top', type=int, default=40, help='lignes par tableau')
    ap.add_argument('--objet', help='au lieu du differentiel : toutes les citations de cet objet')
    ap.add_argument('--out', help='rapport markdown (sinon : affichage a l\'ecran)')
    args = ap.parse_args(argv)

    index = Index(Path(args.index) if args.index else Path(args.projet) / INDEX_NAME)
    if args.objet:
        L = object_report(index, args.objet)
    else:
        absents = (read_names(Path(args.absents_fichier)) if args.absents_fichier else
                   [a.strip() for a in args.absents.split(',')] if args.absents else list(DEFAULT_ABSENTS))
        unknown = [a for a in absents if a not in index.items]
        absents = [a for a in absents if a in index.items]
        for a in unknown:
            print('ATTENTION : absent inconnu de l\'index, ignore : {}'.format(a))
        if not absents:
            raise SystemExit('ERREUR : aucun objet absent connu de l\'index')
        if args.presents_fichier or args.presents:
            presents = (read_names(Path(args.presents_fichier)) if args.presents_fichier else
                        [p.strip() for p in args.presents.split(',')])
            for p in [p for p in presents if p not in index.items]:
                print('ATTENTION : present inconnu de l\'index, ignore : {}'.format(p))
            presents = [p for p in presents if p in index.items and p not in absents]
        else:
            cats = set(args.categorie) or {index.items[a] for a in absents}
            presents = sorted(i for i, c in index.items.items() if c in cats and i not in absents)
        roles = {r.strip() for r in args.roles.split(',')} if args.roles else None
        L = differential(index, presents, absents, roles, args.bruit_max, args.top)
    text = annotate('\n'.join(L) + '\n', args.projet)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding='utf-8')
        print('Ecrit : {}'.format(args.out))
    else:
        sys.stdout.write(text)
    return 0


if __name__ == '__main__':
    sys.exit(main())
