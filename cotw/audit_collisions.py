#!/usr/bin/env python3
"""
audit_collisions.py -- les formes de collision de COTW se decodent-elles juste ? (lecture seule, DECA ferme)

Pourquoi (2026-10-29) : deca/ff_havok_physics.py decode les formes Havok Physics 2014 (hknp) des fichiers PFX
(.pfxc, .pfs) et des .physicsc, d'apres 15 echantillons. On le passe sur tous les fichiers du jeu. Controles, qui
cassent au moindre decalage :
  - polytopes convexes : chaque sommet d'une face est sur le plan de la face (|n.v + d| petit, au plus le rayon
    convexe), aucun sommet n'est hors d'un plan (n.v + d <= 0), les normales sont unitaires, faces et indices restent
    dans leurs tableaux ;
  - formes composees : les feuilles placees par les transformations des instances restent dans la boite englobante
    rangee dans la forme (Havok peut la stocker plus large, jamais plus petite) ;
  - corps : chaque corps a une forme ; classes de formes rencontrees et non decodees ;
  - polytopes dont les contours des faces ne ferment pas la forme (compte, pas un defaut : formes plates du jeu).

Sorties (dossier --sortie) : collisions.csv (un fichier par ligne), collisions.md.

Usage (DECA ferme) :
    python cotw\\audit_collisions.py D:\\dev\\work\\hp --sortie D:\\dev\\audits\\29-collisions
"""
import argparse
import collections
import csv
import multiprocessing
import os
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _projet  # noqa: E402

SEUIL_PLAN = 0.05          # metres : au-dela, un sommet de face est juge hors de son plan (rayon convexe 0,01 a 0,1)
SEUIL_DEHORS = 1e-3        # metres : sommet hors d'un plan du polytope
SEUIL_NORMALE = 1e-3
SEUIL_BOITE = 1e-3         # metres : feuille hors de la boite englobante d'une forme composee
COMPTEURS = {'polytope': 'polytopes', 'capsule': 'capsules', 'sphere': 'spheres', 'convex': 'convexes'}


class Resultat:
    COLONNES = ['fichier', 'statut', 'detail', 'corps', 'corps_sans_forme', 'formes', 'polytopes', 'capsules',
                'spheres', 'convexes', 'composees', 'instances', 'classes_non_decodees', 'sommets', 'faces',
                'ecart_plan', 'hors_plan', 'ecart_normale', 'indices_hors', 'polytopes_ouverts', 'hors_boite',
                'geometries', 'triangles_geometrie']

    def __init__(self, fichier: str):
        self.fichier = fichier
        self.statut, self.detail = 'ok', ''
        for k in self.COLONNES[3:]:
            setattr(self, k, 0)
        self.classes_non_decodees = ''
        self.ecart_plan = self.hors_plan = self.ecart_normale = 0.0
        self.hors_boite: Optional[float] = None

    def ligne(self):
        def f(v):
            if v is None:
                return ''
            if isinstance(v, float):
                return '{:.3g}'.format(v)
            return v
        return [f(getattr(self, k)) for k in self.COLONNES]


def auditer_octets(data: bytes, chemin: str) -> Resultat:
    from deca import ff_havok as H, ff_havok_physics as F
    from deca.ff_pfx import MAGIC as PFX, packfile_bytes
    r = Resultat(chemin)
    try:
        pf = H.Packfile(packfile_bytes(data) if data[:4] == PFX else data)
    except Exception as exc:  # noqa: BLE001
        r.statut, r.detail = 'illisible', '{}: {}'.format(type(exc).__name__, str(exc)[:160])
        return r
    try:
        lecteur = F.Reader(pf)
        corps = lecteur.bodies()
        for o in pf.objects:                          # formes hors des corps (rares) : decodees aussi
            if o.class_name in F.SHAPE_CLASSES:
                lecteur.shape(o.offset)
        r.corps = len(corps)
        r.corps_sans_forme = sum(1 for b in corps if b.shape is None)
        inconnues = collections.Counter()
        for s in lecteur.shapes.values():
            r.formes += 1
            if s.kind == 'unknown':
                inconnues[s.class_name] += 1
                continue
            if s.kind == 'compound':
                r.composees += 1
                r.instances += len(s.instances)
                e = F.compound_aabb_excess(pf, s)
                if e is not None:
                    r.hors_boite = e if r.hors_boite is None else max(r.hors_boite, e)
                continue
            r.sommets += len(s.vertices)
            r.faces += len(s.faces)
            champ = COMPTEURS[s.kind]
            setattr(r, champ, getattr(r, champ) + 1)
            if s.kind in ('polytope', 'capsule'):
                e = F.polytope_errors(s)
                r.ecart_plan = max(r.ecart_plan, e['on_plane'])
                r.hors_plan = max(r.hors_plan, e['outside'])
                r.ecart_normale = max(r.ecart_normale, e['normal'])
                r.indices_hors += int(e['range'] > 0)
                if s.kind == 'polytope' and not F.polytope_closed(s):
                    r.polytopes_ouverts += 1
        geo = lecteur.geometries()
        r.geometries = len(geo)
        r.triangles_geometrie = sum(len(t) for _n, _v, t in geo)
        if any(len(t) and (t.min() < 0 or t.max() >= len(v)) for _n, v, t in geo):
            r.statut, r.detail = 'geometrie_fausse', 'indice de triangle hors des sommets'
        r.classes_non_decodees = ' '.join('{}:{}'.format(c, n) for c, n in sorted(inconnues.items()))
        if r.indices_hors:
            r.statut = 'indices_hors'
        elif r.hors_plan > SEUIL_DEHORS or r.ecart_normale > SEUIL_NORMALE:
            r.statut = 'polytope_faux'
        elif r.ecart_plan > SEUIL_PLAN:
            r.statut = 'face_hors_plan'
        elif r.hors_boite is not None and r.hors_boite > SEUIL_BOITE:
            r.statut = 'hors_boite'
        elif r.corps_sans_forme:
            r.statut = 'corps_sans_forme'
        elif r.statut == 'ok' and inconnues:
            r.statut = 'classes_non_decodees'
    except Exception as exc:  # noqa: BLE001 - un fichier en echec est compte, pas fatal
        r.statut, r.detail = 'echec', '{}: {}'.format(type(exc).__name__, str(exc)[:160])
    return r


_VFS = None


def _init_travail(projet: str):
    global _VFS
    _VFS = _projet.open_project(projet)


def _lot(lot: List[Tuple[int, str]]) -> List[Resultat]:
    out = []
    for uid, chemin in lot:
        try:
            data = _projet.read_node(_VFS, _VFS.node_where_uid(uid))
        except Exception as exc:  # noqa: BLE001
            r = Resultat(chemin)
            r.statut, r.detail = 'lecture', '{}: {}'.format(type(exc).__name__, str(exc)[:160])
            out.append(r)
            continue
        out.append(auditer_octets(data, chemin))
    return out


def selectionner(vfs) -> List[Tuple[int, str]]:
    """Un noeud par fichier : type pfx (.pfxc, .pfs), et les .physicsc."""
    rows = vfs.db_query_all("SELECT MIN(node_id), v_path, v_hash FROM core_nodes WHERE file_type = 'pfx' "
                            "OR CAST(v_path AS TEXT) LIKE '%.physicsc' GROUP BY COALESCE(CAST(v_path AS TEXT), v_hash)",
                            [], dbg='cotw_audit_collisions')
    out = [(uid, _projet.to_text(p) if p else '(sans nom) {:08x}'.format((h or 0) & 0xFFFFFFFF)) for uid, p, h in rows]
    return sorted(out, key=lambda t: t[1])


def ecrire(sortie: Path, resultats: List[Resultat], projet: str, duree: float) -> List[str]:
    sortie.mkdir(parents=True, exist_ok=True)
    with open(sortie / 'collisions.csv', 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f, delimiter=';')
        w.writerow(Resultat.COLONNES)
        for r in resultats:
            w.writerow(r.ligne())
    statuts = collections.Counter(r.statut for r in resultats)
    lus = [r for r in resultats if r.statut not in ('illisible', 'lecture', 'echec')]
    ext = collections.Counter(Path(r.fichier).suffix or '(sans nom)' for r in resultats)
    L = ['# Audit des formes de collision (deca/ff_havok_physics.py)', '',
         '- Projet : `{}` ; {} fichiers ({}) ; duree : {:.0f} s'.format(
             projet, len(resultats), ', '.join('{} {}'.format(k, n) for k, n in ext.most_common()), duree),
         '- **Decodes sans defaut : {} / {}**'.format(statuts.get('ok', 0), len(resultats)), '',
         '| Statut | Fichiers |', '|---|---:|']
    L += ['| {} | {} |'.format(s, n) for s, n in statuts.most_common()]
    tot = {k: sum(getattr(r, k) for r in lus) for k in ('corps', 'formes', 'polytopes', 'capsules', 'spheres',
                                                       'convexes', 'composees', 'instances', 'sommets', 'faces',
                                                       'geometries', 'triangles_geometrie', 'polytopes_ouverts')}
    L += ['', '## Formes', '',
          '- Corps : {corps} ; formes : {formes} (polytopes {polytopes}, capsules {capsules}, spheres {spheres}, '
          'convexes sans faces {convexes}, composees {composees} a {instances} instances)'.format(**tot),
          '- Sommets : {sommets} ; faces : {faces} ; hkGeometry : {geometries} ({triangles_geometrie} triangles)'.format(**tot),
          '- Polytopes dont les contours des faces ne ferment pas la forme (plats ou degeneres ; les autres prouvent '
          'l\'ordre des indices) : {polytopes_ouverts} sur {polytopes}'.format(**tot)]
    inc = collections.Counter()
    for r in lus:
        for x in r.classes_non_decodees.split():
            c, n = x.split(':')
            inc[c] += int(n)
    L.append('- Classes de formes non decodees : {}'.format(', '.join('{} x {}'.format(c, n) for c, n in inc.most_common())
                                                           or 'aucune'))
    L += ['', '## Controles (maximum par fichier ; mediane, 99e centile et maximum sur les fichiers)', '',
          '| Controle | Fichiers | Mediane | 99e centile | Maximum | Seuil |', '|---|---:|---:|---:|---:|---:|']
    for k, nom, seuil in (('ecart_plan', 'sommet de face hors de son plan (m)', SEUIL_PLAN),
                          ('hors_plan', 'sommet hors d\'un plan du polytope (m)', SEUIL_DEHORS),
                          ('ecart_normale', 'normale de plan : ecart de sa norme a 1', SEUIL_NORMALE),
                          ('hors_boite', 'feuille hors de la boite d\'une forme composee (m)', SEUIL_BOITE)):
        v = np.array([getattr(r, k) for r in lus if getattr(r, k) is not None
                      and (k != 'hors_boite' or r.composees)], dtype=float)
        if len(v):
            L.append('| {} | {} | {:.3g} | {:.3g} | {:.3g} | {:.3g} |'.format(
                nom, len(v), float(np.median(v)), float(np.percentile(v, 99)), float(v.max()), seuil))
    autres = [r for r in resultats if r.statut != 'ok']
    if autres:
        L += ['', '## Exemples par statut', '']
        par = collections.defaultdict(list)
        for r in autres:
            par[(r.statut, r.detail.split(':')[0] if r.detail else r.classes_non_decodees[:60])].append(r)
        for (s, d), rs in sorted(par.items(), key=lambda kv: -len(kv[1])):
            L.append('- **{}** {} ({}) : {}'.format(s, d, len(rs), ' ; '.join(
                '`{}`{}'.format(r.fichier, ' ({})'.format(r.detail) if r.detail and len(rs) < 4 else '') for r in rs[:3])))
    (sortie / 'collisions.md').write_text('\n'.join(L) + '\n', encoding='utf-8')
    return L


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('projet', help='projet DECA (dossier contenant db\\core.db)')
    ap.add_argument('--sortie', required=True, help='dossier des resultats')
    ap.add_argument('--processus', type=int, default=max(1, (os.cpu_count() or 2) * 3 // 4),
                    help='processus de lecture (defaut : 3/4 des coeurs)')
    ap.add_argument('--lot', type=int, default=40, help='fichiers par lot envoye a un processus')
    args = ap.parse_args(argv)
    t0 = time.time()
    vfs = _projet.open_project(args.projet)
    choix = selectionner(vfs)
    print('{} fichiers physiques'.format(len(choix)), flush=True)
    lots = [choix[i:i + args.lot] for i in range(0, len(choix), args.lot)]
    resultats: List[Resultat] = []
    if args.processus <= 1:
        _init_travail(args.projet)
        for lot in lots:
            resultats += _lot(lot)
    else:
        with multiprocessing.Pool(args.processus, initializer=_init_travail, initargs=(args.projet,)) as pool:
            for i, res in enumerate(pool.imap_unordered(_lot, lots)):
                resultats += res
                if (i + 1) % 25 == 0 or i + 1 == len(lots):
                    print('  {} / {} lots ({:.0f} s)'.format(i + 1, len(lots), time.time() - t0), flush=True)
    resultats.sort(key=lambda r: r.fichier)
    L = ecrire(Path(args.sortie), resultats, args.projet, time.time() - t0)
    texte = '\n'.join(L[:L.index('## Exemples par statut')] if '## Exemples par statut' in L else L)
    enc = sys.stdout.encoding or 'utf-8'
    print((texte + '\n\nResultats : {}'.format(args.sortie)).encode(enc, errors='replace').decode(enc))
    return 0


if __name__ == '__main__':
    multiprocessing.freeze_support()
    sys.exit(main())
