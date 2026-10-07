#!/usr/bin/env python3
"""
dependances.py -- ce qu'une munition ajoutee doit encore a un objet d'origine du jeu.

But (priorite du 2026-10-06) : ajouter une munition qui ne depende d'aucune munition d'origine. Une munition
ajoutee part toujours d'une copie (cloner_munition) ; ce module dit, element par element, ce qui vient encore d'un
objet d'origine. Il juge les FICHIERS PRODUITS, compares aux fichiers de depart, et pas les options demandees :
une option qui n'a rien change, ou un changement fait par une autre voie, sont vus tels qu'ils sont.

Statuts, du plus genant au moins genant :
  partage  la munition designe une ressource d'un objet d'origine, que le jeu lit chez lui : entite, cle de
           traduction, icone, modele de cartouche de l'arme, son, nom d'instance du reglage... Vraie dependance :
           ce que le jeu affiche ou fait vient de l'autre objet.
  absent   element que la munition n'a pas : pas de ligne de stats (ni stats ni logo au magasin), aucun modele.
  inconnu  non determine (fichier illisible, cas non prevu) : le detail dit pourquoi.
  herite   valeur recopiee dans les fichiers de la munition et restee egale a celle d'origine : aucun lien en jeu,
           mais rien ne la distingue encore (poids, unites, etiquettes de spawn, valeurs balistiques...).
  propre   valeur ou fichier propre a la munition.

Reference de comparaison : la RACINE de la munition, c'est-a-dire sa source, ou la source de sa source quand
celle-ci est elle-meme ajoutee par le mod (un objet du mod n'est pas un objet d'origine). La ligne de stats est
comparee a la racine de son modele (stats_from), qui peut etre une autre munition que la source (test8 : FMJ).

Regles reprises du journal de recherche :
  - entite partagee, ou item_id egal a un autre objet : l'arme charge les munitions de cet objet (test4, test7) ;
  - nom d'instance du .ammotunec ou _object_id egaux a ceux de la source : changement de munition bloque (test13) ;
  - une ligne de stats placee apres la premiere ligne vide de la feuille n'est pas lue (test11) ;
  - modele de cartouche : le composant de l'arme au point d'attache l'emporte sur celui de la munition (test17) ;
    une arme a variantes commandees par evenements (show_event / hide_event) n'est pas analysee.

Le module ne fait que lire. cloner_munition.construire l'appelle a la fin de chaque construction (ligne de commande,
construire_mod, atelier) ; une erreur ici ne fait jamais echouer une construction : la section en cause devient
un element "inconnu".

Usage, depuis la racine du depot, environnement virtuel active, DECA ferme :
    python cotw\\dependances.py D:\\dev\\work\\hp cotw\\mods\\test18.cotwmod.json [--json SORTIE.json]
  construit le mod en memoire (rien n'est ecrit dans un dropzone) et n'affiche que l'analyse.
"""
import argparse
import dataclasses
import io
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _projet  # noqa: E402  (installe aussi le substitut de zugbruecke hors Windows)

import deca.ff_rtpc_write as W  # noqa: E402
from deca.ff_rtpc import k_type_f32, k_type_none, k_type_objid, k_type_str, k_type_u32  # noqa: E402

PARTAGE, ABSENT, INCONNU, HERITE, PROPRE = 'partage', 'absent', 'inconnu', 'herite', 'propre'
STATUTS = (PARTAGE, ABSENT, INCONNU, HERITE, PROPRE)   # ordre d'affichage : ce qui reste a faire d'abord

H_CLASS = W.name_hash('_class')
H_ITEM_ID = W.name_hash('item_id')
H_TUNING_FILE = W.name_hash('tuning_file')
H_OBJECT_ID = W.name_hash('_object_id')            # 0xcfff8405
H_SECONDARY_IDS = W.name_hash('secondary_ids')     # 0x5e330413 : liste des DLC qui debloquent l'objet
CLASSE_ITEM = b'CAmmunitionItem'
STRINGHASH_VIDE = 0xdeadbeef                       # StringHash vide dans les ADF du jeu
STATS_SHEET = 'ammo'
BASE_GAME = '-1'                                   # secondary_ids des objets du jeu de base
# Noms de proprietes deja etablis (journal de recherche) ; le dictionnaire du fork et la base du projet completent.
NOMS_CONNUS = ('name', 'note', 'display_name', 'display_description', 'display_icon', 'cash_price', 'locked',
               'units', 'slot_type', 'capacity_cost', 'compatibilities', 'secondary_ids', 'equipment_name',
               'ammunition_slot_index', 'desc', 'tags', 'entity', 'debug', 'item_id', 'tuning_file', 'item_category',
               '_object_id', '_class', 'base_model')


class EDependances(Exception):
    pass


@dataclass
class Element:
    rubrique: str
    statut: str
    detail: str
    origine: Optional[str] = None      # objet (munition, arme) dont l'element vient, s'il est connu
    du_mod: bool = False               # partage avec une munition ajoutee par le mod, pas avec un objet d'origine


@dataclass
class Bilan:
    munition: str
    source: str
    racine: str                        # premiere munition d'origine en remontant les sources
    ancetres: List[str] = field(default_factory=list)   # munitions du mod entre la munition et sa racine
    elements: List[Element] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def ajouter(self, rubrique: str, statut: str, detail: str, origine: Optional[str] = None, du_mod: bool = False):
        self.elements.append(Element(rubrique, statut, detail, origine, du_mod))

    def partage_avec_le_mod(self, rubrique: str, ancetre: str, detail: str):
        """Ressource d'une munition ajoutee plus haut dans le mod : pas une dependance au jeu, mais le jeu montre ou
        utilise la meme chose pour les deux munitions."""
        self.ajouter(rubrique, PARTAGE, '{} (munition du mod)'.format(detail), ancetre, du_mod=True)

    def compte(self) -> Dict[str, int]:
        return {s: sum(1 for e in self.elements if e.statut == s) for s in STATUTS}

    def par_statut(self, statut: str) -> List[Element]:
        return [e for e in self.elements if e.statut == statut]

    def independante(self) -> bool:
        """Plus rien de partage avec un objet d'origine (des valeurs heritees, ou des ressources partagees avec une
        autre munition du mod, peuvent rester)."""
        return not any(not e.du_mod for e in self.par_statut(PARTAGE))

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


# ---------------------------------------------------------------------------
# Acces aux fichiers : produits par la construction, ou de depart (originaux du jeu, ou base dropzone)
# ---------------------------------------------------------------------------

def norm(v_path: str) -> str:
    return v_path.replace('\\', '/')


class Contexte:
    """Fichiers produits et fichiers de depart, lus une fois ; noms des hashes."""

    def __init__(self, vfs, produced: Dict[str, bytes], read_start: Callable[[str], bytes]):
        self.vfs = vfs
        self.produced = {norm(k): v for k, v in produced.items()}
        self._read_start = read_start
        self._depart: Dict[str, Optional[bytes]] = {}
        self._rtpc: Dict[Tuple[str, bool], object] = {}
        self._vues: Dict[bool, object] = {}
        self._paires: Dict[Tuple[bool, int], Dict[int, int]] = {}
        self._stats: Dict[bool, object] = {}
        self.noms: Dict[int, List[str]] = {W.name_hash(n): [n] for n in NOMS_CONNUS}
        for (width, h), names in _projet.load_dictionary().items():
            if width == 32:
                self.noms.setdefault(h, list(names))
        self._cherches: set = set()

    def depart(self, v_path: str) -> Optional[bytes]:
        """Fichier de depart, ou None s'il n'existe pas (ou ne se lit pas : l'analyse le dira)."""
        v = norm(v_path)
        if v not in self._depart:
            try:
                self._depart[v] = self._read_start(v)
            except (SystemExit, Exception):   # _projet.read_by_path : SystemExit pour un chemin absent
                self._depart[v] = None
        return self._depart[v]

    def actuel(self, v_path: str) -> Optional[bytes]:
        """Version du mod si la construction l'a produite, sinon celle de depart."""
        v = norm(v_path)
        return self.produced[v] if v in self.produced else self.depart(v)

    def rtpc(self, v_path: str, actuel: bool = True):
        key = (norm(v_path), actuel)
        if key not in self._rtpc:
            data = self.actuel(v_path) if actuel else self.depart(v_path)
            self._rtpc[key] = W.rtpc_from_bytes(data) if data is not None else None
        return self._rtpc[key]

    def existe_dans_le_jeu(self, v_path: str) -> bool:
        if self.vfs is None:
            return self.depart(v_path) is not None
        import cloner_munition as CM
        try:
            return CM.game_file_exists(self.vfs, norm(v_path))
        except Exception:
            return self.depart(v_path) is not None

    def nommer(self, hashes: Iterable[int]):
        """Complete self.noms avec la base du projet (une requete par lot de hashes inconnus)."""
        wanted = {h for h in hashes if h not in self.noms and h not in self._cherches}
        if not wanted or self.vfs is None:
            return
        self._cherches |= wanted
        try:
            for h, names in _projet.resolve_hash32(self.vfs, wanted, use_dictionary=False).items():
                if names:
                    self.noms[h] = names
        except Exception:
            pass

    def nom(self, h: int) -> str:
        names = self.noms.get(h)
        return names[0] if names else '0x{:08x}'.format(h)

    def textes(self, cle: str) -> Optional[List[Optional[str]]]:
        """Texte de la cle dans chaque langue (None : absente), dans string_ids du mod s'il en produit un, sinon dans
        celui de depart ; None si string_ids manque ou ne se lit pas."""
        import stringlookup as SL
        mod = SL.STRING_IDS in self.produced
        if mod not in self._vues:
            data = self.actuel(SL.STRING_IDS)
            try:
                self._vues[mod] = SL.Vue(data) if data is not None else None
            except Exception:
                self._vues[mod] = None
        vue = self._vues[mod]
        if vue is None:
            return None
        h = SL.key_hash(cle)
        out = []
        for lang in range(vue.n_lang):
            if (mod, lang) not in self._paires:
                self._paires[(mod, lang)] = dict(vue.pairs(lang))
            off = self._paires[(mod, lang)].get(h)
            out.append(vue.text(off) if off is not None else None)
        return out

    def feuille_stats(self, actuel: bool):
        """(valeurs XLSBook de equipment_stats_ui, index de la feuille ammo) ; None si absent ou illisible."""
        if actuel not in self._stats:
            self._stats[actuel] = self._lire_stats(actuel)
        return self._stats[actuel]

    def _lire_stats(self, actuel: bool):
        import adf_ajouter_ligne as AL
        import adf_ecriture as AE
        data = self.actuel(AL.STATS_UI) if actuel else self.depart(AL.STATS_UI)
        if data is None:
            return None
        if self.vfs is not None:
            import resoudre_hashes as RH
            adf = AL.decoder(RH.FileReader(self.vfs))(data)
        else:   # fichier qui porte ses types (celui du jeu les porte)
            from deca.ff_adf import Adf
            from deca.file import ArchiveFile
            adf = Adf()
            adf.deserialize(ArchiveFile(io.BytesIO(data[AE.adf_base(data):])))
        if adf is None:
            return None
        book = AE.plain_values(adf, 0)
        sheet = next((i for i, s in enumerate(book.get('Sheet', []))
                      if bytes(s['Name']).decode('utf-8', 'replace') == STATS_SHEET), None)
        return (book, sheet) if sheet is not None else None


# ---------------------------------------------------------------------------
# Petits outils RTPC
# ---------------------------------------------------------------------------

def _str(node, h) -> Optional[str]:
    p = W.node_get_prop(node, h)
    return bytes(p.data).decode('utf-8', 'replace') if p is not None and p.type == k_type_str else None


def props_egales(a, b) -> bool:
    return a.name_hash == b.name_hash and a.type == b.type and W.prop_value_bytes(a) == W.prop_value_bytes(b)


def noeuds_egaux(a, b) -> bool:
    """Meme contenu (proprietes et enfants, dans le meme ordre), sans regarder le hash du noeud lui-meme."""
    if len(a.prop_table) != len(b.prop_table) or len(a.child_table) != len(b.child_table):
        return False
    if not all(props_egales(p, q) for p, q in zip(a.prop_table, b.prop_table)):
        return False
    return all(ca.name_hash == cb.name_hash and noeuds_egaux(ca, cb) for ca, cb in zip(a.child_table, b.child_table))


def valeur(p) -> str:
    """Valeur courte pour le rapport."""
    if p is None:
        return '(absent)'
    if p.type == k_type_u32:
        return str(p.data)
    if p.type == k_type_f32:
        return '{:.7g}'.format(p.data)
    if p.type == k_type_str:
        return json.dumps(bytes(p.data).decode('utf-8', 'replace'), ensure_ascii=False)
    import rtpc_dump as D
    return D.format_value(p, {})


def _walk(node, out):
    out.append(node)
    for c in node.child_table:
        _walk(c, out)
    return out


def _objids(tree) -> set:
    return {p.data for n in _walk(tree.root_node, []) for p in n.prop_table
            if p.type == k_type_objid and p.name_hash == H_OBJECT_ID}


def _item_node(tree):
    for n in _walk(tree.root_node, []):
        p = W.node_get_prop(n, H_CLASS)
        if p is not None and p.type == k_type_str and bytes(p.data) == CLASSE_ITEM:
            return n
    return None


def _cell(v) -> str:
    if isinstance(v, float):
        return '{:g}'.format(v)
    return str(v)


def _champ(c, v) -> str:
    """Valeur d'un champ du reglage balistique (ammotune.Champ) pour le rapport."""
    if v is None:
        return '(absent)'
    if c.genre == 'hash':
        return '0x{:08x}'.format(v)
    if c.genre == 'reel':
        return '{:.7g}'.format(v)
    return str(v)


def _classes_texte(levels: Sequence[int]) -> str:
    return ';'.join(str(x) for x in levels) if levels else '(aucune)'


def _classes_de(text) -> Optional[List[int]]:
    """Classes ecrites dans une case de la feuille de stats ("2;3;4;5;6"), ou None."""
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        return [int(x) for x in text.replace(',', ';').split(';') if x.strip()]
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Chaines de copie : racine d'une munition ou d'une ligne de stats
# ---------------------------------------------------------------------------

def racine(munitions: Sequence, nom: str, attribut: str = 'source') -> Optional[str]:
    """Premier objet hors du mod en remontant ``attribut`` (source, ou stats_de) depuis la munition ``nom`` ;
    None si la chaine s'arrete dans le mod (stats_de absent) ou boucle."""
    by_name = {m.nom: m for m in munitions}
    current, seen = getattr(by_name[nom], attribut), {nom}
    while current in by_name:
        if current in seen:
            return None
        seen.add(current)
        current = getattr(by_name[current], attribut)
    return current


def ancetres(munitions: Sequence, nom: str) -> List[str]:
    """Munitions du mod entre ``nom`` et sa racine, de la plus proche a la plus lointaine (sources successives)."""
    by_name = {m.nom: m for m in munitions}
    out, current = [], by_name[nom].source
    while current in by_name and current not in out and current != nom:
        out.append(current)
        current = by_name[current].source
    return out


# ---------------------------------------------------------------------------
# Analyse d'une munition
# ---------------------------------------------------------------------------

def _section(b: Bilan, rubrique: str, func, *args):
    """Une section qui leve devient un element inconnu : l'analyse continue."""
    try:
        return func(*args)
    except Exception as e:   # analyse informative : aucune erreur ne doit l'arreter
        b.ajouter(rubrique, INCONNU, 'analyse impossible ({}: {})'.format(type(e).__name__, e))
        return None


def _objet(ctx: Contexte, b: Bilan, node, ref):
    import cloner_munition as CM
    skip = {CM.H_NAME, CM.H_DISPLAY_NAME, CM.H_DISPLAY_DESC, CM.H_DISPLAY_ICON}
    ctx.nommer([p.name_hash for p in node.prop_table + ref.prop_table] +
               [c.name_hash for c in node.child_table + ref.child_table])
    ref_props = {p.name_hash: p for p in ref.prop_table if p.type != k_type_none}
    repris, changes = [], []
    for p in node.prop_table:
        if p.type == k_type_none or p.name_hash in skip:
            continue
        q = ref_props.pop(p.name_hash, None)
        if q is not None and props_egales(p, q):
            repris.append('{} = {}'.format(ctx.nom(p.name_hash), valeur(p)))
        else:
            changes.append('{} : {} -> {}'.format(ctx.nom(p.name_hash), valeur(q), valeur(p)))
    for h, q in ref_props.items():
        if h not in skip:
            changes.append('{} : {} -> (retire)'.format(ctx.nom(h), valeur(q)))
    if repris:
        b.ajouter('equipment_data, champs', HERITE, ', '.join(repris), b.racine)
    if changes:
        b.ajouter('equipment_data, champs', PROPRE, ', '.join(changes))

    ref_children = {c.name_hash: c for c in ref.child_table}
    for c in node.child_table:
        r = ref_children.pop(c.name_hash, None)
        if c.name_hash == CM.H_COMPAT:
            mine, theirs = _armes(c), (_armes(r) if r is not None else [])
            if mine == theirs:
                b.ajouter('armes compatibles', HERITE, ', '.join(mine) or '(aucune)', b.racine)
            else:
                b.ajouter('armes compatibles', PROPRE, '{} (racine : {})'.format(', '.join(mine) or '(aucune)',
                                                                                 ', '.join(theirs) or '(aucune)'))
        elif c.name_hash == H_SECONDARY_IDS:
            ids = [bytes(p.data).decode('utf-8', 'replace') for p in c.prop_table if p.type == k_type_str]
            label = ', '.join(ids) or '(vide)'
            if r is not None and noeuds_egaux(c, r):
                b.ajouter('DLC (secondary_ids)', HERITE, label, b.racine)
            else:
                b.ajouter('DLC (secondary_ids)', PROPRE, label)
            if ids and ids != [BASE_GAME]:
                b.notes.append('DLC {} : la munition n\'est visible que si l\'un de ces DLC est possede ou offert a tous '
                               '(releve du 2026-10-01)'.format(label))
        elif r is not None and noeuds_egaux(c, r):
            b.ajouter('equipment_data, enfant {}'.format(ctx.nom(c.name_hash)), HERITE, 'identique', b.racine)
        else:
            b.ajouter('equipment_data, enfant {}'.format(ctx.nom(c.name_hash)), PROPRE,
                      'ajoute' if r is None else 'modifie')
    for h in ref_children:
        b.ajouter('equipment_data, enfant {}'.format(ctx.nom(h)), PROPRE, 'retire')


def _armes(compat) -> List[str]:
    import cloner_munition as CM
    out = []
    for e in compat.child_table:
        name = _str(e, CM.H_EQUIPMENT_NAME)
        if name:
            out.append(name)
    return out


def _textes(ctx: Contexte, b: Bilan, node, ref, anc_nodes):
    import cloner_munition as CM
    for h, rubrique in ((CM.H_DISPLAY_NAME, 'nom affiche'), (CM.H_DISPLAY_DESC, 'description')):
        mine, theirs = _str(node, h), _str(ref, h)
        if mine is None:
            b.ajouter(rubrique, ABSENT, 'pas de cle de traduction (propriete 0x{:08x})'.format(h))
            continue
        if mine == theirs:
            b.ajouter(rubrique, PARTAGE, 'cle "{}" de {} : le jeu affiche son texte'.format(mine, b.racine), b.racine)
            continue
        same = [a for a, n in anc_nodes if _str(n, h) == mine]
        if same:
            b.partage_avec_le_mod(rubrique, same[0], 'cle "{}" de {} : le jeu affiche le meme texte'.format(mine, same[0]))
            continue
        texts = ctx.textes(mine)
        if texts is None:
            b.ajouter(rubrique, INCONNU, 'cle "{}" ; string_ids absent ou illisible'.format(mine))
        elif not any(t is not None for t in texts):
            b.ajouter(rubrique, INCONNU, 'cle "{}" absente de string_ids : le jeu afficherait la cle brute '
                                         '[a verifier]'.format(mine))
        else:
            found = [t for t in texts if t is not None]
            b.ajouter(rubrique, PROPRE, 'cle "{}", {} langue(s) sur {} : {}'.format(
                mine, len(found), len(texts), json.dumps(found[0], ensure_ascii=False)))


def _icone(ctx: Contexte, b: Bilan, m, node, ref, eq_depart, anc_nodes):
    import cloner_munition as CM
    mine, theirs = _str(node, CM.H_DISPLAY_ICON), _str(ref, CM.H_DISPLAY_ICON)
    if mine is None:
        b.ajouter('icone', ABSENT, 'pas de display_icon')
        return
    v = norm(mine)
    same = [a for a, n in anc_nodes if norm(_str(n, CM.H_DISPLAY_ICON) or '') == v]
    if theirs is not None and v == norm(theirs):
        b.ajouter('icone', PARTAGE, 'texture de {} : {}'.format(b.racine, v), b.racine)
    elif same:
        b.partage_avec_le_mod('icone', same[0], 'texture de {} : {}'.format(same[0], v))
    elif v in ctx.produced:
        data = ctx.produced[v]
        copied = None
        for other in [x for x in (getattr(m, 'icone_de', None), b.racine) if x]:
            try:
                _, item = CM.find_item(eq_depart.root_node, other, CM.H_NAME)
                path = _str(item, CM.H_DISPLAY_ICON)
            except CM.EClone:
                continue
            if path and ctx.depart(path) == data:
                copied = other
                break
        if copied:
            b.ajouter('icone', HERITE, 'texture {} : copie a l\'octet pres de l\'icone de {}'.format(v, copied), copied)
        else:
            b.ajouter('icone', PROPRE, 'texture {} ({} octets)'.format(v, len(data)))
    elif ctx.existe_dans_le_jeu(v):
        owner = _proprietaire(eq_depart, CM.H_DISPLAY_ICON, v)
        b.ajouter('icone', PARTAGE, 'texture du jeu {}{}'.format(v, ', celle de ' + owner if owner else ''), owner or 'jeu')
    else:
        b.ajouter('icone', INCONNU, 'texture {} introuvable dans le mod et dans le jeu'.format(v))


def _proprietaire(tree, prop_hash: int, v_path: str) -> Optional[str]:
    """Nom (propriete name ou desc) du premier objet dont la propriete ``prop_hash`` designe ``v_path``."""
    import cloner_munition as CM
    if tree is None:
        return None
    for n in _walk(tree.root_node, []):
        value = _str(n, prop_hash)
        if value is not None and norm(value) == v_path:
            return _str(n, CM.H_NAME) or _str(n, CM.H_SPAWN_NAME)
    return None


def _etiquettes(b: Bilan, spawn, spawn_ref, nom: str, ref_nom: str):
    import cloner_munition as CM
    mine = [t for t in (_str(spawn, CM.H_TAGS) or '').split(',') if t and t != nom]
    theirs = [t for t in (_str(spawn_ref, CM.H_TAGS) or '').split(',') if t and t != ref_nom]
    if mine == theirs:
        b.ajouter('etiquettes de spawn', HERITE, '{} [role inconnu]'.format(', '.join(mine) or '(aucune)'), b.racine)
    else:
        b.ajouter('etiquettes de spawn', PROPRE, '{} (racine : {})'.format(', '.join(mine) or '(aucune)',
                                                                          ', '.join(theirs) or '(aucune)'))


def _identite_entite(data: bytes) -> Tuple[set, set]:
    """(_object_id du .epe, noms d'instance du .ammotunec) d'une entite de munition."""
    import adf_ecriture as AE
    import ammotune as AT
    import modele_munition as MM
    import sarc as S
    s = S.parse(data)
    ids = _objids(W.rtpc_from_bytes(s.entries[MM.entree_epe(s)].data))
    return ids, set(AE.instance_names(s.entries[AT.entree_reglage(s)].data))


def _entite(ctx: Contexte, b: Bilan, nom: str, data: bytes, ref_data: bytes,
            anc_entities: Sequence[Tuple[str, bytes]], mod_names: set) -> Optional[List[int]]:
    """Contenu d'une entite propre compare a celui de l'entite de la racine (heritage, partage avec le jeu) et a
    celles des munitions du mod dont elle descend (partage avec le mod) ; renvoie les classes du reglage."""
    import adf_ecriture as AE
    import ammotune as AT
    import entite as E
    import modele_munition as MM
    import sarc as S
    ancestors = []
    for a, a_data in anc_entities:
        try:
            ancestors.append((a,) + _identite_entite(a_data))
        except Exception:
            continue
    s, r = S.parse(data), S.parse(ref_data)
    links = [e.path for e in s.entries if e.is_link]
    if links:
        b.ajouter('entite, fichiers lies', PARTAGE, 'designes par l\'entite, pas inclus : {}'.format(', '.join(links)))
    ref_by_path = {e.path: e for e in r.entries if not e.is_link}
    included = [e.path for e in s.entries if not e.is_link]

    # .epe : item_id, tuning_file, _object_id
    k_epe, k_ref = MM.entree_epe(s), MM.entree_epe(r)
    tree, ref_tree = W.rtpc_from_bytes(s.entries[k_epe].data), W.rtpc_from_bytes(r.entries[k_ref].data)
    item = _item_node(tree)
    item_id = _str(item, H_ITEM_ID) if item is not None else None
    if item_id == nom:
        b.ajouter('item_id (.epe)', PROPRE, item_id)
    elif item_id in mod_names:
        b.partage_avec_le_mod('item_id (.epe)', item_id, '"{}" : l\'arme chargerait les munitions de cet objet '
                              '(test4)'.format(item_id))
    else:
        b.ajouter('item_id (.epe)', PARTAGE, '"{}" : l\'arme chargerait les munitions de cet objet (test4)'.format(
            item_id), item_id)
    tuning = _str(item, H_TUNING_FILE) if item is not None else None
    if tuning is not None:
        forms = set(E.variants(tuning))
        own = [p for p in included if forms & set(E.variants(p))]
        if own:
            b.ajouter('tuning_file (.epe)', PROPRE, 'son propre reglage : {}'.format(own[0]))
        else:
            b.ajouter('tuning_file (.epe)', PARTAGE, '"{}" : reglage hors de l\'entite'.format(tuning))
    common = _objids(tree) & _objids(ref_tree)
    common_mod = [(a, ids & _objids(tree)) for a, ids, _n in ancestors if ids & _objids(tree)]
    if common:
        b.ajouter('_object_id (.epe)', PARTAGE, '{} identifiant(s) egaux a ceux de {} : deux entites chargees '
                  'ensemble declareraient les memes objets (test13)'.format(len(common), b.racine), b.racine)
    elif common_mod:
        a, ids = common_mod[0]
        b.partage_avec_le_mod('_object_id (.epe)', a, '{} identifiant(s) egaux a ceux de {} : deux entites chargees '
                              'ensemble declareraient les memes objets (test13)'.format(len(ids), a))
    else:
        b.ajouter('_object_id (.epe)', PROPRE, '{} identifiant(s) propres'.format(len(_objids(tree))))

    # .ammotunec : nom d'instance, valeurs, classes
    levels = None
    k_tune, k_tune_ref = AT.entree_reglage(s), AT.entree_reglage(r)
    tune, tune_ref = s.entries[k_tune].data, r.entries[k_tune_ref].data
    inst, inst_ref = AE.instance_names(tune), AE.instance_names(tune_ref)
    same_mod = [(a, names & set(inst)) for a, _ids, names in ancestors if names & set(inst)]
    if set(inst) & set(inst_ref):
        b.ajouter('reglage, nom d\'instance', PARTAGE, '"{}" comme {} : changement de munition bloque (test13)'.format(
            '", "'.join(sorted(set(inst) & set(inst_ref))), b.racine), b.racine)
    elif same_mod:
        a, names = same_mod[0]
        b.partage_avec_le_mod('reglage, nom d\'instance', a, '"{}" comme {} : changement de munition bloque '
                              '(test13)'.format('", "'.join(sorted(names)), a))
    else:
        b.ajouter('reglage, nom d\'instance', PROPRE, ', '.join('"{}"'.format(n) for n in inst))
    values, levels, fields = AT.lire(tune)
    ref_values, ref_levels, _ = AT.lire(tune_ref)
    same, changed = [], []
    for c in fields:
        v, rv = values[c.chemin], ref_values.get(c.chemin)
        if c.genre == 'hash' and v != STRINGHASH_VIDE:
            if v == rv:
                b.ajouter('reglage, {}'.format(c.chemin), PARTAGE, 'hash 0x{:08x} comme {} (ressource designee : '
                          'modele de fleche, collision...)'.format(v, b.racine), b.racine)
            else:
                b.ajouter('reglage, {}'.format(c.chemin), PROPRE, 'hash 0x{:08x}'.format(v))
            continue
        if v == rv:
            same.append(c.chemin)
        else:
            changed.append('{} : {} -> {}'.format(c.chemin, _champ(c, rv), _champ(c, v)))
    if same:
        b.ajouter('balistique', HERITE, '{} champ(s) sur {} egaux a {} : {}'.format(
            len(same), len(fields), b.racine, ', '.join(same)), b.racine)
    if changed:
        b.ajouter('balistique', PROPRE, ', '.join(changed))
    if levels == ref_levels:
        b.ajouter('classes d\'animaux', HERITE, _classes_texte(levels), b.racine)
    else:
        b.ajouter('classes d\'animaux', PROPRE, '{} (racine : {})'.format(_classes_texte(levels),
                                                                         _classes_texte(ref_levels)))

    # autres entrees incluses : son (.fmod_guids) et le reste
    for k, e in enumerate(s.entries):
        if e.is_link or k in (k_epe, k_tune):
            continue
        ref_e = ref_by_path.get(e.path.replace(nom, b.racine)) or ref_by_path.get(e.path)
        if e.path.endswith('.fmod_guids'):
            if ref_e is not None and ref_e.data == e.data:
                b.ajouter('son (.fmod_guids)', PARTAGE, 'GUID FMOD de {} : evenements sonores de {} [role exact '
                          'inconnu ; ecrire un son n\'est pas pris en charge]'.format(b.racine, b.racine), b.racine)
            else:
                b.ajouter('son (.fmod_guids)', PROPRE, '{} octets'.format(len(e.data)))
        elif ref_e is not None and ref_e.data == e.data:
            b.ajouter('entite, {}'.format(e.path.rsplit('/', 1)[-1]), HERITE, 'copie a l\'identique', b.racine)
        else:
            b.ajouter('entite, {}'.format(e.path.rsplit('/', 1)[-1]), PROPRE, '{} octets'.format(len(e.data)))
    return levels


def _modeles(ctx: Contexte, b: Bilan, entity: bytes, ref_entity: Optional[bytes], weapons: Sequence[str]):
    """Modele de cartouche et de douille affiche avec chaque arme compatible (regle de test17)."""
    import cloner_munition as CM
    import modele_munition as MM
    points = [(c.point, c.nom, c.modele) for c in MM.lire(entity) if c.nom in MM.COMPOSANTS]
    if not points:
        b.ajouter('modele', INCONNU, 'pas de composant fresh_round / empty_casing dans l\'entite')
        return
    ref_models = {}
    if ref_entity is not None:
        try:
            ref_models = {c.point: c.modele for c in MM.lire(ref_entity) if c.nom in MM.COMPOSANTS}
        except Exception:
            ref_models = {}
    sw = ctx.rtpc(CM.SPAWN_WEAPON)
    if sw is None:
        b.ajouter('modele', INCONNU, '{} absent : armes non analysees'.format(CM.SPAWN_WEAPON))
        return
    labels = {MM.CARTOUCHE: 'cartouche chargee', MM.DOUILLE: 'douille ejectee'}
    for w in weapons:
        try:
            path = norm(CM.entity_path_of(sw, w))
        except CM.EClone as e:
            b.ajouter('modele ({})'.format(w), INCONNU, str(e))
            continue
        data = ctx.actuel(path)
        if data is None:
            b.ajouter('modele ({})'.format(w), INCONNU, 'entite de l\'arme introuvable : {}'.format(path))
            continue
        try:
            comps = MM.lire_arme(data)
        except (MM.EModele, MM.S.ESarc) as e:
            b.ajouter('modele ({})'.format(w), INCONNU, 'entite de l\'arme illisible : {}'.format(e))
            continue
        converted = path in ctx.produced
        used = {p for p, _n, _m in points}
        others = sorted({c.point for c in comps} - used)
        if others and any(model for _p, _n, model in points):
            b.notes.append('{} montre aussi ses propres modeles aux points {}, autres que ceux des munitions : le modele '
                           'declare par la munition s\'y ajouterait ou non [a verifier en jeu]'.format(w, ', '.join(others)))
        for point, comp_name, model in points:
            rubrique = 'modele, {} ({})'.format(labels.get(comp_name, comp_name), w)
            at = [c for c in comps if c.point == point]
            if at and any(c.evenements for c in at):
                b.ajouter(rubrique, INCONNU, 'variantes commandees par evenements (show_event / hide_event) : cas non '
                                             'pris en charge')
            elif at:
                b.ajouter(rubrique, PARTAGE, 'modele de l\'arme, le meme pour toutes ses munitions : {}'.format(
                    at[0].modele), w)
            elif model is None and others:
                b.ajouter(rubrique, PARTAGE, 'aucun modele au point {} ; l\'arme montre les siens aux points {}'.format(
                    point, ', '.join(others)), w)
            elif model is None:
                b.ajouter(rubrique, ABSENT, 'ni l\'arme ni la munition ne declarent de modele au point {} '
                                            '[a verifier en jeu]'.format(point))
            elif norm(model) in ctx.produced:
                b.ajouter(rubrique, PROPRE, 'modele du mod : {}'.format(norm(model)))
            else:
                same = ref_models.get(point) is not None and norm(ref_models[point]) == norm(model)
                b.ajouter(rubrique, PARTAGE, 'modele du jeu{} : {}{}'.format(
                    ', comme ' + b.racine if same else '', norm(model),
                    ' (arme basculee par le mod)' if converted else ''), b.racine if same else 'jeu')


def _stats(ctx: Contexte, b: Bilan, m, munitions, levels: Optional[List[int]]):
    import adf_ajouter_ligne as AL
    import xlsbook as X
    current = ctx.feuille_stats(True)
    if current is None:
        if ctx.actuel(AL.STATS_UI) is None:
            b.ajouter('ligne de stats', ABSENT, 'pas de fichier equipment_stats_ui : ni stats ni logo de possession')
        else:
            b.ajouter('ligne de stats', INCONNU, 'equipment_stats_ui illisible (ou sans feuille "{}")'.format(STATS_SHEET))
        return
    book, sheet = current
    hits = [h for h in X.find_rows(book, m.nom, STATS_SHEET) if 0 in h.columns]
    if not hits:
        b.ajouter('ligne de stats', ABSENT, 'pas de ligne dans equipment_stats_ui : ni stats ni logo de possession au '
                                            'magasin (test7, test8)')
        return
    row_index = hits[0].row
    rows = X.sheet_rows(book, sheet)
    first_empty = X.first_empty_row(book, sheet)
    if row_index >= first_empty:
        b.ajouter('ligne de stats', INCONNU, 'ligne {} apres la premiere ligne vide ({}) : le jeu ne lit pas ces stats '
                                             '(test11)'.format(row_index, first_empty))
    header, row = rows[0], rows[row_index]
    template = racine(munitions, m.nom, 'stats_de') if getattr(m, 'stats_de', None) else b.racine
    template = template or b.racine
    ref_row = None
    start = ctx.feuille_stats(False)
    if start is not None:
        ref_hits = [h for h in X.find_rows(start[0], template, STATS_SHEET) if 0 in h.columns]
        if ref_hits:
            ref_row = X.sheet_rows(start[0], start[1])[ref_hits[0].row]
    if ref_row is None:
        b.ajouter('ligne de stats', PROPRE, 'ligne {} ; pas de ligne de {} pour comparer'.format(row_index, template))
    else:
        same, changed = [], []
        for c in range(1, len(header)):
            label = header[c] if isinstance(header[c], str) else 'colonne {}'.format(c)
            if row[c] == ref_row[c]:
                same.append('{} = {}'.format(label, _cell(row[c])))
            else:
                changed.append('{} : {} -> {}'.format(label, _cell(ref_row[c]), _cell(row[c])))
        if same:
            b.ajouter('ligne de stats', HERITE, 'valeurs de {} : {}'.format(template, ', '.join(same)), template)
        if changed:
            b.ajouter('ligne de stats', PROPRE, ', '.join(changed))
    try:
        shown = _classes_de(row[X.resolve_column(header, 'class')])
    except X.EXls:
        shown = None
    if shown is not None and levels is not None and shown != list(levels):
        b.notes.append('classes affichees au magasin ({}) differentes de celles du reglage balistique ({}) : la '
                       'ligne de stats et le tir ne disent pas la meme chose'.format(_classes_texte(shown),
                                                                                    _classes_texte(levels)))


def analyser_munition(ctx: Contexte, m, munitions: Sequence) -> Bilan:
    import cloner_munition as CM
    root = racine(munitions, m.nom) or m.source
    chain = ancetres(munitions, m.nom)
    b = Bilan(m.nom, m.source, root, list(chain))
    mod_names = {x.nom for x in munitions}
    eq, eq_start = ctx.rtpc(CM.EQUIPMENT_DATA), ctx.rtpc(CM.EQUIPMENT_DATA, actuel=False)
    sp, sp_start = ctx.rtpc(CM.SPAWN_AMMO), ctx.rtpc(CM.SPAWN_AMMO, actuel=False)
    if eq is None or eq_start is None or sp is None or sp_start is None:
        raise EDependances('equipment_data ou spawn_ammunition_defs illisible')
    _, node = CM.find_item(eq.root_node, m.nom, CM.H_NAME)
    _, ref = CM.find_item(eq_start.root_node, root, CM.H_NAME)
    anc_nodes = [(a, CM.find_item(eq.root_node, a, CM.H_NAME)[1]) for a in chain]
    _section(b, 'equipment_data', _objet, ctx, b, node, ref)
    _section(b, 'textes', _textes, ctx, b, node, ref, anc_nodes)
    _section(b, 'icone', _icone, ctx, b, m, node, ref, eq_start, anc_nodes)

    _, spawn = CM.find_item(sp.root_node, m.nom, CM.H_SPAWN_NAME)
    _, spawn_ref = CM.find_item(sp_start.root_node, root, CM.H_SPAWN_NAME)
    _section(b, 'etiquettes de spawn', _etiquettes, b, spawn, spawn_ref, m.nom, root)
    path, ref_path = norm(_str(spawn, CM.H_ENTITY) or ''), norm(_str(spawn_ref, CM.H_ENTITY) or '')
    anc_paths = [(a, norm(_str(CM.find_item(sp.root_node, a, CM.H_SPAWN_NAME)[1], CM.H_ENTITY) or '')) for a in chain]
    # contenu compare a l'entite de depart de la racine ; modeles compares a sa version actuelle (une bascule d'arme
    # peut lui avoir ajoute ceux de l'arme)
    ref_entity = ctx.depart(ref_path) if ref_path else None
    ref_entity_now = ctx.actuel(ref_path) if ref_path else None
    same_mod = [a for a, ap in anc_paths if ap == path]
    entity, levels = None, None
    if path == ref_path:
        b.ajouter('entite', PARTAGE, 'entite de {} ({}) : l\'arme charge les munitions de {}, avec son reglage et '
                  'son son (test4)'.format(root, path, root), root)
        entity = ref_entity_now
    elif same_mod:
        b.partage_avec_le_mod('entite', same_mod[0], 'entite de {} ({}) : l\'arme charge les munitions de {} '
                              '(test4)'.format(same_mod[0], path, same_mod[0]))
        entity = ctx.actuel(path)
    elif path in ctx.produced:
        entity = ctx.produced[path]
        if ref_entity is not None and entity == ref_entity:
            b.ajouter('entite', HERITE, '{} : copie a l\'octet pres de celle de {} (diagnostic)'.format(path, root), root)
        else:
            b.ajouter('entite', PROPRE, path)
        if ref_entity is None:
            b.ajouter('entite', INCONNU, 'entite de {} introuvable : contenu non compare'.format(root))
        else:
            anc_entities = [(a, ctx.actuel(ap)) for a, ap in anc_paths if ap and ctx.actuel(ap) is not None]
            levels = _section(b, 'entite', _entite, ctx, b, m.nom, entity, ref_entity, anc_entities, mod_names)
    else:
        entity = ctx.depart(path)
        owner = _proprietaire(sp_start, CM.H_ENTITY, path)
        if entity is None:
            b.ajouter('entite', INCONNU, '{} introuvable dans le mod et dans le jeu'.format(path))
        else:
            b.ajouter('entite', PARTAGE, 'entite du jeu {}{}'.format(path, ', celle de ' + owner if owner else ''),
                      owner or 'jeu')
    if entity is not None:
        compat = W.node_get_child(node, CM.H_COMPAT)
        _section(b, 'modele', _modeles, ctx, b, entity, ref_entity_now, _armes(compat) if compat is not None else [])
        if levels is None:   # entite non analysee (partagee) : classes lues pour le controle de la ligne de stats
            try:
                import ammotune as AT
                levels = AT.lire_entite(entity)[2]
            except Exception:
                levels = None
    _section(b, 'ligne de stats', _stats, ctx, b, m, munitions, levels)
    return b


def analyser(vfs, munitions: Sequence, produced: Dict[str, bytes], read_start: Callable[[str], bytes]) -> List[Bilan]:
    """Un bilan par munition ajoutee. ``produced`` : fichiers produits par la construction (chemin virtuel ->
    octets) ; ``read_start`` : lecture d'un fichier de depart (leve SystemExit ou une autre exception s'il manque)."""
    ctx = Contexte(vfs, produced, read_start)
    out = []
    for m in munitions:
        try:
            out.append(analyser_munition(ctx, m, munitions))
        except Exception as e:   # analyse informative : une munition illisible n'arrete pas les autres
            b = Bilan(m.nom, m.source, racine(munitions, m.nom) or m.source, ancetres(munitions, m.nom))
            b.ajouter('analyse', INCONNU, 'impossible ({}: {})'.format(type(e).__name__, e))
            out.append(b)
    return out


# ---------------------------------------------------------------------------
# Rapport
# ---------------------------------------------------------------------------

LEGENDE = ['Ce que chaque munition ajoutee doit encore a un objet d\'origine, d\'apres les fichiers produits :',
           '  partage = le jeu lit une ressource d\'un objet d\'origine ; absent = element manquant ;',
           '  inconnu = non determine ; herite = valeur recopiee, encore egale a l\'origine ; propre = propre a la munition.']


def rapport(bilans: Sequence[Bilan]) -> List[str]:
    lines = ['', '# Dependances restantes (cotw/dependances.py)'] + LEGENDE
    for b in bilans:
        origin = b.source if b.source == b.racine else '{}, elle-meme issue de {}'.format(b.source, b.racine)
        lines += ['', '## {} (copie de {})'.format(b.munition, origin)]
        for statut in STATUTS:
            for e in b.par_statut(statut):
                lines.append('  {:<8} {} : {}'.format(statut, e.rubrique, e.detail))
        lines += ['  note : ' + n for n in dict.fromkeys(b.notes)]
        c = b.compte()
        with_mod = sum(1 for e in b.par_statut(PARTAGE) if e.du_mod)
        counts = ['{} {}{}'.format(c[s], s, ' (dont {} avec le mod)'.format(with_mod) if s == PARTAGE and with_mod
                                   else '') for s in STATUTS]
        lines.append('  bilan : {}{}'.format(', '.join(counts), ' ; plus aucune ressource partagee avec un objet '
                                                               'd\'origine' if b.independante() else ''))
    return lines


# ---------------------------------------------------------------------------
# Ligne de commande
# ---------------------------------------------------------------------------

def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(errors='replace')
    ap = argparse.ArgumentParser(description='Ce que les munitions d\'un mod doivent encore aux objets du jeu.')
    ap.add_argument('projet', help='projet DECA (dossier contenant db\\core.db)')
    ap.add_argument('mod', help='fichier de mod (.cotwmod.json) ; construit en memoire, rien n\'est ecrit')
    ap.add_argument('--json', metavar='FICHIER', help='ecrit aussi les bilans en JSON')
    args = ap.parse_args(argv)
    import construire_mod as CMOD
    import cloner_munition as CM
    try:
        mf = CMOD.load(args.mod)
        vfs = _projet.open_project(args.projet)
        result = CMOD.construire(vfs, mf)
    except (CMOD.EMod, CM.EClone) as e:
        raise SystemExit('ERREUR : {}'.format(e))
    sys.stdout.write('\n'.join(rapport(result.dependances)) + '\n')
    if args.json:
        Path(args.json).write_text(json.dumps([b.to_dict() for b in result.dependances], indent=2, ensure_ascii=False)
                                   + '\n', encoding='utf-8')
    return 0


if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    sys.exit(main())
