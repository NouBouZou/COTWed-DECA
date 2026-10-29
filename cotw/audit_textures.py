#!/usr/bin/env python3
"""
audit_textures.py -- les textures de COTW (.ddsc, AVTX) : formats, en-tetes, decodage (lecture seule, DECA ferme)

Pourquoi (2026-10-28) : les decodeurs BC1 a BC5 de DECA sont refaits (deca/dxgi_bc.py, defauts D1 a D4). Il reste a
savoir ce que le jeu emploie vraiment :
  - formats DXGI des textures (BC6H n'est pas decode par DECA : image noire ; SNORM etait lu comme du UNORM) ;
  - effet reel du defaut D2 : blocs BC2 / BC3 dont couleur0 <= couleur1, que l'ancien decodeur lisait comme du BC1
    (index 3 en noir, index 2 a mi-chemin) ; pixels qui etaient faux ;
  - BC1 a 3 couleurs (transparence 1 bit) : textures qui s'en servent ;
  - en-tete AVTX : champs non compris (octet 0x06, u16 0x16, u32 0x18 et 0x1c, u32 0x28, octets suivants) ;
  - structure : taille du corps (somme des niveaux presents dans le .ddsc) = champ 0x24, fichier = en-tete + corps ;
  - decodage par DECA (ff_avtx, comme l'onglet Image) : niveaux du .ddsc pour toutes les textures, chaine complete
    (.hmddsc, .atx1...) pour un echantillon (--complet).

2026-10-29, apres 27-textures : les octets non nuls apres 0x2c valent 4k - 1 pour k niveaux hors du .ddsc ; d'ou
l'hypothese d'une table de 8 flux de 12 octets de 0x20 a 0x80 [u32 position, u32 taille, u16 alignement, u8, u8
source] (le flux 0 = le corps du .ddsc : 128, taille du corps, 16, 0, 0). L'audit releve les flux, les tailles des
niveaux hors du .ddsc et celles des fichiers .hmddsc / .atxN du meme nom, et confronte les trois.
28-textures l'a confirmee sur toutes les textures (14 392 a niveaux externes : un flux par niveau, du plus petit au plus
grand, source 1 = le .hmddsc, dont la taille egale la fin du dernier flux) ; deca.ff_avtx la lit (AvtxStream).

Sorties (dossier --sortie) : textures.csv (une texture par ligne), textures.md.

Usage (DECA ferme) :
    python cotw\\audit_textures.py D:\\dev\\work\\hp --sortie D:\\dev\\audits\\27-textures
"""
import argparse
import collections
import csv
import io
import multiprocessing
import os
import random
import struct
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _projet  # noqa: E402

# En-tete AVTX (journal de recherche, icones des munitions) : 0x00 magic, 0x04 version, 0x06 groupe (27-textures : un
# par famille de contenu), 0x07 dimension - 1, 0x08 format DXGI, 0x0c largeur, 0x0e hauteur, 0x10 profondeur (ou
# taille du tableau), 0x12 drapeaux, 0x14 niveaux, 0x15 niveaux dans le fichier, 0x16 u16 inconnu, 0x18 et 0x1c u32
# inconnus, 0x20 table des flux (hypothese : 8 x 12 octets) dont le premier est le corps du .ddsc.
ENTETE = struct.Struct('<4sHBBIHHHHBBHIIII')
FLUX = struct.Struct('<IIHBB')        # position, taille, alignement, inconnu, source
N_FLUX = 8
DRAPEAU_CUBE = 0x40
EXTERNES = ['.hmddsc'] + ['.atx{}'.format(i) for i in range(1, 9)]

BC_COULEUR = {70: (8, 0), 71: (8, 0), 72: (8, 0),                  # format -> (taille du bloc, position du bloc couleur)
              73: (16, 8), 74: (16, 8), 75: (16, 8), 76: (16, 8), 77: (16, 8), 78: (16, 8)}


class Resultat:
    COLONNES = ['fichier', 'statut', 'detail', 'format', 'nom_format', 'largeur', 'hauteur', 'profondeur', 'dimension',
                'drapeaux', 'niveaux', 'niveaux_fichier', 'octet_06', 'u16_16', 'u32_18', 'u32_1c', 'taille_entete',
                'taille_corps', 'corps_attendu', 'taille_fichier', 'u32_28', 'octets_entete_non_nuls',
                'niveaux_decodes', 'blocs_couleur', 'blocs_mode3', 'pixels_faux_ancien_bc23', 'pixels_transparents_bc1',
                'complet_niveaux', 'complet_detail', 'flux0', 'flux', 'niveaux_externes', 'fichiers_externes',
                'flux_controle']

    def __init__(self, fichier: str):
        self.fichier = fichier
        self.statut, self.detail = 'ok', ''
        for k in self.COLONNES[3:]:
            setattr(self, k, '')
        self.niveaux_decodes = self.blocs_couleur = self.blocs_mode3 = 0
        self.pixels_faux_ancien_bc23 = self.pixels_transparents_bc1 = 0

    def ligne(self):
        return [getattr(self, k) for k in self.COLONNES]


def blocs_couleur(raw: bytes, fmt: int) -> Tuple[int, int, int, int]:
    """(blocs, blocs a couleur0 <= couleur1, pixels que l'ancien decodeur lisait faux en BC2 / BC3, pixels
    transparents en BC1) pour un niveau BC1 / BC2 / BC3."""
    taille, pos = BC_COULEUR[fmt]
    n = len(raw) // taille
    if n == 0:
        return 0, 0, 0, 0
    b = np.frombuffer(raw, dtype=np.uint8, count=n * taille).reshape(n, taille)[:, pos:pos + 8].astype(np.uint32)
    c0 = b[:, 0] | (b[:, 1] << 8)
    c1 = b[:, 2] | (b[:, 3] << 8)
    bits = b[:, 4] | (b[:, 5] << 8) | (b[:, 6] << 16) | (b[:, 7] << 24)
    idx = (bits[:, None] >> (2 * np.arange(16, dtype=np.uint32))[None, :]) & 3
    mode3 = c0 <= c1
    if taille == 8:
        return n, int(mode3.sum()), 0, int(((idx == 3) & mode3[:, None]).sum())
    # ancien decodeur : index 3 noir, index 2 = (c0 + c1) / 2 au lieu de (2 c0 + c1) / 3 (identiques si c0 == c1)
    faux = (idx == 3) | ((idx == 2) & (c0 != c1)[:, None])
    return n, int(mode3.sum()), int((faux & mode3[:, None]).sum()), 0


def lire_entete(r: Resultat, data: bytes) -> Optional[tuple]:
    if len(data) < 0x2c or data[:4] != b'AVTX':
        r.statut, r.detail = 'entete', 'magic {!r}, {} octets'.format(data[:4], len(data))
        return None
    (_m, _v, r.octet_06, dim, fmt, r.largeur, r.hauteur, r.profondeur, flags, r.niveaux, r.niveaux_fichier,
     r.u16_16, r.u32_18, r.u32_1c, r.taille_entete, r.taille_corps) = ENTETE.unpack_from(data)
    r.format, r.dimension, r.drapeaux = fmt, dim + 1, '0x{:x}'.format(flags)
    r.u32_28 = struct.unpack_from('<I', data, 0x28)[0]
    r.octets_entete_non_nuls = sum(1 for x in data[0x2c:r.taille_entete] if x)
    flux = lire_flux(data)
    r.flux0 = ':'.join(str(v) for v in flux[0])
    r.flux = ' '.join(':'.join(str(v) for v in e) for e in flux[1:] if any(e))
    r.taille_fichier = len(data)
    from deca.dxgi_types import dxgi_name_db
    r.nom_format = dxgi_name_db.get(fmt, str(fmt))
    return fmt, flags


def lire_flux(data: bytes) -> List[Tuple[int, int, int, int, int]]:
    """Les 8 entrees de 12 octets de 0x20 a 0x80 : (position, taille, alignement, inconnu, source)."""
    if len(data) < 0x20 + FLUX.size * N_FLUX:
        return []
    return [FLUX.unpack_from(data, 0x20 + FLUX.size * i) for i in range(N_FLUX)]


def tailles_niveaux(fmt: int, w: int, h: int, depth: int, niveaux: int, faces: int) -> List[int]:
    """Taille de chaque niveau, du plus grand au plus petit (faces et tranches comprises)."""
    from deca.dxgi_types import raw_data_size
    return [raw_data_size(fmt, max(1, w >> i), max(1, h >> i)) * max(1, depth) * faces for i in range(max(1, niveaux))]


def corps_attendu(fmt: int, w: int, h: int, depth: int, niveaux: int, dans_fichier: int, faces: int) -> int:
    """Somme des tailles des niveaux presents dans le .ddsc (les plus petits : les dans_fichier derniers)."""
    tailles = tailles_niveaux(fmt, w, h, depth, niveaux, faces)
    return sum(tailles[len(tailles) - max(1, dans_fichier):])


def controler_flux(data: bytes, r: 'Resultat', externes: dict) -> None:
    """Confronte la table des flux aux niveaux hors du .ddsc et aux fichiers .hmddsc / .atxN du meme nom.
    externes : extension -> taille du fichier (vide pour un fichier sans nom). Ecrit r.niveaux_externes,
    r.fichiers_externes et r.flux_controle (mots separes par des espaces : somme=ok|faux, puis par flux
    source>extension quand la taille du fichier egale la fin du dernier flux de cette source)."""
    flux = lire_flux(data)
    faces = 6 if int(r.drapeaux, 16) & DRAPEAU_CUBE else 1
    niveaux = r.niveaux or 1
    k = niveaux - (r.niveaux_fichier if r.niveaux else 1)
    ext = tailles_niveaux(r.format, r.largeur, r.hauteur, r.profondeur, niveaux, faces)[:max(0, k)]
    r.niveaux_externes = ' '.join(str(v) for v in ext)
    r.fichiers_externes = ' '.join('{}={}'.format(e.lstrip('.'), n) for e, n in externes.items())
    utiles = [e for e in flux[1:] if e[1]]
    mots = ['somme=' + ('ok' if sum(e[1] for e in utiles) == sum(ext) else 'faux')]
    if utiles and sorted(e[1] for e in utiles) == sorted(ext):
        mots.append('niveaux=ok')
    fins = collections.defaultdict(int)
    for pos, taille, _al, _x, src in utiles:
        fins[src] = max(fins[src], pos + taille)
    for src, fin in sorted(fins.items()):
        egaux = [e.lstrip('.') for e, n in externes.items() if n == fin]
        mots.append('{}>{}'.format(src, '/'.join(egaux) if egaux else '?'))
    r.flux_controle = ' '.join(mots)


def auditer_ddsc(data: bytes, chemin: str) -> Resultat:
    """En-tete, structure et decodage des niveaux du .ddsc (ceux que DECA affiche sans .hmddsc ni .atx)."""
    from deca.ff_avtx import Ddsc
    from deca.dxgi_types import dxgi_base_format_db
    r = Resultat(chemin)
    lu = lire_entete(r, data)
    if lu is None:
        return r
    fmt, flags = lu
    if fmt not in dxgi_base_format_db:
        r.statut, r.detail = 'format_inconnu', str(fmt)
        return r
    faces = 6 if flags & DRAPEAU_CUBE else 1
    niveaux = r.niveaux or 1
    r.corps_attendu = corps_attendu(fmt, r.largeur, r.hauteur, r.profondeur, niveaux, r.niveaux_fichier if r.niveaux else 1,
                                    faces)
    if r.taille_entete + r.taille_corps != len(data) or r.corps_attendu != r.taille_corps:
        r.statut = 'taille_incoherente'
    try:
        dd = Ddsc()
        dd.load_ddsc(io.BytesIO(data), save_raw_data=True)
    except Exception as exc:  # noqa: BLE001 - une texture en echec est comptee, pas fatale
        r.statut, r.detail = 'echec', '{}: {}'.format(type(exc).__name__, str(exc)[:160])
        return r
    for mip in dd.mips:
        if mip.data is None or mip.raw_data is None:
            continue
        r.niveaux_decodes += 1
        if fmt in BC_COULEUR:
            n, m3, faux, transp = blocs_couleur(mip.raw_data, fmt)
            r.blocs_couleur += n
            r.blocs_mode3 += m3
            r.pixels_faux_ancien_bc23 += faux
            r.pixels_transparents_bc1 += transp
    if dxgi_base_format_db[fmt] == 94 and r.statut == 'ok':
        r.statut = 'bc6h_non_decode'                    # deca.dxgi_94_95_96 ecrit une image nulle
    return r


def auditer_complet(vfs, uid: int, r: Resultat):
    """Chaine complete (.ddsc + .hmddsc + .atxN) par deca.ff_avtx.image_load, comme l'onglet Image."""
    from deca import ff_avtx
    try:
        dd = ff_avtx.image_load(vfs, vfs.node_where_uid(uid), save_raw_data=False)
        lus = sum(1 for m in dd.mips if m.data is not None)
        r.complet_niveaux = '{}/{}'.format(lus, len(dd.mips))
        if lus < len(dd.mips):
            manquant = next(i for i, m in enumerate(dd.mips) if m.data is not None)
            r.complet_detail = 'niveaux 0 a {} absents'.format(manquant - 1)
    except Exception as exc:  # noqa: BLE001
        r.complet_niveaux = 'echec'
        r.complet_detail = '{}: {}'.format(type(exc).__name__, str(exc)[:160])


_VFS = None
_EXTERNES = {}


def tailles_externes(vfs) -> dict:
    """chemin (texte) -> taille, pour les .hmddsc et .atxN du projet (la plus grande si plusieurs copies)."""
    rows = vfs.db_query_all("SELECT v_path, MAX(size_u) FROM core_nodes WHERE CAST(v_path AS TEXT) LIKE '%.hmddsc' "
                            "OR CAST(v_path AS TEXT) LIKE '%.atx_' GROUP BY v_path", [], dbg='cotw_audit_textures')
    return {_projet.to_text(p): n for p, n in rows if p}


def externes_de(chemin: str, tailles: dict) -> dict:
    if not chemin.endswith('.ddsc'):
        return {}
    base = chemin[:-len('.ddsc')]
    return {e: tailles[base + e] for e in EXTERNES if base + e in tailles}


def _init_travail(projet: str):
    global _VFS, _EXTERNES
    _VFS = _projet.open_project(projet)
    _EXTERNES = tailles_externes(_VFS)


def _lot(lot: List[Tuple[int, str, bool]]) -> List[Resultat]:
    out = []
    for uid, chemin, complet, nomme in lot:
        try:
            node = _VFS.node_where_uid(uid)
            data = _projet.read_node(_VFS, node)
        except Exception as exc:  # noqa: BLE001
            r = Resultat(chemin)
            r.statut, r.detail = 'lecture', '{}: {}'.format(type(exc).__name__, str(exc)[:160])
            out.append(r)
            continue
        r = auditer_ddsc(data, chemin)
        if r.format != '' and r.niveaux != '':
            controler_flux(data, r, externes_de(chemin, _EXTERNES) if nomme else {})
        if complet:
            auditer_complet(_VFS, uid, r)
        out.append(r)
    return out


def selectionner(vfs, complet: int, graine: int = 1) -> List[Tuple[int, str, bool, bool]]:
    """Un noeud AVTX par chemin (ou par hash pour les fichiers sans nom) ; chaine complete pour `complet` textures
    nommees tirees au hasard (0 : aucune, -1 : toutes)."""
    rows = vfs.db_query_all("SELECT MIN(node_id), v_path, v_hash FROM core_nodes WHERE file_type = 'avtx' "
                            "GROUP BY COALESCE(CAST(v_path AS TEXT), v_hash)", [], dbg='cotw_audit_textures')
    out = []
    for uid, p, h in rows:
        nom = _projet.to_text(p) if p else ''
        out.append((uid, nom or '(sans nom) {:08x}'.format((h or 0) & 0xFFFFFFFF), bool(nom)))
    out.sort(key=lambda t: t[1])
    nommes = [i for i, t in enumerate(out) if t[2]]
    if complet < 0:
        choix = set(nommes)
    else:
        choix = set(random.Random(graine).sample(nommes, min(complet, len(nommes))))
    return [(uid, nom, i in choix, n) for i, (uid, nom, n) in enumerate(out)]


def _cumul(rs: List[Resultat], k: str) -> int:
    return sum(int(getattr(r, k) or 0) for r in rs)


def table_flux(lus: List[Resultat]) -> List[str]:
    """Section du rapport : la table des flux (0x20, 8 x 12 octets) confrontee aux niveaux et aux fichiers externes."""
    L = ['', '## Table des flux (0x20 : 8 x 12 octets)', '']
    f0 = sum(1 for r in lus if r.flux0 == '{}:{}:16:0:0'.format(r.taille_entete, r.taille_corps))
    L.append('- Flux 0 = (taille de l\'en-tete, taille du corps, 16, 0, 0) : {} / {}'.format(f0, len(lus)))
    ext = [r for r in lus if r.niveaux_externes]
    if not ext:
        return L
    somme = sum(1 for r in ext if 'somme=ok' in r.flux_controle)
    niv = sum(1 for r in ext if 'niveaux=ok' in r.flux_controle)
    L.append('- Textures a niveaux hors du .ddsc : {} ; somme des flux = somme de ces niveaux : {} ; un flux par niveau, '
             'memes tailles : {}'.format(len(ext), somme, niv))
    sans = sum(1 for r in lus if not r.niveaux_externes and r.flux)
    L.append('- Textures sans niveau externe mais avec des flux : {}'.format(sans))
    champs = collections.Counter()
    for r in ext:
        for e in r.flux.split():
            _pos, _taille, al, x, src = e.split(':')
            champs[('alignement', al)] += 1
            champs[('octet 10', x)] += 1
            champs[('source', src)] += 1
    for nom in ('alignement', 'octet 10', 'source'):
        L.append('- {} des flux : {}'.format(nom, ', '.join('`{}` x {}'.format(v, n) for (k, v), n in champs.most_common()
                                                            if k == nom)))
    nommes = [r for r in ext if r.fichiers_externes]
    liens = collections.Counter(w for r in nommes for w in r.flux_controle.split() if '>' in w)
    L.append('- Source -> fichier dont la taille egale la fin du dernier flux de cette source ({} textures nommees a '
             'fichiers externes) : {}'.format(len(nommes), ', '.join('`{}` x {}'.format(w, n) for w, n in liens.most_common(12))))
    formes = collections.Counter((len(r.niveaux_externes.split()), ' '.join(sorted(x.split('=')[0] for x in r.fichiers_externes.split())))
                                 for r in nommes)
    L.append('- Niveaux externes -> fichiers presents : {}'.format(', '.join('{} : {} x {}'.format(k, f or '(aucun)', n)
                                                                         for (k, f), n in formes.most_common(10))))
    faux = [r for r in ext if 'somme=ok' not in r.flux_controle][:5]
    L += ['  - somme fausse : `{}` flux `{}`, niveaux `{}`, fichiers `{}`'.format(r.fichier, r.flux, r.niveaux_externes,
                                                                                 r.fichiers_externes) for r in faux]
    exemples = [r for r in nommes if len(r.niveaux_externes.split()) >= 3][:3]
    L += ['  - exemple : `{}` flux `{}`, niveaux `{}`, fichiers `{}`'.format(r.fichier, r.flux, r.niveaux_externes,
                                                                            r.fichiers_externes) for r in exemples]
    return L


def ecrire(sortie: Path, resultats: List[Resultat], projet: str, duree: float) -> List[str]:
    sortie.mkdir(parents=True, exist_ok=True)
    with open(sortie / 'textures.csv', 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f, delimiter=';')
        w.writerow(Resultat.COLONNES)
        for r in resultats:
            w.writerow(r.ligne())
    lus = [r for r in resultats if r.format != '']
    statuts = collections.Counter(r.statut for r in resultats)
    L = ['# Audit des textures (.ddsc, AVTX)', '',
         '- Projet : `{}` ; {} textures ; duree : {:.0f} s'.format(projet, len(resultats), duree),
         '- **Decodees sans defaut : {} / {}**'.format(statuts.get('ok', 0), len(resultats)), '',
         '| Statut | Textures |', '|---|---:|']
    L += ['| {} | {} |'.format(s, n) for s, n in statuts.most_common()]

    L += ['', '## Formats DXGI', '', '| Format | Textures | Cubes | Volumes | Plus grande | Niveaux decodes (.ddsc) |',
          '|---|---:|---:|---:|---|---:|']
    par_fmt = collections.defaultdict(list)
    for r in lus:
        par_fmt[(r.format, r.nom_format)].append(r)
    for (fmt, nom), rs in sorted(par_fmt.items(), key=lambda kv: -len(kv[1])):
        cubes = sum(1 for r in rs if int(r.drapeaux, 16) & DRAPEAU_CUBE)
        volumes = sum(1 for r in rs if (r.profondeur or 1) > 1)
        grand = max(rs, key=lambda r: r.largeur * r.hauteur)
        L.append('| {} ({}) | {} | {} | {} | {} x {} | {} |'.format(nom, fmt, len(rs), cubes, volumes, grand.largeur,
                                                                     grand.hauteur, _cumul(rs, 'niveaux_decodes')))

    bc23 = [r for r in lus if r.format in BC_COULEUR and BC_COULEUR[r.format][0] == 16]
    bc1 = [r for r in lus if r.format in BC_COULEUR and BC_COULEUR[r.format][0] == 8]
    L += ['', '## Defaut D2 (BC2 / BC3 lus comme du BC1 par l\'ancien decodeur), sur les niveaux du .ddsc', '']
    if bc23:
        touchees = [r for r in bc23 if r.pixels_faux_ancien_bc23]
        nb, m3, px = _cumul(bc23, 'blocs_couleur'), _cumul(bc23, 'blocs_mode3'), _cumul(bc23, 'pixels_faux_ancien_bc23')
        L += ['- Textures BC2 / BC3 : {} ; blocs : {} ; blocs a couleur0 <= couleur1 : {} ({:.2f} %)'.format(
                  len(bc23), nb, m3, 100.0 * m3 / max(1, nb)),
              '- **Pixels que l\'ancien decodeur lisait faux : {} ({:.3f} % des pixels), dans {} textures ({:.1f} %)**'.format(
                  px, 100.0 * px / max(1, 16 * nb), len(touchees), 100.0 * len(touchees) / len(bc23))]
        pires = sorted(touchees, key=lambda r: -r.pixels_faux_ancien_bc23 / max(1, 16 * r.blocs_couleur))[:8]
        L += ['  - `{}` : {:.1f} % des pixels'.format(r.fichier, 100.0 * r.pixels_faux_ancien_bc23 / (16 * r.blocs_couleur))
              for r in pires]
    if bc1:
        tr = [r for r in bc1 if r.pixels_transparents_bc1]
        L += ['- BC1 : {} textures ; {} utilisent la transparence 1 bit (blocs a 3 couleurs, index 3)'.format(
            len(bc1), len(tr))]

    L += ['', '## Champs d\'en-tete', '']
    for k, titre in (('octet_06', 'octet 0x06'), ('dimension', 'dimension (0x07 + 1)'), ('drapeaux', 'drapeaux (0x12)'),
                     ('u16_16', 'u16 0x16'), ('u32_18', 'u32 0x18'), ('u32_1c', 'u32 0x1c'), ('taille_entete', 'taille de l\'en-tete'),
                     ('u32_28', 'u32 0x28'), ('octets_entete_non_nuls', 'octets non nuls apres 0x2c')):
        c = collections.Counter(getattr(r, k) for r in lus)
        L.append('- {} : {}'.format(titre, ', '.join('`{}` x {}'.format(v, n) for v, n in c.most_common(10))
                                     + (' ({} valeurs)'.format(len(c)) if len(c) > 10 else '')))
    for k, titre in (('octet_06', 'octet 0x06'), ('u32_18', 'u32 0x18')):
        par = collections.defaultdict(collections.Counter)
        for r in lus:
            par[getattr(r, k)][r.fichier.split('/')[0] + '/' + r.fichier.split('/')[1] if r.fichier.count('/') > 1
                               else '(sans nom ou racine)'] += 1
        L.append('- {} par dossier : {}'.format(titre, ' ; '.join('`{}` : {}'.format(v, ', '.join(
            '{} {}'.format(d, n) for d, n in par[v].most_common(3))) for v, _n in collections.Counter(
                getattr(r, k) for r in lus).most_common(12))))
    drap_fmt = collections.Counter((r.drapeaux, r.nom_format) for r in lus)
    L += ['- drapeaux par format : ' + ', '.join('{} {} x {}'.format(d, f, n) for (d, f), n in drap_fmt.most_common(12))]

    L += table_flux(lus)

    complets = [r for r in resultats if r.complet_niveaux]
    if complets:
        echecs = [r for r in complets if r.complet_niveaux == 'echec']
        partiels = [r for r in complets if r.complet_niveaux != 'echec' and r.complet_detail]
        L += ['', '## Chaine complete (.ddsc + .hmddsc + .atx), echantillon', '',
              '- Textures : {} ; tous niveaux decodes : {} ; niveaux manquants : {} ; echecs : {}'.format(
                  len(complets), len(complets) - len(echecs) - len(partiels), len(partiels), len(echecs))]
        for r in (echecs + partiels)[:10]:
            L.append('  - `{}` : {} {}'.format(r.fichier, r.complet_niveaux, r.complet_detail))

    autres = [r for r in resultats if r.statut != 'ok']
    if autres:
        L += ['', '## Exemples par statut', '']
        par = collections.defaultdict(list)
        for r in autres:
            par[(r.statut, r.detail.split(':')[0] if r.detail else r.nom_format)].append(r)
        for (s, d), rs in sorted(par.items(), key=lambda kv: -len(kv[1])):
            L.append('- **{}** {} ({}) : {}'.format(s, d, len(rs), ' ; '.join(
                '`{}`{}'.format(r.fichier, ' ({})'.format(r.detail) if r.detail and len(rs) < 4 else '') for r in rs[:3])))
    (sortie / 'textures.md').write_text('\n'.join(L) + '\n', encoding='utf-8')
    return L


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('projet', help='projet DECA (dossier contenant db\\core.db)')
    ap.add_argument('--sortie', required=True, help='dossier des resultats')
    ap.add_argument('--complet', type=int, default=300,
                    help='textures nommees tirees au hasard, decodees avec .hmddsc et .atx (defaut 300 ; -1 = toutes)')
    ap.add_argument('--processus', type=int, default=max(1, (os.cpu_count() or 2) * 3 // 4),
                    help='processus de lecture (defaut : 3/4 des coeurs)')
    ap.add_argument('--lot', type=int, default=100, help='textures par lot envoye a un processus')
    args = ap.parse_args(argv)
    t0 = time.time()
    vfs = _projet.open_project(args.projet)
    choix = selectionner(vfs, args.complet)
    print('{} textures, dont {} en chaine complete'.format(len(choix), sum(1 for c in choix if c[2])), flush=True)
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
