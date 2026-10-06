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
description, icone, et par defaut la meme entite (.ee). Options qui completent la copie (constats en jeu du
2026-10-01, essais test7 et test8) :
  --entite-propre  une entite .ee propre a la nouvelle munition (entite.py) : sans elle, l'arme charge les
                   munitions de la source ;
  --stats-de OBJET une ligne d'equipment_stats_ui.bin copiee de celle d'OBJET (adf_ajouter_ligne.py) : sans
                   elle, ni stats affichees ni logo de possession au magasin ;
  --nom-affiche, --description
                   nom et description propres : cles de traduction <nom>_name et <nom>_description ajoutees a
                   string_ids.stringlookup (stringlookup.py), dans les 10 langues, et designees par display_name
                   et display_description de la nouvelle munition. Un texte sans prefixe vaut pour toutes les
                   langues ; "en=TEXT" (option repetee) donne celui d'une langue ;
  --balistique CHAMP=VALEUR, --classes 3;4;5;6
                   balistique propre (avec --entite-propre) : valeurs du .ammotunec de la nouvelle entite
                   (ammotune.py) ; la source et les autres munitions gardent les leurs. Les stats affichees viennent
                   de la ligne de stats (--stat) : les mettre en accord a la main.
  --icone IMAGE, --icone-de OBJET
                   icone propre (magasin, inventaire) : texture <nom>_alpha.ddsc ajoutee a cote de celle de la
                   source, designee par display_icon. --icone la fabrique depuis une image (PNG..., icone.py : mise a
                   la taille, BC3, niveaux de detail, en-tete de l'icone de la source) ; --icone-de copie celle d'un
                   autre objet (essai du mecanisme sans fabrication).
  --modele-de OBJET, --modele CHEMIN.modelc, --modele-douille CHEMIN.modelc
                   modele 3D propre de la cartouche chargee et de la douille ejectee (avec --entite-propre) :
                   base_model ajoute aux composants fresh_round et empty_casing du .epe de l'entite propre, comme
                   dans les entites des .223 du jeu (modele_munition.py) ; sans cela, l'arme montre le sien.
                   --modele-de reprend les modeles declares par l'entite d'une autre munition.

Usage, depuis la racine du depot, environnement virtuel active, DECA ferme :
    python cotw\\cloner_munition.py D:\\dev\\work\\hp --source equipment_ammo_243_sp_01 ^
        --nom equipment_ammo_243_test_01 --libelle ".243 Test" --prix 10 ^
        --entite-propre --stats-de equipment_ammo_243_sp_01 ^
        --nom-affiche "BALLES DE TEST .243" --nom-affiche "en=.243 TEST BULLET" ^
        --description "Munition de test." --sortie D:\\dev\\mods\\munition_test

Puis copier le CONTENU de D:\\dev\\mods\\munition_test\\dropzone dans le dossier dropzone du jeu.
Pour annuler : supprimer ces fichiers du dropzone.

--base DOSSIER : partir des fichiers presents dans DOSSIER (par exemple ton dropzone actuel, s'il
contient deja un equipment_data.bin modifie par un autre mod) plutot que des originaux du jeu.
"""
import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple, Union

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _projet  # noqa: E402  (installe aussi le substitut de zugbruecke hors Windows)
import rtpc_dump as D  # noqa: E402

import deca.ff_rtpc_write as W  # noqa: E402
from deca.ff_rtpc import Rtpc, RtpcNode, k_type_f32, k_type_str, k_type_u32  # noqa: E402

EQUIPMENT_DATA = 'settings/hp_settings/equipment_data.bin'
SPAWN_AMMO = 'settings/spawn_ammunition_defs.bin'

H_NAME = W.name_hash('name')                    # 0xd31ab684
H_NOTE = W.name_hash('note')                    # 0x2314c9ea, libelle anglais
H_PRICE = 0x33df3b2f                            # prix (d'apres cotw-mod-builder)
H_COMPAT = W.name_hash('compatibilities')        # 0x77c2b3ba, liste des armes compatibles
H_EQUIPMENT_NAME = W.name_hash('equipment_name')  # 0x36642277, nom d'une arme dans la liste
H_WEAPONS = W.name_hash('weapons')              # 0xc04b7961, categorie des armes
H_SPAWN_NAME = 0xf71c2a21                       # nom de l'objet dans spawn_ammunition_defs
H_TAGS = W.name_hash('tags')                    # 0x54697e8f
H_ENTITY = W.name_hash('entity')                # 0x399ed0ed
H_DISPLAY_NAME = W.name_hash('display_name')    # 0x94142e13, cle de traduction du nom affiche
H_DISPLAY_DESC = W.name_hash('display_description')  # 0x041df969, cle de traduction de la description
H_DISPLAY_ICON = W.name_hash('display_icon')    # 0x31e9a4e0, chemin de l'icone (.ddsc), seul champ d'icone des munitions


class EClone(Exception):
    """Operation impossible : le message explique pourquoi."""


# ---------------------------------------------------------------------------
# Outils sur l'arbre
# ---------------------------------------------------------------------------

def item_matches(root: RtpcNode, name: str, name_prop: int) -> List[Tuple[RtpcNode, RtpcNode]]:
    """[(parent, noeud)] des objets ``name`` : hash du noeud = hash32(name) ET propriete ``name_prop`` egale a
    ``name``. La double condition evite de confondre avec un autre noeud de meme hash."""
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
    return found


def find_item(root: RtpcNode, name: str, name_prop: int) -> Tuple[RtpcNode, RtpcNode]:
    """(parent, noeud) de l'unique objet ``name`` (voir item_matches)."""
    found = item_matches(root, name, name_prop)
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


def field_hash(field: str) -> int:
    """Nom d'un champ (cash_price, locked...) ou son hash en hexadecimal (0x4923c2e2)."""
    field = field.strip()
    return int(field, 16) if field.lower().startswith('0x') else W.name_hash(field)


def set_field(node: RtpcNode, field: str, text: str, what: str) -> Tuple[str, str]:
    """Change la valeur d'une propriete EXISTANTE en gardant son type (u32, f32 ou chaine)."""
    h = field_hash(field)
    p = W.node_get_prop(node, h)
    if p is None:
        raise EClone('{} : pas de propriete "{}" (0x{:08x})'.format(what, field, h))
    old = D.format_value(p, {})
    try:
        if p.type == k_type_u32:
            p.data = int(text, 0)
        elif p.type == k_type_f32:
            p.data = float(text)
        elif p.type == k_type_str:
            p.data = text.encode('utf-8')
        else:
            raise EClone('{} : type {} non modifiable par cet outil'.format(what, D.type_name(p.type)))
        W.prop_value_bytes(p)  # valide la valeur (bornes du u32, octet nul...)
    except (ValueError, W.ERtpcWrite) as e:
        raise EClone('{} : valeur "{}" refusee pour {} ({})'.format(what, text, field, e))
    return old, D.format_value(p, {})


def parse_field_spec(spec: str, with_item: bool) -> Tuple[Optional[str], str, str]:
    """'OBJET:CHAMP=VALEUR' (with_item) ou 'CHAMP=VALEUR'."""
    left, sep, value = spec.partition('=')
    if not sep:
        raise EClone('attendu {}CHAMP=VALEUR, recu "{}"'.format('OBJET:' if with_item else '', spec))
    item = None
    if with_item:
        item, sep, left = left.partition(':')
        if not sep:
            raise EClone('attendu OBJET:CHAMP=VALEUR, recu "{}"'.format(spec))
        item = item.strip()
    return item, left.strip(), value.strip()


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


def compat_key(index: int) -> int:
    """Cle de la i-eme entree d'une liste de compatibilite : hash32("compatibility_<i>").

    Retrouve le 2026-10-01 (resoudre_hashes + generateur positionnel) : les 69 cles inconnues des listes de
    compatibilite d'equipment_data.bin (munitions et lunettes) sont toutes de cette forme, de compatibility_0 a
    compatibility_67. Une seule exception dans le jeu : "compatibility_01" pour une entree d'index 1.
    """
    return W.name_hash('compatibility_{}'.format(index))


def known_weapons(root: RtpcNode) -> set:
    weapons = W.node_get_child(root, H_WEAPONS)
    if weapons is None:
        return set()
    return {c.name_hash for c in weapons.child_table}


def set_compatible_weapons(item: RtpcNode, weapons: Sequence[str]) -> List[str]:
    """
    Remplace la liste de compatibilite. L'entree d'index i recoit la cle hash32("compatibility_<i>"), comme
    dans le jeu. Une arme deja presente dans la liste de la source garde une copie de son entree (meme
    ammunition_slot_index) ; une arme nouvelle recoit une copie de la premiere entree de la source.
    """
    compat, entries = compat_entries(item)
    by_weapon = {bytes(W.node_get_prop(e, H_EQUIPMENT_NAME).data).decode('utf-8', 'replace'): e for e in entries}
    first = next((e for e in entries if e.name_hash == compat_key(0)), entries[0])
    new_entries = []
    for i, weapon in enumerate(weapons):
        e = W.clone_node(by_weapon.get(weapon, first))
        e.name_hash = compat_key(i)
        set_str(e, H_EQUIPMENT_NAME, weapon, 'entree de compatibilite')
        new_entries.append(e)
    others = [c for c in compat.child_table if W.node_get_prop(c, H_EQUIPMENT_NAME) is None]
    compat.child_table = sorted(others + new_entries, key=lambda c: c.name_hash)
    compat.child_map = {c.name_hash: c for c in compat.child_table}
    compat.child_count = len(compat.child_table)
    return ['compatibility_{} (0x{:08x}) -> {}'.format(i, e.name_hash, w)
            for i, (e, w) in enumerate(zip(new_entries, weapons))]


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
        report += ['    ' + line for line in set_compatible_weapons(item, weapons)]
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
        written = entity.replace('/', '\\')   # le jeu ecrit ce chemin avec des '\\'
        old = set_str(item, H_ENTITY, written, 'entity')
        report.append('  entite : "{}" -> "{}"'.format(old, written))
    else:
        report.append('  entite conservee : "{}"'.format(bytes(W.node_get_prop(item, H_ENTITY).data).decode()))
    index = insert_sorted(parent, item)
    report.append('  insere en position {} sur {}'.format(index, len(parent.child_table)))
    return parent, item, report


def display_keys(item: RtpcNode, new: str, name_specs: Sequence[str], desc_specs: Sequence[str]):
    """--nom-affiche / --description : la nouvelle munition designe des cles de traduction propres, <new>_name et
    <new>_description ; les cles de la source servent de modele aux lignes de dialogue de string_ids. Renvoie les
    entrees a ajouter a string_ids.stringlookup et les lignes du rapport."""
    import stringlookup as SL
    entries, report = [], []
    for specs, prop, suffix, what in ((name_specs, H_DISPLAY_NAME, '_name', 'display_name'),
                                      (desc_specs, H_DISPLAY_DESC, '_description', 'display_description')):
        if not specs:
            continue
        try:
            texts = SL.textes_par_langue(specs)
        except SL.EStringLookup as e:
            raise EClone('{} : {}'.format(what, e))
        key = new + suffix
        old = set_str(item, prop, key, what)
        entries.append(SL.Entree(key, texts, old))
        report.append('  {} : "{}" -> "{}"'.format(what, old, key))
    return entries, report


def verify_addition(original_bytes: bytes, produced_bytes: bytes, new_hash: Union[int, Iterable[int]]) -> None:
    """
    Relit le fichier produit, retire le ou les nouveaux objets (un hash, ou plusieurs pour un mod) et exige un arbre
    identique a l'original : prouve que l'operation n'a rien modifie d'autre.
    """
    produced = W.rtpc_from_bytes(produced_bytes)
    original = W.rtpc_from_bytes(original_bytes)
    for h in ([new_hash] if isinstance(new_hash, int) else list(new_hash)):
        hits = []

        def walk(node):
            for c in node.child_table:
                if c.name_hash == h:
                    hits.append((node, c))
                walk(c)

        walk(produced.root_node)
        if len(hits) != 1:
            raise EClone('verification : {} objet(s) de hash 0x{:08x} dans le fichier produit, 1 attendu'.format(
                len(hits), h))
        remove_child(*hits[0])
    diff = W.rtpc_diff(original, produced)
    if diff:
        raise EClone('verification : le fichier produit differe de l\'original ailleurs que sur l\'ajout : ' + diff)
    if W.rtpc_to_binary(original) != original_bytes:
        raise EClone('verification : l\'original ne se reecrit pas a l\'identique (cas jamais observe sur COTW)')


def icon_path(item: RtpcNode, what: str) -> str:
    prop = W.node_get_prop(item, H_DISPLAY_ICON)
    if prop is None or prop.type != k_type_str:
        raise EClone('{} : pas de display_icon (texte)'.format(what))
    return bytes(prop.data).decode('utf-8')


def new_icon_path(source_icon: str, new: str) -> str:
    """Chemin de l'icone de la nouvelle munition : dossier, suffixe "_alpha" et separateurs de celle de la source
    (textures\\ui\\items\\ammo\\<source>_alpha.ddsc -> textures\\ui\\items\\ammo\\<nouveau>_alpha.ddsc)."""
    sep = '\\' if '\\' in source_icon else '/'
    folder, _, file_name = source_icon.replace('\\', '/').rpartition('/')
    stem, dot, ext = file_name.rpartition('.')
    if not dot:
        raise EClone('chemin d\'icone sans extension : "{}"'.format(source_icon))
    name = new + ('_alpha' if stem.endswith('_alpha') else '') + '.' + ext
    return (folder + '/' + name if folder else name).replace('/', sep)


def entity_path_of(sp_tree: Rtpc, name: str) -> str:
    """Chemin virtuel (avec des '/') de l'entite d'un objet, d'apres spawn_ammunition_defs."""
    _, spawn = find_item(sp_tree.root_node, name, H_SPAWN_NAME)
    prop = W.node_get_prop(spawn, H_ENTITY)
    if prop is None or prop.type != k_type_str:
        raise EClone('{} : pas d\'entite dans spawn_ammunition_defs'.format(name))
    return bytes(prop.data).decode('utf-8').replace('\\', '/')


def entite_de(vfs, base: Optional[Path], name: str) -> Tuple[str, bytes, str]:
    """(chemin, octets, origine) de l'entite d'une munition du jeu, ou de la base (dropzone) si elle y est."""
    sp_bytes, _ = load(vfs, base, SPAWN_AMMO)
    path = entity_path_of(W.rtpc_from_bytes(sp_bytes), name)
    data, origin = load(vfs, base, path)
    return path, data, origin


def game_file_exists(vfs, v_path: str) -> bool:
    """Fichier present dans les archives du jeu, par son chemin ou par son hash (nom inconnu de DECA)."""
    from deca.hashes import hash32_func
    v_path = v_path.replace('\\', '/')
    nodes = list(_projet.nodes_by_path(vfs, v_path)) + list(vfs.nodes_where_match(v_hash=hash32_func(v_path.encode())))
    return any(n.offset is not None for n in nodes)


def own_models(vfs, sp_tree: Rtpc, m: 'Munition', entity: bytes, read, produced) -> Tuple[bytes, List[str]]:
    """--modele-de / --modele / --modele-douille : base_model ecrit dans l'entite propre ``entity``."""
    import modele_munition as MM
    lines = []
    if m.modele_de:
        path = entity_path_of(sp_tree, m.modele_de)
        try:
            data, origin = read(path)
        except SystemExit as e:       # _projet.read_by_path : chemin absent du projet
            raise EClone('modele de {} : {}'.format(m.modele_de, str(e).replace('ERREUR : ', '')))
        try:
            models = MM.modeles_declares(data)
        except (MM.EModele, MM.S.ESarc) as e:
            raise EClone('modele de {} : {}'.format(m.modele_de, e))
        if not models:
            raise EClone('{} ne declare aucun modele de cartouche : elle prend celui de l\'arme'.format(m.modele_de))
        lines.append('modeles repris de {} ({}) : {}'.format(m.modele_de, origin, ', '.join(
            '{} = {}'.format(k, v) for k, v in sorted(models.items()))))
        missing = [c for c in MM.COMPOSANTS if c not in models]
        if missing:
            lines.append('  {} : pas de modele chez {}, celui de l\'arme reste'.format(', '.join(missing), m.modele_de))
    else:
        models = {k: v for k, v in ((MM.CARTOUCHE, m.modele), (MM.DOUILLE, m.modele_douille)) if v}
    for comp, path in models.items():
        v_path = path.replace('\\', '/')
        if v_path not in produced and (vfs is None or not game_file_exists(vfs, v_path)):
            raise EClone('modele {} introuvable dans le jeu : {}'.format(comp, v_path))
    try:
        out, more = MM.modifier(entity, models)
    except (MM.EModele, MM.S.ESarc) as e:
        raise EClone('modele : {}'.format(e))
    return out, lines + more


def own_icon(vfs, eq_tree: Rtpc, item: RtpcNode, m: 'Munition', read) -> Tuple[str, bytes, List[str]]:
    """--icone / --icone-de : texture propre, ajoutee sous un chemin neuf, et designee par display_icon de ``item``
    (la nouvelle munition). Renvoie (chemin virtuel, contenu, lignes du rapport)."""
    import icone as I

    def read_texture(v: str, what: str) -> Tuple[bytes, str]:
        try:
            return read(v)
        except SystemExit as e:       # _projet.read_by_path : chemin absent du projet
            raise EClone('{} : {}'.format(what, str(e).replace('ERREUR : ', '')))

    old = icon_path(item, m.nom)
    written = new_icon_path(old, m.nom)
    v_path = written.replace('\\', '/')
    if vfs is not None and game_file_exists(vfs, v_path):
        raise EClone('icone : un fichier du jeu porte deja le chemin {} : il serait remplace'.format(v_path))
    if m.icone_de:
        _, other = find_item(eq_tree.root_node, m.icone_de, H_NAME)
        src_path = icon_path(other, m.icone_de).replace('\\', '/')
        data, origin = read_texture(src_path, 'icone de ' + m.icone_de)
        try:
            f = I.forme(data)
        except I.EIcone as e:
            raise EClone('icone de {} ({}) : {}'.format(m.icone_de, src_path, e))
        lines = ['Origine : ' + origin, 'icone : copie de celle de {} ({}x{}, format DXGI {}, {} octets)'.format(
            m.icone_de, f.largeur, f.hauteur, f.format, len(data))]
    else:
        image = Path(m.icone).expanduser()
        if not image.is_file():
            raise EClone('icone : image introuvable : {}'.format(image))
        modele, origin = read_texture(old.replace('\\', '/'), 'icone de la source')
        try:
            data, more = I.fabriquer(str(image), modele)
        except I.EIcone as e:
            raise EClone('icone : {}'.format(e))
        except OSError as e:          # PIL : fichier illisible ou format inconnu
            raise EClone('icone : image illisible : {} ({})'.format(image, e))
        lines = ['Origine (modele) : ' + origin, 'icone fabriquee depuis {}'.format(image), '  ' + more[0]] + more[1:]
    set_str(item, H_DISPLAY_ICON, written, 'display_icon')
    lines.append('  display_icon : "{}" -> "{}"'.format(old, written))
    return v_path, data, lines


# ---------------------------------------------------------------------------
# Construction : une munition (ligne de commande) ou plusieurs (mod, construire_mod.py, interface)
# ---------------------------------------------------------------------------

ENTITE_SOURCE = 'source'   # entite de la source (defaut de la ligne de commande ; l'arme charge alors la source)
ENTITE_PROPRE = 'propre'   # copie de l'entite de la source, item_id, tuning_file, _object_id et instance renommes
ENTITE_COPIE = 'copie'     # diagnostic : copie a l'octet pres de l'entite de la source, sous un nouveau chemin
ENTITES = (ENTITE_SOURCE, ENTITE_PROPRE, ENTITE_COPIE)


@dataclass
class Munition:
    """Une munition a ajouter. Les champs reprennent les options de la ligne de commande."""
    source: str
    nom: str
    libelle: Optional[str] = None
    prix: Optional[int] = None
    armes: Optional[List[str]] = None            # None : celles de la source
    entite: str = ENTITE_SOURCE
    entite_chemin: Optional[str] = None          # avec ENTITE_SOURCE : autre chemin d'entite (--entite)
    stats_de: Optional[str] = None               # ligne d'equipment_stats_ui copiee (None : pas de ligne)
    stats: Dict[str, str] = field(default_factory=dict)   # colonne -> valeur (--stat)
    stats_cellules_propres: bool = False
    nom_affiche: List[str] = field(default_factory=list)  # [LANGUE=]TEXTE (--nom-affiche)
    description: List[str] = field(default_factory=list)
    champs: List[str] = field(default_factory=list)       # CHAMP=VALEUR (--champ)
    forcer: bool = False                          # armes absentes de la categorie weapons acceptees
    balistique: Dict[str, str] = field(default_factory=dict)   # champ du .ammotunec -> valeur (--balistique)
    classes: Optional[List[int]] = None           # classes d'animaux du .ammotunec (--classes ; None : celles de la source)
    icone: Optional[str] = None                   # image dont fabriquer l'icone (--icone)
    icone_de: Optional[str] = None                # objet dont copier l'icone (--icone-de)
    modele_de: Optional[str] = None               # munition dont reprendre les modeles de cartouche (--modele-de)
    modele: Optional[str] = None                  # modele de la cartouche chargee, .modelc (--modele)
    modele_douille: Optional[str] = None          # modele de la douille ejectee, .modelc (--modele-douille)

    def valider(self):
        if not self.nom or not self.nom.isascii() or not self.nom.replace('_', '').isalnum():
            raise EClone('le nom ne doit contenir que des lettres ASCII, des chiffres et des _ : "{}"'.format(self.nom))
        if (self.stats or self.stats_cellules_propres) and not self.stats_de:
            raise EClone('{} : --stat et --stats-cellules-propres demandent --stats-de'.format(self.nom))
        if self.entite not in ENTITES:
            raise EClone('{} : entite "{}" inconnue ({})'.format(self.nom, self.entite, ', '.join(ENTITES)))
        if self.entite_chemin and self.entite != ENTITE_SOURCE:
            raise EClone('{} : --entite, --entite-copie et --entite-propre sont incompatibles'.format(self.nom))
        if (self.balistique or self.classes is not None) and self.entite != ENTITE_PROPRE:
            raise EClone('{} : --balistique et --classes demandent --entite-propre (sinon le reglage modifie serait '
                         'celui de la source, ou une copie de diagnostic)'.format(self.nom))
        if self.icone and self.icone_de:
            raise EClone('{} : --icone et --icone-de sont incompatibles'.format(self.nom))
        if (self.modele_de or self.modele or self.modele_douille) and self.entite != ENTITE_PROPRE:
            raise EClone('{} : --modele-de, --modele et --modele-douille demandent --entite-propre (le modele est '
                         'declare dans l\'entite de la munition)'.format(self.nom))
        if self.modele_de and (self.modele or self.modele_douille):
            raise EClone('{} : --modele-de est incompatible avec --modele et --modele-douille'.format(self.nom))


@dataclass
class Mod:
    """Munitions ajoutees dans l'ordre, et changements d'objets existants (faits une fois, avant les ajouts)."""
    munitions: List[Munition]
    controle_prix: List[str] = field(default_factory=list)   # NOM=PRIX (--controle-prix)
    modifier: List[str] = field(default_factory=list)        # OBJET:CHAMP=VALEUR (--modifier)


@dataclass
class Resultat:
    fichiers: List[Tuple[str, bytes]]   # (chemin virtuel, contenu), dans l'ordre d'ecriture
    rapport: List[str]

    def chemins(self) -> List[str]:
        return [v for v, _ in self.fichiers]


def load(vfs, base: Optional[Path], v_path: str) -> Tuple[bytes, str]:
    if base is not None:
        local = base / Path(v_path)
        if local.is_file():
            return local.read_bytes(), str(local)
    return _projet.read_by_path(vfs, v_path), 'jeu : ' + v_path


def fichiers_vises(munitions: Sequence[Munition]) -> List[str]:
    """Chemins virtuels fixes que la construction ecrira (sans les entites, connues seulement en cours de route)."""
    import adf_ajouter_ligne as AL
    import stringlookup as SL
    out = [EQUIPMENT_DATA, SPAWN_AMMO]
    if any(m.stats_de for m in munitions):
        out.append(AL.STATS_UI)
    if any(m.nom_affiche or m.description for m in munitions):
        out.append(SL.STRING_IDS)
    return out


def _chain_models(entries):
    """Une cle dont le modele est lui-meme ajoute par la meme passe (munition copiee d'une munition du mod) prend
    le modele de celle-ci : la ligne de dialogue copiee doit exister dans le fichier d'origine."""
    import stringlookup as SL
    by_key = {e.cle: e for e in entries}
    out = []
    for e in entries:
        model, seen = e.modele, set()
        while model in by_key and model not in seen:
            seen.add(model)
            model = by_key[model].modele
        out.append(e if model == e.modele else SL.Entree(e.cle, e.textes, model))
    return out


def construire(vfs, mod: Mod, base: Optional[Path] = None,
               etape: Optional[Callable[[str], None]] = None) -> Resultat:
    """
    Produit en memoire les fichiers du dropzone pour toutes les munitions du mod.

    Les fichiers partent de ``base`` (format dropzone) s'il les contient, sinon des originaux du jeu. Les munitions
    sont ajoutees l'une apres l'autre dans les memes arbres ; une munition peut donc copier une munition ajoutee
    plus haut dans la liste (son entite et sa ligne de stats sont lues dans les fichiers en cours). Tout est relu et
    verifie avant d'etre renvoye : retirer les munitions ajoutees redonne les fichiers de depart (aux changements
    d'objets existants pres). ``etape`` recoit un court message avant chaque etape longue (interface).
    Leve EClone avec un message explicite en cas de refus.
    """
    import adf_ajouter_ligne as AL
    import stringlookup as SL
    if not mod.munitions:
        raise EClone('aucune munition a ajouter')
    for m in mod.munitions:
        m.valider()
    names = [m.nom for m in mod.munitions]
    doubles = sorted({n for n in names if names.count(n) > 1})
    if doubles:
        raise EClone('nom present plusieurs fois dans le mod : {}'.format(', '.join(doubles)))
    single = len(mod.munitions) == 1
    produced: Dict[str, bytes] = {}

    def read(v_path: str) -> Tuple[bytes, str]:
        if v_path in produced:
            return produced[v_path], 'mod : ' + v_path
        return load(vfs, base, v_path)

    def step(text: str):
        if etape is not None:
            etape(text)

    m0 = mod.munitions[0]
    if single:
        report = ['# Nouvelle munition "{}" copiee de "{}"'.format(m0.nom, m0.source), '']
    else:
        report = ['# Mod : {} munitions ajoutees : {}'.format(len(names), ', '.join(names)), '']
    step('equipment_data, spawn_ammunition_defs')
    eq_bytes, eq_src = read(EQUIPMENT_DATA)
    sp_bytes, sp_src = read(SPAWN_AMMO)
    report += ['Origine : ' + eq_src, 'Origine : ' + sp_src, '']
    eq_tree, sp_tree = W.rtpc_from_bytes(eq_bytes), W.rtpc_from_bytes(sp_bytes)
    for m in mod.munitions:
        for tree, prop, src in ((eq_tree, H_NAME, eq_src), (sp_tree, H_SPAWN_NAME, sp_src)):
            if item_matches(tree.root_node, m.nom, prop):
                raise EClone('"{}" existe deja dans {} : nom deja utilise (commande deja lancee sur cette base ?)'.format(
                    m.nom, src))
    for spec in mod.controle_prix:
        name, _, value = spec.partition('=')
        if not value.isdigit():
            raise EClone('--controle-prix attend NOM=PRIX, recu "{}"'.format(spec))
        _, node = find_item(eq_tree.root_node, name.strip(), H_NAME)
        old = set_u32(node, H_PRICE, int(value), 'prix de controle')
        report.append('Controle : prix de {} : {} -> {}'.format(name.strip(), old, value))
    for spec in mod.modifier:
        item, fld, value = parse_field_spec(spec, with_item=True)
        _, node = find_item(eq_tree.root_node, item, H_NAME)
        old, new = set_field(node, fld, value, 'modification de ' + item)
        report.append('Modification : {} : {} : {} -> {}'.format(item, fld, old, new))
    # reference de la verification : le depart avec les seuls changements volontaires d'objets existants
    eq_reference = W.rtpc_to_binary(eq_tree) if (mod.controle_prix or mod.modifier) else eq_bytes

    text_entries, eq_hashes, sp_hashes, entities, icons = [], [], [], [], []
    for m in mod.munitions:
        if not single:
            report += ['', '## {} (copie de {})'.format(m.nom, m.source)]
        step('{} : equipment_data, entite'.format(m.nom))
        _, eq_item, lines = clone_equipment(eq_tree, m.source, m.nom, m.libelle, m.prix, m.armes, m.forcer)
        report += lines
        for spec in m.champs:
            _, fld, value = parse_field_spec(spec, with_item=False)
            old, new = set_field(eq_item, fld, value, 'nouvelle munition')
            report.append('  champ {} : {} -> {}'.format(fld, old, new))
        entries, lines = display_keys(eq_item, m.nom, m.nom_affiche, m.description)
        text_entries += entries
        report += lines
        if m.icone or m.icone_de:
            step('{} : icone'.format(m.nom))
            v_icon, icon_bytes, lines = own_icon(vfs, eq_tree, eq_item, m, read)
            report += lines
            produced[v_icon] = icon_bytes
            icons.append(v_icon)
        entity_path = m.entite_chemin
        if m.entite in (ENTITE_PROPRE, ENTITE_COPIE):
            import entite as E
            _, src_spawn = find_item(sp_tree.root_node, m.source, H_SPAWN_NAME)
            src_entity = bytes(W.node_get_prop(src_spawn, H_ENTITY).data).decode('utf-8').replace('\\', '/')
            if m.source not in src_entity:
                raise EClone('le chemin de l\'entite source ne contient pas son nom : {}'.format(src_entity))
            entity_path = src_entity.replace(m.source, m.nom)
            ee_bytes, ee_src = read(src_entity)
            if m.entite == ENTITE_COPIE:
                entity_out, lines = ee_bytes, ['entite : copie a l\'identique ({} octets) sous {}'.format(
                    len(ee_bytes), entity_path)]
            else:
                try:
                    entity_out, _renames, lines = E.clone_entity(ee_bytes, m.source, m.nom)
                except (E.EEntite, E.S.ESarc) as e:
                    raise EClone('entite : {}'.format(e))
                if m.balistique or m.classes is not None:
                    import ammotune as AT
                    try:
                        entity_out, more = AT.modifier_entite(entity_out, m.balistique, m.classes)
                    except (AT.EAmmotune, E.S.ESarc) as e:
                        raise EClone('balistique : {}'.format(e))
                    lines += ['  ' + line for line in more]
                    if m.classes is not None and m.stats_de and not any('class' in k.lower() for k in m.stats):
                        lines.append('  note : classes du reglage {} ; la ligne de stats garde celles de {} (--stat '
                                     'Class=... pour les afficher)'.format(';'.join(map(str, m.classes)), m.stats_de))
                if m.modele_de or m.modele or m.modele_douille:
                    entity_out, more = own_models(vfs, sp_tree, m, entity_out, read, produced)
                    lines += ['  ' + line for line in more]
            report += ['Origine : ' + ee_src] + lines
            produced[entity_path] = entity_out
            entities.append(entity_path)
        _, sp_item, lines = clone_spawn(sp_tree, m.source, m.nom, entity_path)
        report += lines
        eq_hashes.append(eq_item.name_hash)
        sp_hashes.append(sp_item.name_hash)

    step('verification equipment_data, spawn_ammunition_defs')
    eq_out, sp_out = W.rtpc_to_binary(eq_tree), W.rtpc_to_binary(sp_tree)
    verify_addition(eq_reference, eq_out, eq_hashes)
    verify_addition(sp_bytes, sp_out, sp_hashes)
    produced[EQUIPMENT_DATA], produced[SPAWN_AMMO] = eq_out, sp_out
    if not single:
        report.append('')

    stats_rows = [m for m in mod.munitions if m.stats_de]
    if stats_rows:
        import resoudre_hashes as RH
        step('equipment_stats_ui ({} ligne(s))'.format(len(stats_rows)))
        st_bytes, st_src = read(AL.STATS_UI)
        decode = AL.decoder(RH.FileReader(vfs))
        report += ['Origine : ' + st_src]
        report.append('equipment_stats_ui : ligne ajoutee, relue par DECA (original plus cette ligne, rien d\'autre)'
                      if single else 'equipment_stats_ui : lignes ajoutees une a une, chacune relue par DECA '
                                     '(fichier precedent plus cette ligne, rien d\'autre)')
        for m in stats_rows:
            try:
                st_bytes, lines = AL.add_rows(decode, st_bytes, [(m.stats_de, m.nom)], values=dict(m.stats),
                                              own_cells=m.stats_cellules_propres)
            except (AL.X.EXls, AL.AE.EAdfEcriture) as e:
                raise EClone('equipment_stats_ui : {}{}'.format('' if single else m.nom + ' : ', e))
            report += lines
        produced[AL.STATS_UI] = st_bytes

    if text_entries:
        step('string_ids ({} cle(s), le fichier du jeu prend environ 40 s)'.format(len(text_entries)))
        si_bytes, si_src = read(SL.STRING_IDS)
        chained = _chain_models(text_entries)
        try:
            strings_out, plan = SL.add(SL.decode, si_bytes, chained)
            SL.verify(SL.decode, si_bytes, strings_out, plan)
        except (SL.EStringLookup, SL.AE.EAdfEcriture) as e:
            raise EClone('string_ids : {}'.format(e))
        report += ['Origine : ' + si_src, 'string_ids : cles ajoutees dans les {} langues, relues (textes '
                   'd\'origine intacts, paires triees, chaine de relocation {})'.format(len(SL.LANGUES), plan.chain)]
        for e in chained:
            report.append('  {} (modele {}) :'.format(e.cle, e.modele))
            report += ['    {} : {}'.format(code, t) for code, t in zip(SL.LANGUES, e.textes)]
        produced[SL.STRING_IDS] = strings_out

    report += ['', 'Verification : relus, et identiques aux originaux une fois la nouvelle munition retiree.' if single
               else 'Verification : relus, et identiques aux fichiers de depart une fois les nouvelles munitions retirees.']
    order = [EQUIPMENT_DATA, SPAWN_AMMO] + entities + icons + [AL.STATS_UI, SL.STRING_IDS]
    return Resultat([(v, produced[v]) for v in order if v in produced], report)


def parse_assignments(specs: Sequence[str], option: str) -> Dict[str, str]:
    """["CHAMP=VALEUR", ...] -> {champ: valeur}, dans l'ordre ; un champ donne deux fois est refuse."""
    out: Dict[str, str] = {}
    for spec in specs:
        key, sep, value = spec.partition('=')
        if not sep or not key.strip():
            raise EClone('{} attend CHAMP=VALEUR, recu "{}"'.format(option, spec))
        if key.strip() in out:
            raise EClone('{} : champ "{}" donne deux fois'.format(option, key.strip()))
        out[key.strip()] = value.strip()
    return out


def ecrire_fichiers(fichiers: Sequence[Tuple[str, bytes]], racine: Path) -> List[str]:
    """Ecrit les fichiers sous ``racine`` (un dossier au format dropzone) ; renvoie les lignes du rapport."""
    lines = []
    for v_path, data in fichiers:
        target = racine / Path(v_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        lines.append('Ecrit : {} ({} octets)'.format(target, len(data)))
    return lines


# ---------------------------------------------------------------------------
# Programme principal
# ---------------------------------------------------------------------------

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
    ap.add_argument('--entite-copie', action='store_true',
                    help='diagnostic : copie octet pour octet de l\'entite .ee de la source sous un nouveau nom '
                         '(rien n\'est modifie a l\'interieur) ; sert a savoir si dropzone fournit un .ee de chemin nouveau')
    ap.add_argument('--entite-propre', action='store_true',
                    help='copier l\'entite .ee de la source pour la nouvelle munition (item_id au nouveau nom) : '
                         'sans cela, l\'arme charge les munitions de la source')
    ap.add_argument('--stats-de', metavar='OBJET',
                    help='ajouter a equipment_stats_ui.bin une ligne copiee de celle de OBJET (ex : la source), au nom '
                         'de la nouvelle munition : stats affichees et logo de possession au magasin')
    ap.add_argument('--stat', action='append', default=[], metavar='COLONNE=VALEUR',
                    help='avec --stats-de : valeur d\'une colonne de la ligne de stats (option repetable), ex : '
                         '"Penetration=17", "Expansion=20", "Class=3;4;5;6"')
    ap.add_argument('--stats-cellules-propres', action='store_true',
                    help='avec --stats-de : chaque case de la ligne de stats a sa propre cellule')
    ap.add_argument('--nom-affiche', action='append', default=[], metavar='[LANGUE=]TEXTE',
                    help='nom affiche en jeu : texte pour toutes les langues, puis au besoin "en=TEXT" pour une '
                         'langue (option repetable ; langues : en, fr, de, es, ru, pl, ja, pt, cs, zh)')
    ap.add_argument('--description', action='append', default=[], metavar='[LANGUE=]TEXTE',
                    help='description affichee en jeu, meme forme que --nom-affiche')
    ap.add_argument('--balistique', action='append', default=[], metavar='CHAMP=VALEUR',
                    help='avec --entite-propre : valeur d\'un champ du reglage balistique (.ammotunec) de la nouvelle '
                         'munition (option repetable), ex : projectile_penetration=0.08, tracer_settings.enabled=1 ; '
                         'champs : python cotw\\ammotune.py PROJET OBJET')
    ap.add_argument('--classes', metavar='CLASSES',
                    help='avec --entite-propre : classes d\'animaux du reglage balistique, ex : "3;4;5;6" ou "3-6"')
    ap.add_argument('--icone', metavar='IMAGE',
                    help='icone propre (magasin, inventaire) fabriquee depuis une image (PNG de preference, fond '
                         'transparent ; mise a la taille de l\'icone de la source, proportions gardees)')
    ap.add_argument('--icone-de', metavar='OBJET',
                    help='icone propre copiee de celle d\'OBJET, ex : equipment_ammo_12ga_slug_01')
    ap.add_argument('--modele-de', metavar='OBJET',
                    help='avec --entite-propre : modeles 3D de cartouche et de douille repris de l\'entite d\'une '
                         'autre munition qui en declare, ex : equipment_ammo_12ga_slug_01')
    ap.add_argument('--modele', metavar='CHEMIN',
                    help='avec --entite-propre : modele de la cartouche chargee (.modelc du jeu ou du mod)')
    ap.add_argument('--modele-douille', metavar='CHEMIN',
                    help='avec --entite-propre : modele de la douille ejectee (.modelc du jeu ou du mod)')
    ap.add_argument('--sortie', required=True, help='dossier de sortie ; les fichiers sont ecrits dans SORTIE\\dropzone')
    ap.add_argument('--base', help='dossier au format dropzone dont partir, s\'il contient deja ces fichiers')
    ap.add_argument('--ecraser', action='store_true', help='remplacer des fichiers deja presents dans la sortie')
    ap.add_argument('--forcer', action='store_true', help='accepter des armes absentes de la categorie weapons')
    ap.add_argument('--champ', action='append', default=[], metavar='CHAMP=VALEUR',
                    help='change un champ de la NOUVELLE munition (option repetable), ex : locked=3, 0x4923c2e2=1')
    ap.add_argument('--modifier', action='append', default=[], metavar='OBJET:CHAMP=VALEUR',
                    help='change un champ d\'un objet EXISTANT (option repetable), '
                         'ex : equipment_ammo_762x54r_fmj_01:locked=3')
    ap.add_argument('--controle-prix', action='append', default=[], metavar='NOM=PRIX',
                    help='change aussi le prix d\'un objet EXISTANT (option repetable) : si ce prix apparait en jeu, '
                         'le fichier produit est bien lu par le jeu')
    args = ap.parse_args(argv)

    if not args.nom.isascii() or not args.nom.replace('_', '').isalnum():
        raise SystemExit('ERREUR : le nom ne doit contenir que des lettres ASCII, des chiffres et des _')
    if (args.stat or args.stats_cellules_propres) and not args.stats_de:
        raise SystemExit('ERREUR : --stat et --stats-cellules-propres demandent --stats-de')
    if sum(bool(x) for x in (args.entite, args.entite_propre, args.entite_copie)) > 1:
        raise SystemExit('ERREUR : --entite, --entite-copie et --entite-propre sont incompatibles')
    import adf_ajouter_ligne as AL
    import ammotune as AT
    try:
        balistique = parse_assignments(args.balistique, '--balistique')
        classes = AT.parse_classes(args.classes) if args.classes is not None else None
    except (EClone, AT.EAmmotune) as e:
        raise SystemExit('ERREUR : {}'.format(e))
    munition = Munition(
        source=args.source, nom=args.nom, libelle=args.libelle, prix=args.prix,
        armes=[w.strip() for w in args.armes.split(',') if w.strip()] if args.armes else None,
        entite=ENTITE_PROPRE if args.entite_propre else (ENTITE_COPIE if args.entite_copie else ENTITE_SOURCE),
        entite_chemin=args.entite, stats_de=args.stats_de, stats=AL.parse_values(args.stat),
        stats_cellules_propres=args.stats_cellules_propres, nom_affiche=list(args.nom_affiche),
        description=list(args.description), champs=list(args.champ), forcer=args.forcer,
        balistique=balistique, classes=classes, icone=args.icone, icone_de=args.icone_de,
        modele_de=args.modele_de, modele=args.modele, modele_douille=args.modele_douille)
    mod = Mod([munition], controle_prix=list(args.controle_prix), modifier=list(args.modifier))
    out_root = Path(args.sortie).expanduser().resolve() / 'dropzone'
    existing = [str(out_root / Path(v)) for v in fichiers_vises(mod.munitions) if (out_root / Path(v)).exists()]
    if existing and not args.ecraser:
        raise SystemExit('ERREUR : fichiers deja presents (option --ecraser pour les remplacer) : {}'.format(existing))

    vfs = _projet.open_project(args.projet)
    base = _projet.base_dir(args.base)
    try:
        result = construire(vfs, mod, base)
        for v_path in result.chemins():
            if not args.ecraser and (out_root / Path(v_path)).exists():
                raise EClone('fichier deja present (option --ecraser) : {}'.format(out_root / Path(v_path)))
    except EClone as e:
        raise SystemExit('ERREUR : {}'.format(e))

    report = result.rapport + ecrire_fichiers(result.fichiers, out_root)
    text = '\n'.join(report) + '\n'
    (out_root.parent / 'rapport_clonage.txt').write_text(text, encoding='utf-8')
    sys.stdout.write(text)
    return 0


if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    sys.exit(main())
