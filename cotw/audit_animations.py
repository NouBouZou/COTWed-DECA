#!/usr/bin/env python3
"""
audit_animations.py -- les animations de COTW (.ban) se decodent-elles juste ? (lecture seule, DECA ferme)

Pourquoi (2026-10-28) : deca/ff_havok_anim.py decode les hkaSplineCompressedAnimation (disposition relevee sur 3
echantillons, 17 animations). Avant de s'en servir (export glTF, modloader), on le passe sur tous les .ban du jeu.
Pour chaque animation :
  - structure : chaque bloc doit finir exactement a m_floatBlockOffsets (une erreur de disposition decale tout) ;
  - continuite : la derniere image d'un bloc et la premiere du suivant sont la meme image, codee deux fois ; l'ecart
    (angle de rotation, translation, echelle) doit rester a l'ordre de la quantification ;
  - quaternions : norme des rotations avant normalisation (1 attendu pour les quaternions non compresses) ;
  - quantifications vues (position, rotation, echelle) : celles que le decodeur ne sait pas lire sont comptees.

Sorties (dossier --sortie) : animations.csv (une animation par ligne), animations.md (statuts, quantifications,
ecarts de continuite, echecs).

Usage (DECA ferme) :
    python cotw\\audit_animations.py D:\\dev\\work\\hp --sortie D:\\dev\\audits\\21-animations
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

# Au-dela, la continuite entre blocs est jugee rompue. 21-animations : sans THREECOMP40, ecart maximal 4,9e-6 rad ;
# avec, 4,9e-3 rad et 2 mm en translation (memes animations) : tolerance de l'ajustement des splines a la compression,
# pas une erreur de lecture.
SEUIL_ROTATION = 1e-2        # radians
SEUIL_TRANSLATION = 1e-2     # unites du jeu (metres)


class Resultat:
    COLONNES = ['fichier', 'index', 'statut', 'detail', 'classe', 'images', 'pistes', 'pistes_flottantes', 'blocs',
                'duree', 'fins_justes', 'ecart_rotation', 'ecart_translation', 'ecart_echelle', 'norme_quaternions',
                'quaternions_compactes', 'compactes_coherents', 'masques_flottants', 'fins_flottants',
                'flottant_max', 'quantifications', 'liaison']

    def __init__(self, fichier: str, index: int):
        self.fichier, self.index = fichier, index
        self.statut, self.detail, self.classe = 'ok', '', ''
        self.images = self.pistes = self.pistes_flottantes = self.blocs = 0
        self.duree = 0.0
        self.fins_justes = ''
        self.ecart_rotation: Optional[float] = None
        self.ecart_translation: Optional[float] = None
        self.ecart_echelle: Optional[float] = None
        self.norme_quaternions: Optional[float] = None
        self.quaternions_compactes = 0
        self.compactes_coherents = 0
        self.masques_flottants = ''
        self.fins_flottants = ''
        self.flottant_max: Optional[float] = None
        self.quantifications = collections.Counter()
        self.liaison = ''

    def ligne(self):
        def f(v):
            if v is None:
                return ''
            if isinstance(v, float):
                return '{:.3g}'.format(v)
            if isinstance(v, collections.Counter):
                return ' '.join('{}/{}/{}:{}'.format(a, b, c, n) for (a, b, c), n in sorted(v.items()))
            return v
        return [f(getattr(self, k)) for k in self.COLONNES]


def auditer_fichier(data: bytes, chemin: str) -> List[Resultat]:
    from deca import ff_havok, ff_havok_anim
    try:
        pf = ff_havok.Packfile(data)
    except Exception as exc:  # noqa: BLE001
        r = Resultat(chemin, -1)
        r.statut, r.detail = 'packfile_illisible', '{}: {}'.format(type(exc).__name__, str(exc)[:160])
        return [r]
    out = []
    autres = [o for o in pf.objects if o.class_name in ff_havok.ANIMATION_CLASSES
              and o.class_name != 'hkaSplineCompressedAnimation']
    for o in autres:
        r = Resultat(chemin, len(out))
        r.statut, r.classe = 'classe_non_decodee', o.class_name
        out.append(r)
    for o, binding in ff_havok_anim.animations_of(pf):
        r = Resultat(chemin, len(out))
        r.classe = o.class_name
        r.liaison = '' if binding is None else str(binding.get('blendHint'))
        try:
            info = ff_havok.decode_animation(pf, o)
            r.pistes, r.pistes_flottantes = info['numberOfTransformTracks'], info['numberOfFloatTracks']
            r.images, r.blocs, r.duree = info['numFrames'], info['numBlocks'], info['duration']
            d = ff_havok_anim.decode(pf, o)
            r.quantifications = d['quantizations']
            fins = [b['end_ok'] for b in d['blocks']]
            r.fins_justes = '{}/{}'.format(sum(fins), len(fins))
            fins_f = [b['float_end_ok'] for b in d['blocks']]
            if r.pistes_flottantes:
                r.fins_flottants = '{}/{}'.format(sum(fins_f), len(fins_f))
                r.flottant_max = float(abs(d['floats']).max()) if d['floats'].size else 0.0
            r.ecart_rotation = d['continuity']['rotation']
            r.ecart_translation = d['continuity']['translation']
            r.ecart_echelle = d['continuity']['scale']
            r.norme_quaternions = d['quaternion_norm_error']
            r.quaternions_compactes, r.compactes_coherents = d['packed_quaternions']
            r.masques_flottants = ' '.join('{:02x}:{}'.format(m, n) for m, n in sorted(d['float_masks'].items()))
            if not all(fins):
                r.statut = 'fin_de_bloc_fausse'
            elif not all(fins_f):
                r.statut = 'flottants_fin_fausse'
            elif r.ecart_rotation > SEUIL_ROTATION or r.ecart_translation > SEUIL_TRANSLATION:
                r.statut = 'discontinue'
        except ff_havok_anim.EHavokAnim as exc:
            r.statut, r.detail = 'non_decode', str(exc)[:160]
        except Exception as exc:  # noqa: BLE001 - une animation en echec est comptee, pas fatale
            r.statut, r.detail = 'echec', '{}: {}'.format(type(exc).__name__, str(exc)[:160])
        out.append(r)
    if not out:
        r = Resultat(chemin, -1)
        r.statut = 'sans_animation'
        out.append(r)
    return out


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
            r = Resultat(chemin, -1)
            r.statut, r.detail = 'lecture', '{}: {}'.format(type(exc).__name__, str(exc)[:160])
            out.append(r)
            continue
        out += auditer_fichier(data, chemin)
    return out


def selectionner(vfs, echantillon: int = 0, graine: int = 1) -> List[Tuple[int, str]]:
    """Un noeud Havok par .ban nomme (le plus petit numero)."""
    rows = vfs.db_query_all("SELECT MIN(node_id), v_path FROM core_nodes WHERE CAST(v_path AS TEXT) LIKE '%.ban' "
                            "AND file_type = 'h2014' GROUP BY v_path", [], dbg='cotw_audit_animations')
    out = sorted((uid, _projet.to_text(p)) for uid, p in rows)
    if echantillon and echantillon < len(out):
        import random
        out = sorted(random.Random(graine).sample(out, echantillon))
    return out


def ecrire(sortie: Path, resultats: List[Resultat], projet: str, duree: float, n_fichiers: int) -> List[str]:
    sortie.mkdir(parents=True, exist_ok=True)
    with open(sortie / 'animations.csv', 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f, delimiter=';')
        w.writerow(Resultat.COLONNES)
        for r in resultats:
            w.writerow(r.ligne())
    statuts = collections.Counter(r.statut for r in resultats)
    anims = [r for r in resultats if r.index >= 0 and r.classe == 'hkaSplineCompressedAnimation']
    ok = [r for r in anims if r.statut == 'ok']
    L = ['# Audit des animations (.ban, deca/ff_havok_anim.py)', '',
         '- Projet : `{}` ; {} fichiers .ban ; duree : {:.0f} s'.format(projet, n_fichiers, duree),
         '- **Animations a splines decodees sans defaut : {} / {}** ({:.1f} %)'.format(
             len(ok), len(anims), 100.0 * len(ok) / max(1, len(anims))),
         '- Images : {} ; pistes x images : {}'.format(sum(r.images for r in anims),
                                                      sum(r.images * r.pistes for r in anims)),
         '- Dont {} avec des pistes de flottants, dont la fin des donnees tombe sur la fin du bloc (disposition '
         'supposee : deca/ff_havok_anim.py)'.format(sum(1 for r in ok if r.pistes_flottantes)), '',
         '| Statut | Animations ou fichiers |', '|---|---:|']
    L += ['| {} | {} |'.format(s, n) for s, n in statuts.most_common()]
    quant = collections.Counter()
    for r in anims:
        quant.update(r.quantifications)
    noms_r = {0: 'POLAR32', 1: 'THREECOMP40', 2: 'THREECOMP48', 3: 'THREECOMP24', 4: 'STRAIGHT16', 5: 'UNCOMPRESSED'}
    L += ['', '## Quantifications (position / rotation / echelle, par piste et par bloc)', '',
          '| Position | Rotation | Echelle | Pistes x blocs |', '|---|---|---|---:|']
    for (a, b, c), n in quant.most_common():
        L.append('| {} | {} | {} | {} |'.format({0: '8 bits', 1: '16 bits'}.get(a, a), noms_r.get(b, b),
                                               {0: '8 bits', 1: '16 bits'}.get(c, c), n))
    L += ['', '## Continuite entre blocs (animations a plusieurs blocs)', '',
          '| Animations | Ecart | Nombre | Mediane | 99e centile | Maximum |', '|---|---|---:|---:|---:|---:|']
    for groupe, sel in (('rotations non compressees', [r for r in anims if not r.quaternions_compactes]),
                        ('avec THREECOMP40 / 48', [r for r in anims if r.quaternions_compactes])):
        for k, nom in (('ecart_rotation', 'rotation (rad)'), ('ecart_translation', 'translation'),
                       ('ecart_echelle', 'echelle'),
                       ('norme_quaternions', 'norme des quaternions stockes - 1 (toutes)')):
            v = np.array([getattr(r, k) for r in sel if getattr(r, k) is not None
                          and (r.blocs > 1 or k == 'norme_quaternions')])
            if len(v):
                L.append('| {} | {} | {} | {:.3g} | {:.3g} | {:.3g} |'.format(
                    groupe, nom, len(v), float(np.median(v)), float(np.percentile(v, 99)), float(v.max())))
    lus = sum(r.quaternions_compactes for r in anims)
    if lus:
        coh = sum(r.compactes_coherents for r in anims)
        L += ['', '- Quaternions compactes (THREECOMP40 / 48) : {} lus, {:.2f} % avec la composante reconstruite la '
              'plus grande (100 % attendu si le decodage est juste)'.format(lus, 100.0 * coh / lus)]
    masques = collections.Counter()
    for r in anims:
        for m in r.masques_flottants.split():
            k, n = m.split(':')
            masques[k] += int(n)
    if masques:
        valeurs = np.array([r.flottant_max for r in anims if r.flottant_max is not None])
        L += ['- Masques des pistes de flottants (octet : pistes x blocs) : {}'.format(
            ' '.join('{}:{}'.format(k, n) for k, n in masques.most_common(12))),
              '- Valeur absolue maximale des pistes de flottants, par animation : mediane {:.3g}, maximum {:.3g} ; '
              '{} animations ou toutes valent 0'.format(float(np.median(valeurs)), float(valeurs.max()),
                                                       int(np.sum(valeurs == 0)))]
    pistes_f = sum(1 for r in anims if r.pistes_flottantes)
    L += ['', '- Animations a pistes de flottants : {}'.format(pistes_f),
          '- Liaisons : {}'.format(dict(collections.Counter(r.liaison or '(aucune)' for r in anims)))]
    autres = [r for r in resultats if r.statut != 'ok']
    if autres:
        L += ['', '## Exemples par statut', '']
        par = collections.defaultdict(list)
        for r in autres:
            par[(r.statut, r.detail.split(':')[0] if r.detail else r.classe)].append(r)
        for (s, d), rs in sorted(par.items(), key=lambda kv: -len(kv[1])):
            L.append('- **{}** {} ({}) : {}'.format(s, d, len(rs), ' ; '.join(
                '`{}`#{}{}'.format(r.fichier, r.index, ' ({})'.format(r.detail) if r.detail and len(rs) < 4 else '')
                for r in rs[:3])))
    (sortie / 'animations.md').write_text('\n'.join(L) + '\n', encoding='utf-8')
    return L


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('projet', help='projet DECA (dossier contenant db\\core.db)')
    ap.add_argument('--sortie', required=True, help='dossier des resultats')
    ap.add_argument('--echantillon', type=int, default=0, help='nombre de .ban tires au hasard (0 = tous)')
    ap.add_argument('--processus', type=int, default=max(1, (os.cpu_count() or 2) * 3 // 4),
                    help='processus de lecture (defaut : 3/4 des coeurs)')
    ap.add_argument('--lot', type=int, default=50, help='fichiers par lot envoye a un processus')
    args = ap.parse_args(argv)
    t0 = time.time()
    vfs = _projet.open_project(args.projet)
    choix = selectionner(vfs, args.echantillon)
    print('{} fichiers .ban'.format(len(choix)), flush=True)
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
    resultats.sort(key=lambda r: (r.fichier, r.index))
    L = ecrire(Path(args.sortie), resultats, args.projet, time.time() - t0, len(choix))
    texte = '\n'.join(L[:L.index('## Exemples par statut')] if '## Exemples par statut' in L else L)
    enc = sys.stdout.encoding or 'utf-8'
    print((texte + '\n\nResultats : {}'.format(args.sortie)).encode(enc, errors='replace').decode(enc))
    return 0


if __name__ == '__main__':
    multiprocessing.freeze_support()
    sys.exit(main())
