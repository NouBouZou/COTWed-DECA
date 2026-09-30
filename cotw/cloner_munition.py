#!/usr/bin/env python3
"""
cloner_munition.py -- ajoute au jeu une munition copiee d'une munition existante, sans rien ecraser.

Deux fichiers RTPC sont produits, au format du dossier dropzone du jeu :
  settings/hp_settings/equipment_data.bin   declaration de l'objet (nom, prix, compatibilite...)
  settings/spawn_ammunition_defs.bin        lien entre le nom de l'objet et son entite (.ee)

Ce que fait l'outil (structure observee sur COTW le 2026-09-30, voir le journal de recherche) :
  1. il trouve le noeud de la munition source : hash du noeud = hash32(nom), propriete de texte = nom ;
  2. il le copie, donne a la copie le hash32 du nouveau nom, remplace le nom dans les proprietes
     d'identite (name dans equipment_data ; nom et premiere etiquette dans spawn_ammunition_defs),
     et remplace tout u32 egal a hash32(ancien nom) par hash32(nouveau nom) ;
  3. il insere la copie parmi ses soeurs a sa place dans l'ordre croissant des hashes (100 % des
     noeuds du jeu ont leurs enfants tries) ;
  4. options : libelle, prix, liste des armes compatibles, chemin d'entite (.ee) ;
  5. il relit les fichiers produits et verifie que, la copie retiree, ils sont identiques aux originaux.

Tout le reste de la munition source est garde tel quel : cles de traduction du nom et de la
description, icone, et par defaut la meme entite (.ee). La nouvelle munition partage donc les modeles,
les sons et la balistique de la source. C'est volontaire pour le premier essai : seuls deux fichiers
changent, et on teste uniquement l'ajout d'un objet.

Usage, depuis la racine du depot, environnement virtuel active, DECA ferme :
    python cotw\\cloner_munition.py D:\\dev\\work\\hp --source equipment_ammo_243_sp_01 ^
        --nom equipment_ammo_243_test_01 --libelle ".243 Test" --prix 10 ^
        --sortie D:\\dev\\mods\\munition_test

Puis copier le CONTENU de D:\\dev\\mods\\munition_test\\dropzone dans le dossier dropzone du jeu.
Pour annuler : supprimer ces deux fichiers du dropzone.

--base DOSSIER : partir des fichiers presents dans DOSSIER (par exemple ton dropzone actuel, s'il
contient deja un equipment_data.bin modifie par un autre mod) plutot que des originaux du jeu.
"""
import argparse
import sys
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _projet  # noqa: E402  (installe aussi le substitut de zugbruecke hors Windows)

import deca.ff_rtpc_write as W  # noqa: E402
from deca.ff_rtpc import Rtpc, RtpcNode, k_type_str, k_type_u32  # noqa: E402

EQUIPMENT_DATA = 'settings/hp_settings/equipment_data.bin'
SPAWN_AMMO = 'settings/spawn_ammunition_defs.bin'

H_NAME = W.name_hash('name')                    # 0xd31ab684
H_NOTE = W.name_hash('note')                    # 0x2314c9ea, libelle anglais
H_PRICE = 0x33df3b2f                            # prix (d'apres cotw-mod-builder)
H_COMPAT = 0x77C2B3BA                           # liste des armes compatibles
H_EQUIPMENT_NAME = W.name_hash('equipment_name')  # 0x36642277, nom d'une arme dans la liste
H_WEAPONS = W.name_hash('weapons')              # 0xc04b7961, categorie des armes
H_SPAWN_NAME = 0xf71c2a21                       # nom de l'objet dans spawn_ammunition_defs
H_TAGS = W.name_hash('tags')                    # 0x54697e8f
H_ENTITY = W.name_hash('entity')                # 0x399ed0ed


class EClone(Exception):
    """Operation impossible : le message explique pourquoi."""


# ---------------------------------------------------------------------------
# Outils sur l'arbre
# ---------------------------------------------------------------------------

def find_item(root: RtpcNode, name: str, name_prop: int) -> Tuple[RtpcNode, RtpcNode]:
    """
    Renvoie (parent, noeud) de l'objet ``name`` : hash du noeud = hash32(name) ET propriete ``name_prop``
    egale a ``name``. La double condition evite de confondre avec un autre noeud de meme hash.
    """
    h = W.name_hash(name)
    target = name.encode('utf-8')
    found = []

    def walk(node):
        for child in node.child_table:
            if child.name_hash == h:
                p = W.node_get_prop(child, name_prop)
                if p is not None and p.type == k_type_str and bytes(p.data) == target:
                    found.append((node, child))
            walk(child)

    walk(root)
    if not found:
        raise EClone('objet "{}" introuvable'.format(name))
    if len(found) > 1:
        raise EClone('objet "{}" trouve {} fois : cas non prevu'.format(name, len(found)))
    return found[0]


def set_str(node: RtpcNode, prop_hash: int, value: str, what: str):
    p = W.node_get_prop(node, prop_hash)
    if p is None or p.type != k_type_str:
        raise EClone('{} : propriete 0x{:08x} absente ou pas de type texte'.format(what, prop_hash))
    old = bytes(p.data).decode('utf-8', 'replace')
    p.data = value.encode('utf-8')
    W.prop_value_bytes(p)  # valide (pas d'octet nul)
    return old


def set_u32(node: RtpcNode, prop_hash: int, value: int, what: str):
    p = W.node_get_prop(node, prop_hash)
    if p is None or p.type != k_type_u32:
        raise EClone('{} : propriete 0x{:08x} absente ou pas de type u32'.format(what, prop_hash))
    old = p.data
    p.data = value
    W.prop_value_bytes(p)
    return old


def replace_hash_values(node: RtpcNode, old_h: int, new_h: int) -> int:
    """Remplace tout u32 egal a old_h par new_h dans le noeud et ses descendants ; renvoie le nombre."""
    n = 0
    for p in node.prop_table:
        if p.type == k_type_u32 and p.data == old_h:
            p.data = new_h
            n += 1
    for c in node.child_table:
        n += replace_hash_values(c, old_h, new_h)
    return n


def insert_sorted(parent: RtpcNode, node: RtpcNode) -> int:
    if any(c.name_hash == node.name_hash for c in parent.child_table):
        raise EClone('un objet de hash 0x{:08x} existe deja a cet endroit (nom deja utilise ?)'.format(node.name_hash))
    return W.node_add_child(parent, node)


def remove_child(parent: RtpcNode, node: RtpcNode):
    parent.child_table = [c for c in parent.child_table if c is not node]
    parent.child_map = {c.name_hash: c for c in parent.child_table}
    parent.child_count = len(parent.child_table)


# ---------------------------------------------------------------------------
# Compatibilite
# ---------------------------------------------------------------------------

def compat_entries(item: RtpcNode) -> Tuple[RtpcNode, List[RtpcNode]]:
    compat = W.node_get_child(item, H_COMPAT)
    if compat is None:
        raise EClone('la munition source n\'a pas de liste de compatibilite (0x{:08x})'.format(H_COMPAT))
    entries = [c for c in compat.child_table if W.node_get_prop(c, H_EQUIPMENT_NAME) is not None]
    if not entries:
        raise EClone('liste de compatibilite de la source vide ou de structure inconnue')
    return compat, entries


def key_pool(root: RtpcNode) -> List[int]:
    """Cles deja utilisees par des entrees de listes de compatibilite dans le fichier (cles connues valides)."""
    keys = set()

    def walk(node):
        if node.name_hash == H_COMPAT:
            for c in node.child_table:
                if W.node_get_prop(c, H_EQUIPMENT_NAME) is not None:
                    keys.add(c.name_hash)
        for c in node.child_table:
            walk(c)

    walk(root)
    return sorted(keys)


def known_weapons(root: RtpcNode) -> set:
    weapons = W.node_get_child(root, H_WEAPONS)
    if weapons is None:
        return set()
    return {c.name_hash for c in weapons.child_table}


def set_compatible_weapons(item: RtpcNode, weapons: Sequence[str], pool: Sequence[int]) -> List[str]:
    """
    Remplace la liste de compatibilite. On ne connait pas la regle qui produit les cles des entrees :
    on reutilise d'abord celles de la source, puis des cles deja presentes ailleurs dans le fichier.
    Chaque entree est une copie de la premiere entree de la source (memes champs, meme ordre).
    """
    compat, entries = compat_entries(item)
    template = entries[0]
    keys = sorted(c.name_hash for c in entries)[:len(weapons)]
    extra = [k for k in pool if k not in keys]
    while len(keys) < len(weapons):
        if not extra:
            raise EClone('pas assez de cles connues pour {} armes'.format(len(weapons)))
        keys.append(extra.pop(0))
    new_entries = []
    for key, weapon in zip(sorted(keys), weapons):
        e = W.clone_node(template)
        e.name_hash = key
        set_str(e, H_EQUIPMENT_NAME, weapon, 'entree de compatibilite')
        new_entries.append(e)
    others = [c for c in compat.child_table if W.node_get_prop(c, H_EQUIPMENT_NAME) is None]
    compat.child_table = sorted(others + new_entries, key=lambda c: c.name_hash)
    compat.child_map = {c.name_hash: c for c in compat.child_table}
    compat.child_count = len(compat.child_table)
    return ['0x{:08x} -> {}'.format(e.name_hash, w) for e, w in zip(new_entries, weapons)]


# ---------------------------------------------------------------------------
# Operations sur les deux fichiers
# ---------------------------------------------------------------------------

def clone_equipment(tree: Rtpc, source: str, new: str, label: Optional[str] = None,
                    price: Optional[int] = None, weapons: Optional[Sequence[str]] = None,
                    allow_unknown_weapons: bool = False) -> Tuple[RtpcNode, RtpcNode, List[str]]:
    parent, src = find_item(tree.root_node, source, H_NAME)
    report = ['equipment_data : source trouvee, {} proprietes, {} enfants'.format(
        len(src.prop_table), len(src.child_table))]
    item = W.clone_node(src)
    item.name_hash = W.name_hash(new)
    set_str(item, H_NAME, new, 'name')
    n = replace_hash_values(item, W.name_hash(source), W.name_hash(new))
    report.append('  hash du noeud : 0x{:08x} -> 0x{:08x} ; u32 = hash de l\'ancien nom remplaces : {}'.format(
        src.name_hash, item.name_hash, n))
    if label is not None:
        old = set_str(item, H_NOTE, label, 'note')
        report.append('  libelle (note) : "{}" -> "{}"'.format(old, label))
    if price is not None:
        old = set_u32(item, H_PRICE, price, 'prix')
        report.append('  prix : {} -> {}'.format(old, price))
    if weapons:
        known = known_weapons(tree.root_node)
        unknown = [w for w in weapons if W.name_hash(w) not in known]
        if unknown and not allow_unknown_weapons:
            raise EClone('armes absentes de la categorie weapons : {} (option --forcer pour passer outre)'.format(unknown))
        report.append('  compatibilite :')
        report += ['    ' + line for line in set_compatible_weapons(item, weapons, key_pool(tree.root_node))]
    else:
        _, entries = compat_entries(item)
        report.append('  compatibilite conservee : {}'.format(
            ', '.join(bytes(W.node_get_prop(e, H_EQUIPMENT_NAME).data).decode() for e in entries)))
    index = insert_sorted(parent, item)
    report.append('  insere en position {} sur {} dans sa categorie'.format(index, len(parent.child_table)))
    return parent, item, report


def clone_spawn(tree: Rtpc, source: str, new: str, entity: Optional[str] = None) -> Tuple[RtpcNode, RtpcNode, List[str]]:
    parent, src = find_item(tree.root_node, source, H_SPAWN_NAME)
    item = W.clone_node(src)
    item.name_hash = W.name_hash(new)
    set_str(item, H_SPAWN_NAME, new, 'nom')
    report = ['spawn_ammunition_defs : source trouvee, {} proprietes'.format(len(src.prop_table))]
    tags = W.node_get_prop(item, H_TAGS)
    if tags is not None and tags.type == k_type_str:
        parts = bytes(tags.data).decode('utf-8').split(',')
        parts = [new if p.strip() == source else p for p in parts]
        tags.data = ','.join(parts).encode('utf-8')
        report.append('  tags : "{}"'.format(tags.data.decode()))
    n = replace_hash_values(item, W.name_hash(source), W.name_hash(new))
    report.append('  u32 = hash de l\'ancien nom remplaces : {}'.format(n))
    if entity is not None:
        old = set_str(item, H_ENTITY, entity.replace('/', '\\'), 'entity')
        report.append('  entite : "{}" -> "{}"'.format(old, entity))
    else:
        report.append('  entite conservee : "{}"'.format(bytes(W.node_get_prop(item, H_ENTITY).data).decode()))
    index = insert_sorted(parent, item)
    report.append('  insere en position {} sur {}'.format(index, len(parent.child_table)))
    return parent, item, report


def verify_addition(original_bytes: bytes, produced_bytes: bytes, new_hash: int) -> None:
    """
    Relit le fichier produit, retire le nouvel objet et exige un arbre identique a l'original :
    prouve que l'operation n'a rien modifie d'autre.
    """
    produced = W.rtpc_from_bytes(produced_bytes)
    original = W.rtpc_from_bytes(original_bytes)
    hits = []

    def walk(node):
        for c in node.child_table:
            if c.name_hash == new_hash:
                hits.append((node, c))
            walk(c)

    walk(produced.root_node)
    if len(hits) != 1:
        raise EClone('verification : {} objet(s) de hash 0x{:08x} dans le fichier produit, 1 attendu'.format(
            len(hits), new_hash))
    remove_child(*hits[0])
    diff = W.rtpc_diff(original, produced)
    if diff:
        raise EClone('verification : le fichier produit differe de l\'original ailleurs que sur l\'ajout : ' + diff)
    if W.rtpc_to_binary(original) != original_bytes:
        raise EClone('verification : l\'original ne se reecrit pas a l\'identique (cas jamais observe sur COTW)')


# ---------------------------------------------------------------------------
# Programme principal
# ---------------------------------------------------------------------------

def load(vfs, base: Optional[Path], v_path: str) -> Tuple[bytes, str]:
    if base is not None:
        local = base / Path(v_path)
        if local.is_file():
            return local.read_bytes(), str(local)
    return _projet.read_by_path(vfs, v_path), 'jeu : ' + v_path


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(errors='replace')
    ap = argparse.ArgumentParser(description='Ajoute une munition copiee d\'une munition existante.')
    ap.add_argument('projet', help='projet DECA (dossier contenant db\\core.db)')
    ap.add_argument('--source', required=True, help='munition copiee, ex : equipment_ammo_243_sp_01')
    ap.add_argument('--nom', required=True, help='nom de la nouvelle munition, ex : equipment_ammo_243_test_01')
    ap.add_argument('--libelle', help='libelle anglais (propriete note)')
    ap.add_argument('--prix', type=int, help='prix en magasin')
    ap.add_argument('--armes', help='armes compatibles, separees par des virgules (defaut : celles de la source)')
    ap.add_argument('--entite', help='chemin de l\'entite .ee (defaut : celle de la source)')
    ap.add_argument('--sortie', required=True, help='dossier de sortie ; les fichiers sont ecrits dans SORTIE\\dropzone')
    ap.add_argument('--base', help='dossier au format dropzone dont partir, s\'il contient deja ces fichiers')
    ap.add_argument('--ecraser', action='store_true', help='remplacer des fichiers deja presents dans la sortie')
    ap.add_argument('--forcer', action='store_true', help='accepter des armes absentes de la categorie weapons')
    args = ap.parse_args(argv)

    if not args.nom.isascii() or not args.nom.replace('_', '').isalnum():
        raise SystemExit('ERREUR : le nom ne doit contenir que des lettres ASCII, des chiffres et des _')
    out_root = Path(args.sortie).expanduser().resolve() / 'dropzone'
    targets = {v: out_root / Path(v) for v in (EQUIPMENT_DATA, SPAWN_AMMO)}
    existing = [str(p) for p in targets.values() if p.exists()]
    if existing and not args.ecraser:
        raise SystemExit('ERREUR : fichiers deja presents (option --ecraser pour les remplacer) : {}'.format(existing))

    vfs = _projet.open_project(args.projet)
    base = Path(args.base).expanduser().resolve() if args.base else None
    weapons = [w.strip() for w in args.armes.split(',') if w.strip()] if args.armes else None
    report = ['# Nouvelle munition "{}" copiee de "{}"'.format(args.nom, args.source), '']
    try:
        eq_bytes, eq_src = load(vfs, base, EQUIPMENT_DATA)
        sp_bytes, sp_src = load(vfs, base, SPAWN_AMMO)
        report += ['Origine : ' + eq_src, 'Origine : ' + sp_src, '']
        eq_tree, sp_tree = W.rtpc_from_bytes(eq_bytes), W.rtpc_from_bytes(sp_bytes)
        _, eq_item, lines = clone_equipment(eq_tree, args.source, args.nom, args.libelle, args.prix, weapons, args.forcer)
        report += lines
        _, sp_item, lines = clone_spawn(sp_tree, args.source, args.nom, args.entite)
        report += lines
        eq_out, sp_out = W.rtpc_to_binary(eq_tree), W.rtpc_to_binary(sp_tree)
        verify_addition(eq_bytes, eq_out, eq_item.name_hash)
        verify_addition(sp_bytes, sp_out, sp_item.name_hash)
    except EClone as e:
        raise SystemExit('ERREUR : {}'.format(e))
    report += ['', 'Verification : relus, et identiques aux originaux une fois la nouvelle munition retiree.']

    for v_path, data in ((EQUIPMENT_DATA, eq_out), (SPAWN_AMMO, sp_out)):
        targets[v_path].parent.mkdir(parents=True, exist_ok=True)
        targets[v_path].write_bytes(data)
        report.append('Ecrit : {} ({} octets)'.format(targets[v_path], len(data)))
    text = '\n'.join(report) + '\n'
    (out_root.parent / 'rapport_clonage.txt').write_text(text, encoding='utf-8')
    sys.stdout.write(text)
    return 0


if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    sys.exit(main())
