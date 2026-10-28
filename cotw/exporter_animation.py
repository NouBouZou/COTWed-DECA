#!/usr/bin/env python3
"""
exporter_animation.py -- exporte en glTF un squelette, ses modeles et des animations (.ban), pour les voir dans
Blender. Lecture seule du projet, DECA ferme.

Pourquoi (2026-10-28) : deca/ff_havok_anim.py decode les animations a splines de COTW ; audit_animations.py verifie
leur structure sur tout le jeu, mais seul l'oeil dit si un rechargement ou une marche sont justes. L'export reprend le
chemin de DECA (DecaGltf) : modeles a peau, squelette lu par deca.ff_havok, puis une animation glTF par animation
Havok (translation, rotation, echelle locales de chaque os, une cle par image).

Le squelette est donne comme dans les entites (extension remplacee par .bsk, comme l'export). Les animations sont
des chemins exacts ou des motifs SQL LIKE (avec %), limites par --max.

Usage (DECA ferme) :
    python cotw\\exporter_animation.py D:\\dev\\work\\hp --squelette animations/skeletons/hp_characters/elmer.skeleton ^
        --modele models/hp_characters/humans/elmer/hand_and_arm/elmer_hand.modelc ^
        --animations "animations/hp_characters/humans/elmer/first_person/peacemaker_45_01/%" --max 3 ^
        --sortie D:\\dev\\audits\\22-export-animations
    Dans Blender : Fichier > Importer > glTF 2.0, puis la liste des actions dans l'editeur d'actions.
"""
import argparse
import sys
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _projet  # noqa: E402


def chercher_animations(vfs, motifs: List[str], maximum: int) -> List[bytes]:
    """Chemins .ban (noeuds Havok) : chemins exacts, ou motifs LIKE s'ils contiennent %."""
    out: List[bytes] = []
    for m in motifs:
        if '%' in m:
            rows = vfs.db_query_all("SELECT DISTINCT v_path FROM core_nodes WHERE CAST(v_path AS TEXT) LIKE ? "
                                   "AND CAST(v_path AS TEXT) LIKE '%.ban' AND file_type = 'h2014' ORDER BY v_path",
                                   [m], dbg='cotw_exporter_animation')
            out += [p.encode('utf-8') if isinstance(p, str) else bytes(p) for (p,) in rows]
        else:
            out.append(m.encode('utf-8'))
    vus, uniques = set(), []
    for p in out:
        if p not in vus:
            vus.add(p)
            uniques.append(p)
    return uniques[:maximum] if maximum else uniques


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('projet', help='projet DECA (dossier contenant db\\core.db)')
    ap.add_argument('--squelette', required=True, help='chemin du squelette (.skeleton ou .bsk)')
    ap.add_argument('--modele', action='append', default=[], help='modele (.modelc) a peau, repetable')
    ap.add_argument('--animations', action='append', default=[],
                    help='.ban : chemin exact ou motif SQL LIKE avec %%, repetable')
    ap.add_argument('--max', type=int, default=10, help='nombre maximal d\'animations (0 = toutes)')
    ap.add_argument('--sortie', required=True, help='dossier ou ecrire le .gltf et ses fichiers')
    ap.add_argument('--nom', help='nom du .gltf (defaut : celui du squelette)')
    args = ap.parse_args(argv)
    from deca.ff_adf_amf_gltf import DecaGltf
    vfs = _projet.open_project(args.projet)
    squelette = args.squelette.encode('utf-8')
    bans = chercher_animations(vfs, args.animations, args.max)
    nom = args.nom or Path(args.squelette).stem
    g = DecaGltf(vfs, args.sortie, nom, include_skeleton=True, texture_format='png')
    lignes = []
    with g.scene():
        for m in args.modele:
            g.export_modelc(m.encode('utf-8'), None, skeleton_raw_path=squelette)
        g.db.gltf_add_hk_skeleton(g.gltf, squelette, g.d_scene.scene)
        for b in bans:
            try:
                lignes += g.export_animation(b, squelette)
            except Exception as exc:  # noqa: BLE001 - une animation en echec n'arrete pas l'export
                lignes.append('{} : ECHEC {}: {}'.format(_projet.to_text(b), type(exc).__name__, exc))
    g.gltf_save()
    print('{} modele(s), {} fichier(s) .ban'.format(len(args.modele), len(bans)))
    for l in lignes:
        print('  ' + l)
    print('Resultat : {}'.format(g.export_path))
    return 0


if __name__ == '__main__':
    sys.exit(main())
