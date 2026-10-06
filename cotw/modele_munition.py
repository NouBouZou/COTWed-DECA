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

Constat en jeu (test17, 2026-10-06) : base_model ajoute a l'entite d'une copie de la .243 SP, ignore par la Ranger.
Releve des 153 entites d'armes : les armes dont les munitions declarent un modele (.223 a verrou, .270, 12GA a
pompe...) n'ont AUCUN composant aux points ammo_reload / ammo_used ; la Ranger en a deux (model.bullet_new,
model.bullet_used), sans evenement. Regle : un composant de l'arme pour un point l'emporte ; le base_model de la
munition ne sert que si l'arme n'a pas de composant a ce point. Certaines armes (12GA semi-auto "type2", 16GA 1897,
10GA...) ont des variantes par type de munition, montrees et cachees par des evenements (show_event / hide_event)
qu'aucune entite de munition n'emet : non prises en charge.

Ce module :
  - lit ces composants, cote munition et cote arme ;
  - ecrit base_model dans l'entite (propre) d'une munition, comme le font les .223 du jeu ;
  - "bascule" une arme dans la forme des .223 : retire ses composants d'un point (sans evenement) ; l'appelant
    reporte leur modele dans les munitions compatibles qui n'en declarent pas (cloner_munition --basculer-armes).
Le reste des entites est recopie a l'identique (verifie).

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
CLASSE_ARME = b'CBulletWeaponBase'         # classe de l'objet arme dans les 153 entites d'armes relevees
H_SHOW = W.name_hash('show_event')         # 0x0aa0fded
H_HIDE = W.name_hash('hide_event')         # 0xf82b8a98
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


@dataclass
class ComposantArme:
    nom: str                      # model.bullet_new...
    point: str
    modele: Optional[str]
    evenements: bool              # show_event ou hide_event non vide : variante commandee par evenements


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


def _events(node) -> bool:
    for h in (H_SHOW, H_HIDE):
        p = W.node_get_prop(node, h)
        if p is not None and len(p.data):
            return True
    return False


def _composants_arme(tree) -> List[Tuple[object, object]]:
    """(parent, noeud) des CModelAttachementWeaponComponent qui ont un point d'attache, dans l'objet arme."""
    out = []

    def walk(node, parent, in_weapon):
        cls = W.node_get_prop(node, H_CLASS)
        cls = bytes(cls.data) if cls is not None and cls.type == k_type_str else b''
        if in_weapon and cls == CLASSE_COMPOSANT and _text(node, H_POINT):
            out.append((parent, node))
        for c in node.child_table:
            walk(c, node, in_weapon or cls == CLASSE_ARME)

    walk(tree.root_node, None, False)
    return out


def entree_arme(s) -> int:
    """Index de l'unique .epe (RTPC) de l'entite d'une arme (objet CBulletWeaponBase)."""
    found = [k for k, e in enumerate(s.entries)
             if not e.is_link and e.data[:4] == W.RTPC_MAGIC and CLASSE_ARME in e.data]
    if len(found) != 1:
        raise EModele('{} entree(s) RTPC avec un CBulletWeaponBase dans l\'entite, 1 attendue'.format(len(found)))
    return found[0]


def lire_arme(ee: bytes) -> List[ComposantArme]:
    s = S.parse(ee)
    tree = W.rtpc_from_bytes(s.entries[entree_arme(s)].data)
    return [ComposantArme(_text(n, H_NAME) or '0x{:08x}'.format(n.name_hash), _text(n, H_POINT),
                          _text(n, H_BASE_MODEL), _events(n)) for _p, n in _composants_arme(tree)]


def retirer_points(ee: bytes, points) -> Tuple[bytes, List[str], Dict[str, str]]:
    """Retire de l'entite d'une arme ses composants aux points donnes (un seul par point, sans evenement), comme
    les armes du jeu dont les munitions declarent leur modele. Renvoie (entite, rapport, {point: ancien modele})."""
    s = S.parse(ee)
    k = entree_arme(s)
    epe = s.entries[k].data
    tree = W.rtpc_from_bytes(epe)
    pairs = _composants_arme(tree)
    removed, lines = {}, []
    for point in points:
        hits = [(p, n) for p, n in pairs if _text(n, H_POINT) == point]
        if not hits:
            raise EModele('aucun composant au point "{}" dans l\'arme'.format(point))
        if any(_events(n) for _p, n in hits):
            raise EModele('point "{}" : variantes commandees par evenements (show_event / hide_event), non prises en '
                          'charge'.format(point))
        if len(hits) != 1:
            raise EModele('point "{}" : {} composants dans l\'arme, 1 attendu'.format(point, len(hits)))
        parent, node = hits[0]
        model = _text(node, H_BASE_MODEL)
        if not model:
            raise EModele('point "{}" : composant sans base_model'.format(point))
        parent.child_table = [c for c in parent.child_table if c is not node]
        removed[point] = model
        lines.append('{} : composant {} (point {}, "{}") retire'.format(s.entries[k].path, _text(node, H_NAME), point,
                                                                       model))
    new_epe = W.rtpc_to_binary(tree)
    # verification : relu, l'arbre produit est l'original moins ces composants, et rien d'autre
    expected = W.rtpc_from_bytes(epe)
    for parent, node in [(p, n) for p, n in _composants_arme(expected) if _text(n, H_POINT) in removed]:
        parent.child_table = [c for c in parent.child_table if c is not node]
    produced = W.rtpc_from_bytes(new_epe)
    diff = W.rtpc_diff(expected, produced)
    if diff:
        raise EModele('verification : arme : ' + diff)
    if any(_text(n, H_POINT) in removed for _p, n in _composants_arme(produced)):
        raise EModele('verification : arme : composant encore present')
    out = _rebuild(s, k, new_epe)
    return out, lines, removed


def _rebuild(s, k: int, payload: bytes) -> bytes:
    """SARC ``s`` avec l'entree ``k`` remplacee ; les autres recopiees a l'identique (verifie)."""
    entries = [S.Entry(e.path, None, e.length) if e.is_link else
               S.Entry(e.path, payload if j == k else e.data, len(payload) if j == k else len(e.data))
               for j, e in enumerate(s.entries)]
    out, _offsets = S.build(S.Sarc(s.version, entries))
    t = S.parse(out)
    if [(e.path, e.is_link) for e in t.entries] != [(e.path, e.is_link) for e in s.entries]:
        raise EModele('verification : entrees de l\'entite changees')
    for j, (x, y) in enumerate(zip(s.entries, t.entries)):
        if x.is_link:
            if x.length != y.length:
                raise EModele('verification : lien {} modifie'.format(x.path))
        elif y.data != (payload if j == k else x.data):
            raise EModele('verification : entree {} modifiee'.format(x.path))
    return out


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
    return _rebuild(s, k, new_epe), ['{} : {}'.format(s.entries[k].path, line) for line in lines]


def composant_du_point(ee: bytes, point: str) -> Optional[Composant]:
    """Composant fresh_round / empty_casing de la munition attache a ce point (None s'il n'y en a pas)."""
    hits = [c for c in lire(ee) if c.nom in COMPOSANTS and c.point == point]
    return hits[0] if len(hits) == 1 else None


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
