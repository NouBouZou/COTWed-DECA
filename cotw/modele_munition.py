#!/usr/bin/env python3
"""
modele_munition.py -- modele 3D de la cartouche d'une munition, declare dans son entite (.ee).

Constat sur les 126 entites de munitions et l'entite de la .243 Ranger (2026-10-06, journal de recherche) :
  - l'arme donne le modele par defaut : son .epe a deux composants CModelAttachementWeaponComponent, points
    d'attache "ammo_reload" (cartouche chargee, ex : models/hp_ammunition/ammo_bullet_243_soft.modelc) et
    "ammo_used" (douille ejectee, ..._243_empty.modelc), chacun avec base_model ;
  - le .epe de la munition a les deux memes composants, "fresh_round" (ammo_reload) et "empty_casing"
    (ammo_used), sous son CAmmunitionItem ; beaucoup de munitions du jeu y ajoutent base_model, en derniere
    propriete (proprietes triees par hash, base_model 0xf9dcf6ab est la plus grande) : .223 SP et PT y declarent
    deux modeles differents pour la meme arme. Sans base_model (.243 SP et PT), c'est le modele de l'arme.
  [hypothese forte] le base_model de la munition remplace celui de l'arme pour le meme point d'attache.

Ce module lit ces composants et ecrit base_model dans l'entite (propre) d'une munition ajoutee, comme le font les
.223 du jeu ; le reste de l'entite est recopie a l'identique (verifie). L'arme n'est pas touchee.

Usage, depuis la racine du depot, environnement virtuel active, DECA ferme :
    python cotw\\modele_munition.py D:\\dev\\work\\hp equipment_ammo_223_pt_01 [--base DROPZONE]
"""
import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _projet  # noqa: E402,F401  (substitut de zugbruecke hors Windows)
import sarc as S  # noqa: E402

import deca.ff_rtpc_write as W  # noqa: E402
from deca.ff_rtpc import k_type_str  # noqa: E402

H_CLASS = W.name_hash('_class')            # 0x1473b179
H_NAME = W.name_hash('name')               # 0xd31ab684
H_BASE_MODEL = W.name_hash('base_model')   # 0xf9dcf6ab
H_POINT = 0xdd3acc80                       # point d'attache sur l'arme ("ammo_reload", "ammo_used"), nom inconnu
CLASSE_ITEM = b'CAmmunitionItem'
CLASSE_COMPOSANT = b'CModelAttachementWeaponComponent'
CARTOUCHE = 'fresh_round'                  # point ammo_reload : cartouche chargee
DOUILLE = 'empty_casing'                   # point ammo_used : douille ejectee
COMPOSANTS = (CARTOUCHE, DOUILLE)


class EModele(Exception):
    pass


@dataclass
class Composant:
    nom: str                      # fresh_round, empty_casing, usedAmmo_over...
    point: str                    # point d'attache sur l'arme
    modele: Optional[str]         # base_model, None : celui de l'arme


def _text(node, h) -> Optional[str]:
    p = W.node_get_prop(node, h)
    if p is None or p.type != k_type_str:
        return None
    return bytes(p.data).decode('utf-8', 'replace')


def _composants(tree) -> List:
    """Noeuds CModelAttachementWeaponComponent places sous un CAmmunitionItem."""
    out = []

    def walk(node, in_item):
        cls = W.node_get_prop(node, H_CLASS)
        cls = bytes(cls.data) if cls is not None and cls.type == k_type_str else b''
        if in_item and cls == CLASSE_COMPOSANT:
            out.append(node)
        for c in node.child_table:
            walk(c, in_item or cls == CLASSE_ITEM)

    walk(tree.root_node, False)
    return out


def entree_epe(s) -> int:
    """Index de l'unique .epe (RTPC) de l'entite qui contient un CAmmunitionItem."""
    found = []
    for k, e in enumerate(s.entries):
        if e.is_link or e.data[:4] != W.RTPC_MAGIC:
            continue
        if b'CAmmunitionItem' in e.data:
            found.append(k)
    if len(found) != 1:
        raise EModele('{} entree(s) RTPC avec un CAmmunitionItem dans l\'entite, 1 attendue'.format(len(found)))
    return found[0]


def lire_epe(epe: bytes) -> List[Composant]:
    tree = W.rtpc_from_bytes(epe)
    return [Composant(_text(n, H_NAME) or '0x{:08x}'.format(n.name_hash), _text(n, H_POINT) or '',
                      _text(n, H_BASE_MODEL)) for n in _composants(tree)]


def lire(ee: bytes) -> List[Composant]:
    s = S.parse(ee)
    return lire_epe(s.entries[entree_epe(s)].data)


def modeles_declares(ee: bytes) -> Dict[str, str]:
    """{fresh_round / empty_casing: base_model} des seuls composants qui en declarent un."""
    return {c.nom: c.modele for c in lire(ee) if c.nom in COMPOSANTS and c.modele}


def modifier_epe(epe: bytes, modeles: Dict[str, str]) -> Tuple[bytes, List[str]]:
    """base_model ecrit (ajoute s'il manque, remplace sinon) dans les composants nommes. Chemins avec des '/'."""
    unknown = sorted(set(modeles) - set(COMPOSANTS))
    if unknown:
        raise EModele('composant(s) inconnu(s) : {} ({})'.format(', '.join(unknown), ', '.join(COMPOSANTS)))
    tree = W.rtpc_from_bytes(epe)
    nodes = _composants(tree)
    lines, avant = [], {}
    for nom, chemin in modeles.items():
        chemin = chemin.replace('\\', '/')
        if not chemin.endswith('.modelc'):
            raise EModele('{} : "{}" n\'est pas un modele (.modelc)'.format(nom, chemin))
        hits = [n for n in nodes if _text(n, H_NAME) == nom]
        if len(hits) != 1:
            raise EModele('{} composant(s) "{}" dans le .epe, 1 attendu'.format(len(hits), nom))
        node = hits[0]
        old = _text(node, H_BASE_MODEL)
        avant[nom] = old
        point = _text(node, H_POINT) or '?'
        if old is None:
            W.node_add_prop(node, W.make_prop(H_BASE_MODEL, k_type_str, chemin.encode()))
            lines.append('{} ({}) : base_model ajoute "{}" (avant : modele de l\'arme)'.format(nom, point, chemin))
        else:
            W.node_get_prop(node, H_BASE_MODEL).data = chemin.encode()
            lines.append('{} ({}) : base_model "{}" -> "{}"'.format(nom, point, old, chemin))
    out = W.rtpc_to_binary(tree)
    verifier_epe(epe, out, {k: v.replace('\\', '/') for k, v in modeles.items()}, avant)
    return out, lines


def verifier_epe(old: bytes, new: bytes, modeles: Dict[str, str], avant: Dict[str, Optional[str]]):
    """Relu : chaque composant demande a le modele voulu ; remis dans son etat d'avant, l'arbre produit redonne
    exactement l'original (aucune autre propriete, aucun autre noeud touche) ; base_model reste la propriete de plus
    grand hash du composant, comme dans les entites du jeu."""
    produced = W.rtpc_from_bytes(new)
    for node in _composants(produced):
        nom = _text(node, H_NAME)
        if nom not in modeles:
            continue
        if _text(node, H_BASE_MODEL) != modeles[nom]:
            raise EModele('verification : {} : base_model relu different'.format(nom))
        hashes = [p.name_hash for p in node.prop_table]
        if hashes == sorted(hashes) and hashes[-1] != H_BASE_MODEL:
            raise EModele('verification : {} : base_model n\'est pas a sa place (fin des proprietes)'.format(nom))
        if avant[nom] is None:
            W.node_remove_prop(node, H_BASE_MODEL)
        else:
            W.node_get_prop(node, H_BASE_MODEL).data = avant[nom].encode()
    diff = W.rtpc_diff(W.rtpc_from_bytes(old), produced)
    if diff:
        raise EModele('verification : le .epe differe ailleurs que sur base_model : ' + diff)


def modifier(ee: bytes, modeles: Dict[str, str]) -> Tuple[bytes, List[str]]:
    """Modifie le .epe de l'entite ; les autres entrees sont recopiees a l'identique (verifie)."""
    s = S.parse(ee)
    k = entree_epe(s)
    new_epe, lines = modifier_epe(s.entries[k].data, modeles)
    entries = [S.Entry(e.path, None, e.length) if e.is_link else
               S.Entry(e.path, new_epe if j == k else e.data, len(new_epe) if j == k else len(e.data))
               for j, e in enumerate(s.entries)]
    out, _offsets = S.build(S.Sarc(s.version, entries))
    t = S.parse(out)
    if [(e.path, e.is_link) for e in t.entries] != [(e.path, e.is_link) for e in s.entries]:
        raise EModele('verification : entrees de l\'entite changees')
    for j, (x, y) in enumerate(zip(s.entries, t.entries)):
        if x.is_link:
            if x.length != y.length:
                raise EModele('verification : lien {} modifie'.format(x.path))
        elif y.data != (new_epe if j == k else x.data):
            raise EModele('verification : entree {} modifiee'.format(x.path))
    return out, ['{} : {}'.format(s.entries[k].path, line) for line in lines]


# ---------------------------------------------------------------------------
# Lecture en ligne de commande
# ---------------------------------------------------------------------------

def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(errors='replace')
    ap = argparse.ArgumentParser(description='Affiche les modeles de cartouche declares dans l\'entite d\'une munition.')
    ap.add_argument('projet', help='projet DECA (dossier contenant db\\core.db)')
    ap.add_argument('objet', help='munition, ex : equipment_ammo_223_pt_01')
    ap.add_argument('--base', help='dossier au format dropzone (mod construit) ou lire d\'abord les fichiers')
    args = ap.parse_args(argv)
    import cloner_munition as C
    vfs = _projet.open_project(args.projet)
    base = _projet.base_dir(args.base)
    try:
        entity, ee, src = C.entite_de(vfs, base, args.objet)
        comps = lire(ee)
    except (C.EClone, EModele, S.ESarc) as e:
        raise SystemExit('ERREUR : {}'.format(e))
    print('{} : entite {} ({})'.format(args.objet, entity, src))
    for c in comps:
        print('  {:<16} point {:<16} {}'.format(c.nom, c.point, c.modele or '(modele de l\'arme)'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
