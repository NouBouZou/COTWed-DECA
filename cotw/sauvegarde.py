#!/usr/bin/env python3
"""
sauvegarde.py -- lecture d'une sauvegarde de theHunter: COTW, sans rien modifier : conteneur, ADF, et pour le
profil du joueur un resume nomme (objets possedes, sac, debloques, emplacements, loadouts, armes).

Format (journal de recherche, 2026-10-03, releve sur les sauvegardes du PC utilisateur) :
  0x00  'SAVE', u32 0x0032d3c2, u64   (u64 non interprete ici : le rapport dit a quoi il est egal)
  0x10  'COMP', u32 0x0101,     u64 = taille decompressee
  0x20  flux zlib (pas de taille compressee ni de somme de controle)
  decompresse : 01 01 00 00 00, puis un ADF v4 qui porte ses types (commentaire "savegamedata_spec_imm.adf in THP").
Profil (instance thp_player_profile_adf, type PlayerProfileData_22) :
  EquipmentOwned / EquipmentAmount : 1 024 u32 en parallele (hash de l'objet, quantite), ranges en tete : retirer
    un objet decale les suivants ;
  EquipmentBackPack, EquipmentUnlocked : 1 024 u32 ;
  InventorySlot : 16 x PPSlot_1 { Slots : A[u32] } (arme, munition choisie, lunette...) ;
  LoadoutInfo.LoadoutData[].LoadoutEquipmentSlot : idem, par loadout ;
  WeaponData : 1 024 x PPWeaponData_4 (statistiques par arme).
Les objets sont designes par le hash32 de leur nom dans equipment_data.bin.

[hypothese] EquipmentBackPack et EquipmentUnlocked sont lus comme des listes de hashes s'ils contiennent des
valeurs de hash (> 0xFFFF) ; sinon comme des valeurs paralleles a EquipmentOwned. Le rapport dit lequel.

Noms : objets d'equipment_data.bin (toutes categories) du projet DECA, objets d'un fichier de mod (.cotwmod.json),
puis dictionnaire du fork et core_strings. Un hash qu'aucune source ne nomme est signale : objet retire du jeu, ou
objet d'un mod absent (inconnue E4 : la sauvegarde garde le hash d'un objet ajoute).

Usage, depuis la racine du depot :
    python cotw\\sauvegarde.py SAUVEGARDE [--projet D:\\dev\\work\\hp] [--mod MOD.cotwmod.json] [--langue fr]
                              [--charge SORTIE.bin] [--arbre]
  --charge : ecrit la charge decompressee (ADF), lisible par adf_dump.py ou DECA (Open External File)
  --arbre  : ajoute l'arbre complet de l'ADF au rapport
"""
import argparse
import io
import struct
import sys
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _projet  # noqa: E402  (installe aussi le substitut de zugbruecke hors Windows)

SAVE_MAGIC, COMP_MAGIC = b'SAVE', b'COMP'
SAVE_VERSION, COMP_VERSION = 0x0032d3c2, 0x0101
HEADER_SIZE = 16                      # magie, u32 version, u64
ADF5_PREFIX = b'\x01\x01\x00\x00\x00'
ADF_MAGIC = b' FDA'
PROFILE_TYPE = 'PlayerProfileData'    # prefixe du nom de type (PlayerProfileData_22 en 2026-10)
OWNED, AMOUNT, BACKPACK, UNLOCKED = 'EquipmentOwned', 'EquipmentAmount', 'EquipmentBackPack', 'EquipmentUnlocked'
INVENTORY_SLOT, LOADOUT_INFO, LOADOUT_DATA, LOADOUT_SLOT, WEAPON_DATA = (
    'InventorySlot', 'LoadoutInfo', 'LoadoutData', 'LoadoutEquipmentSlot', 'WeaponData')
DOCUMENTED = (OWNED, AMOUNT, BACKPACK, UNLOCKED, INVENTORY_SLOT, LOADOUT_INFO, WEAPON_DATA)
HASH_MIN = 0x10000                    # en dessous : une quantite ou un drapeau plutot qu'un hash
MODE_HASHES, MODE_PARALLEL, MODE_ABSENT = 'hashes', 'parallele', 'absent'


class ESauvegarde(Exception):
    pass


# ---------------------------------------------------------------------------
# Conteneur
# ---------------------------------------------------------------------------

@dataclass
class Conteneur:
    taille_fichier: int
    charge: bytes                                  # octets decompresses (prefixe ADF5 compris)
    save_version: Optional[int] = None
    save_u64: Optional[int] = None
    comp_version: Optional[int] = None
    comp_taille: Optional[int] = None
    zlib_offset: Optional[int] = None
    taille_compressee: int = 0
    remarques: List[str] = field(default_factory=list)

    def resume(self) -> str:
        if self.zlib_offset is None:
            return 'charge non compressee ({} octets)'.format(len(self.charge))
        save = 'SAVE v0x{:08x}'.format(self.save_version) if self.save_version is not None else 'sans SAVE'
        comp = 'COMP v0x{:04x}'.format(self.comp_version) if self.comp_version is not None else 'sans COMP'
        return '{}, {}, zlib a 0x{:x} : {} -> {} octets'.format(save, comp, self.zlib_offset, self.taille_compressee,
                                                                 len(self.charge))


def _u64_meaning(value: int, data_len: int, payload_len: int) -> str:
    for what, n in (('taille decompressee', payload_len), ('taille du fichier', data_len),
                    ('taille du fichier - 16', data_len - HEADER_SIZE),
                    ('taille du fichier - 32', data_len - 2 * HEADER_SIZE)):
        if value == n:
            return what
    return 'non reconnu'


def decoder(data: bytes) -> Conteneur:
    """Sauvegarde -> Conteneur. Accepte aussi une charge deja decompressee (ADF, avec ou sans prefixe ADF5)."""
    data = bytes(data)
    if data[:4] == ADF_MAGIC or (data[:5] == ADF5_PREFIX and data[5:9] == ADF_MAGIC):
        return Conteneur(len(data), data, remarques=['fichier deja decompresse (pas de SAVE / COMP)'])
    if data[:4] != SAVE_MAGIC:
        raise ESauvegarde('pas une sauvegarde : le fichier commence par {} (attendu "SAVE" ou un ADF)'.format(
            data[:8].hex(' ')))
    if len(data) < 2 * HEADER_SIZE:
        raise ESauvegarde('fichier tronque ({} octets)'.format(len(data)))
    c = Conteneur(len(data), b'')
    c.save_version, c.save_u64 = struct.unpack_from('<IQ', data, 4)
    comp = data.find(COMP_MAGIC, HEADER_SIZE, 256)
    if comp < 0:
        c.zlib_offset = HEADER_SIZE
        c.remarques.append('pas d\'en-tete COMP : flux zlib cherche juste apres SAVE')
    else:
        if comp != HEADER_SIZE:
            c.remarques.append('en-tete COMP a 0x{:x} (attendu 0x{:x})'.format(comp, HEADER_SIZE))
        c.comp_version, c.comp_taille = struct.unpack_from('<IQ', data, comp + 4)
        c.zlib_offset = comp + HEADER_SIZE
    d = zlib.decompressobj()
    try:
        c.charge = d.decompress(data[c.zlib_offset:])
    except zlib.error as e:
        raise ESauvegarde('flux zlib illisible a 0x{:x} : {}'.format(c.zlib_offset, e))
    c.taille_compressee = len(data) - c.zlib_offset - len(d.unused_data)
    if not d.eof:
        c.remarques.append('flux zlib tronque : {} octets lus'.format(len(c.charge)))
    if d.unused_data:
        c.remarques.append('{} octets apres le flux zlib'.format(len(d.unused_data)))
    if c.save_version != SAVE_VERSION:
        c.remarques.append('version SAVE inconnue : 0x{:08x} (connue : 0x{:08x})'.format(c.save_version, SAVE_VERSION))
    if c.comp_version is not None and c.comp_version != COMP_VERSION:
        c.remarques.append('version COMP inconnue : 0x{:x} (connue : 0x{:x})'.format(c.comp_version, COMP_VERSION))
    if c.comp_taille is not None and c.comp_taille != len(c.charge):
        c.remarques.append('COMP annonce {} octets, {} decompresses'.format(c.comp_taille, len(c.charge)))
    c.remarques.append('u64 de SAVE = {} : {}'.format(c.save_u64, _u64_meaning(c.save_u64, len(data),
                                                                                  len(c.charge))))
    return c


def adf_base(charge: bytes) -> int:
    if charge[:4] == ADF_MAGIC:
        return 0
    if charge[:5] == ADF5_PREFIX and charge[5:9] == ADF_MAGIC:
        return 5
    raise ESauvegarde('la charge n\'est pas un ADF (debut : {})'.format(charge[:12].hex(' ')))


def lire_adf(charge: bytes):
    """Adf lu par DECA : les types sont dans le fichier, pas besoin de projet."""
    from deca.ff_adf import Adf, EDecaMissingAdfType
    from deca.file import ArchiveFile
    adf = Adf()
    try:
        adf.deserialize(ArchiveFile(io.BytesIO(charge[adf_base(charge):])))
    except EDecaMissingAdfType as e:
        raise ESauvegarde('type ADF 0x{:08x} absent du fichier'.format(e.type_id))
    return adf


def type_name(adf, type_hash: int) -> str:
    td = adf.map_typedef.get(type_hash) if hasattr(adf, 'map_typedef') else None
    if td is None:
        td = adf.extended_map_typedef.get(type_hash)
    if td is None or td.name is None:
        return '0x{:08x}'.format(type_hash)
    return td.name.decode('utf-8', 'replace') if isinstance(td.name, bytes) else str(td.name)


def text(v) -> str:
    return bytes(v).decode('utf-8', 'replace') if isinstance(v, (bytes, bytearray)) else str(v)


# ---------------------------------------------------------------------------
# Noms des objets
# ---------------------------------------------------------------------------

@dataclass
class Objet:
    hash: int
    nom: str
    categorie: str = ''
    cle_nom: Optional[str] = None          # display_name (cle de traduction)
    nom_affiche: Optional[str] = None      # texte de la cle dans la langue choisie
    source: str = 'equipment_data'

    def libelle(self) -> str:
        s = self.nom
        if self.nom_affiche:
            s += ' "{}"'.format(self.nom_affiche)
        return s


class Index:
    """hash32 -> Objet, d'apres equipment_data.bin (toutes categories) et les fichiers de mod."""

    def __init__(self):
        self.objets: Dict[int, Objet] = {}
        self.sources: List[str] = []

    def __len__(self):
        return len(self.objets)

    def get(self, h: int) -> Optional[Objet]:
        return self.objets.get(h)

    def ajouter_equipment_data(self, data: bytes, noms=None, source: str = 'equipment_data.bin') -> int:
        """Tout noeud dont la propriete name a pour hash32 le hash du noeud est un objet ; sa categorie est le
        noeud de premier niveau qui le contient (ammo, weapons, sights...)."""
        import deca.ff_rtpc_write as W
        from deca.ff_rtpc import k_type_str
        from noms import hash32
        h_name, h_display = W.name_hash('name'), W.name_hash('display_name')
        rtpc = W.rtpc_from_bytes(data)
        n = 0

        def prop_str(node, h):
            p = W.node_get_prop(node, h)
            return text(p.data) if p is not None and p.type == k_type_str else None

        for cat in rtpc.root_node.child_table:
            label = (noms.get(cat.name_hash) if noms is not None else None) or '0x{:08x}'.format(cat.name_hash)
            stack = [cat]
            while stack:
                node = stack.pop()
                name = prop_str(node, h_name)
                if name and hash32(name) == node.name_hash and node.name_hash not in self.objets:
                    self.objets[node.name_hash] = Objet(node.name_hash, name, label, prop_str(node, h_display),
                                                        source=source)
                    n += 1
                stack.extend(node.child_table)
        self.sources.append('{} ({} objets)'.format(source, n))
        return n

    def ajouter_mod(self, path) -> int:
        """Munitions ajoutees par un fichier de mod (.cotwmod.json) : leur hash peut rester dans la sauvegarde."""
        import construire_mod as CMOD
        from noms import hash32
        mf = CMOD.load(path)
        n = 0
        for m in mf.mod.munitions:
            h = hash32(m.nom)
            if h not in self.objets:
                shown = CMOD.specs_to_texts(m.nom_affiche) if m.nom_affiche else {}
                self.objets[h] = Objet(h, m.nom, 'ammo (mod)', nom_affiche=shown.get('') or next(iter(shown.values()),
                                                                                                  None),
                                       source='mod {}'.format(Path(path).name))
                n += 1
        self.sources.append('mod {} ({} objets)'.format(Path(path).name, n))
        return n

    def textes(self, string_ids: bytes, langue: str = 'en') -> int:
        """Nom affiche de chaque objet (cle display_name) dans string_ids.stringlookup."""
        import stringlookup as SL
        vue = SL.Vue(string_ids)
        lang = SL.language_index(langue)
        if lang >= vue.n_lang:
            return 0
        offsets = dict(vue.pairs(lang))
        n = 0
        for o in self.objets.values():
            if o.cle_nom and o.nom_affiche is None:
                off = offsets.get(SL.key_hash(o.cle_nom))
                if off is not None:
                    o.nom_affiche = vue.text(off)
                    n += 1
        return n


def index_projet(vfs, langue: Optional[str] = 'en', noms=None, warnings: Optional[List[str]] = None) -> Index:
    """Index des objets du projet DECA : equipment_data.bin, et les noms affiches de string_ids."""
    import stringlookup as SL
    warnings = warnings if warnings is not None else []
    idx = Index()
    try:
        idx.ajouter_equipment_data(_projet.read_by_path(vfs, 'settings/hp_settings/equipment_data.bin'), noms)
    except (SystemExit, Exception) as e:  # noqa: BLE001 - le rapport reste utile sans les noms
        warnings.append('equipment_data.bin illisible : {}'.format(e))
        return idx
    if langue:
        try:
            idx.textes(_projet.read_by_path(vfs, SL.STRING_IDS), langue)
        except (SystemExit, Exception) as e:  # noqa: BLE001
            warnings.append('noms affiches indisponibles : {}'.format(e))
    return idx


# ---------------------------------------------------------------------------
# Profil
# ---------------------------------------------------------------------------

@dataclass
class Possede:
    index: int
    hash: int
    quantite: Optional[int]
    sac: Optional[bool] = None
    debloque: Optional[bool] = None


@dataclass
class Profil:
    instance: str
    type_nom: str
    possedes: List[Possede] = field(default_factory=list)
    trous: List[int] = field(default_factory=list)          # entrees non nulles apres une entree nulle
    sac: List[int] = field(default_factory=list)
    sac_mode: str = MODE_ABSENT
    debloques: List[int] = field(default_factory=list)
    debloques_mode: str = MODE_ABSENT
    emplacements: List[List[int]] = field(default_factory=list)
    loadouts: List[Tuple[str, List[List[int]]]] = field(default_factory=list)
    armes: List[Tuple[int, Dict[str, object]]] = field(default_factory=list)
    absents: List[str] = field(default_factory=list)

    def hashes(self) -> List[int]:
        """Tous les hashes d'objets cites (possedes, sac, debloques, emplacements, loadouts), sans doublon."""
        out = [p.hash for p in self.possedes]
        if self.sac_mode == MODE_HASHES:
            out += self.sac
        if self.debloques_mode == MODE_HASHES:
            out += self.debloques
        for slots in [self.emplacements] + [s for _n, s in self.loadouts]:
            for slot in slots:
                out += slot
        seen, uniq = set(), []
        for h in out:
            if h and h not in seen:
                seen.add(h)
                uniq.append(h)
        return uniq


def _member(values, name: str, depth: int = 3):
    """Valeur du membre ``name`` : au premier niveau, sinon dans les structures imbriquees (largeur d'abord)."""
    level = [values]
    for _ in range(depth):
        nxt = []
        for v in level:
            if isinstance(v, dict):
                if name in v:
                    return v[name]
                nxt += [x for x in v.values() if isinstance(x, dict)]
        level = nxt
    return None


def _u32_list(v) -> Optional[List[int]]:
    if isinstance(v, (list, tuple)) and all(isinstance(x, int) for x in v):
        return [int(x) for x in v]
    return None


def _slot_hashes(slot) -> List[int]:
    """Contenu d'un emplacement : PPSlot { Slots : A[u32] } (ou directement une liste)."""
    if isinstance(slot, dict):
        for v in slot.values():
            lst = _u32_list(v)
            if lst is not None:
                return lst
        return []
    lst = _u32_list(slot)
    if lst is not None:
        return lst
    return [slot] if isinstance(slot, int) else []


def _flag_list(values: Optional[List[int]], n_owned: int) -> Tuple[List[int], str]:
    if values is None:
        return [], MODE_ABSENT
    nonzero = [v for v in values if v]
    if any(v >= HASH_MIN for v in nonzero):
        return nonzero, MODE_HASHES
    return values[:n_owned], MODE_PARALLEL


def profil(adf) -> Optional[Profil]:
    """Resume du profil (premiere instance PlayerProfileData*, ou portant EquipmentOwned), None s'il n'y en a pas."""
    from deca.ff_adf import adf_value_extract
    for ins, value in zip(adf.table_instance, adf.table_instance_values):
        tname = type_name(adf, ins.type_hash)
        values = adf_value_extract(value)
        if not (tname.startswith(PROFILE_TYPE) or (isinstance(values, dict) and _member(values, OWNED) is not None)):
            continue
        p = Profil(text(ins.name), tname)
        p.absents = [m for m in DOCUMENTED if _member(values, m) is None]
        owned = _u32_list(_member(values, OWNED)) or []
        amount = _u32_list(_member(values, AMOUNT))
        seen_zero = False
        for i, h in enumerate(owned):
            if not h:
                seen_zero = True
                continue
            if seen_zero:
                p.trous.append(i)
            p.possedes.append(Possede(i, h, amount[i] if amount is not None and i < len(amount) else None))
        p.sac, p.sac_mode = _flag_list(_u32_list(_member(values, BACKPACK)), len(owned))
        p.debloques, p.debloques_mode = _flag_list(_u32_list(_member(values, UNLOCKED)), len(owned))
        sac, debloques = set(p.sac), set(p.debloques)
        for e in p.possedes:
            if p.sac_mode == MODE_HASHES:
                e.sac = e.hash in sac
            elif p.sac_mode == MODE_PARALLEL:
                e.sac = e.index < len(p.sac) and bool(p.sac[e.index])
            if p.debloques_mode == MODE_HASHES:
                e.debloque = e.hash in debloques
            elif p.debloques_mode == MODE_PARALLEL:
                e.debloque = e.index < len(p.debloques) and bool(p.debloques[e.index])
        slots = _member(values, INVENTORY_SLOT)
        if isinstance(slots, list):
            p.emplacements = [_slot_hashes(s) for s in slots]
        info = _member(values, LOADOUT_INFO)
        data = info.get(LOADOUT_DATA) if isinstance(info, dict) else None
        for i, ld in enumerate(data if isinstance(data, list) else []):
            label = 'loadout {}'.format(i)
            if isinstance(ld, dict):
                names = [text(v) for v in ld.values() if isinstance(v, (bytes, bytearray)) and v]
                if names:
                    label = '{} ({})'.format(label, names[0])
                ls = ld.get(LOADOUT_SLOT)
                p.loadouts.append((label, [_slot_hashes(s) for s in ls] if isinstance(ls, list) else []))
        weapons = _member(values, WEAPON_DATA)
        for i, w in enumerate(weapons if isinstance(weapons, list) else []):
            flat = w if isinstance(w, dict) else {'value': w}
            if any(isinstance(v, (int, float)) and not isinstance(v, bool) and v for v in flat.values()):
                p.armes.append((i, flat))
        return p
    return None


# ---------------------------------------------------------------------------
# Rapport
# ---------------------------------------------------------------------------

UNKNOWN = 'inconnu (objet retire du jeu, ou objet d\'un mod absent ?)'


class Nommeur:
    """Libelle d'un hash d'objet : index (equipment_data, mods), puis noms du projet / du dictionnaire."""

    def __init__(self, index: Optional[Index] = None, noms=None):
        self.index = index or Index()
        self.noms = noms

    def objet(self, h: int) -> Optional[Objet]:
        return self.index.get(h)

    def connu(self, h: int) -> bool:
        return self.index.get(h) is not None or (self.noms is not None and self.noms.get(h) is not None)

    def libelle(self, h: int) -> str:
        o = self.index.get(h)
        if o is not None:
            return '{} [{}]'.format(o.libelle(), o.categorie) if o.categorie else o.libelle()
        name = self.noms.get(h) if self.noms is not None else None
        return name if name else UNKNOWN

    def court(self, h: int) -> str:
        o = self.index.get(h)
        if o is not None:
            return o.nom
        name = self.noms.get(h) if self.noms is not None else None
        return name or '0x{:08x}'.format(h)


def _yes(v: Optional[bool]) -> str:
    return '' if v is None else ('oui' if v else '-')


def _slots_lines(slots: List[List[int]], nom: Nommeur, indent: str) -> List[str]:
    out = []
    for i, hashes in enumerate(slots):
        if hashes:
            out.append('{}[{}] {}'.format(indent, i, ', '.join(nom.court(h) for h in hashes)))
    if not out:
        out.append(indent + '(tous vides)')
    return out


def rapport(chemin: str, c: Conteneur, adf, p: Optional[Profil], nom: Nommeur, arbre: bool = False) -> List[str]:
    L = ['Sauvegarde : {} ({} octets)'.format(chemin, c.taille_fichier), 'Conteneur : ' + c.resume()]
    L += ['  ' + r for r in c.remarques]
    L.append('ADF : {} instance(s), {} types'.format(len(adf.table_instance), len(adf.table_typedef)))
    for ins in adf.table_instance:
        L.append('  {} : {} ({} octets)'.format(text(ins.name), type_name(adf, ins.type_hash), ins.size))
    if nom.index.sources:
        L.append('Noms des objets : ' + ', '.join(nom.index.sources))
    else:
        L.append('Noms des objets : aucun index (ouvrir avec --projet pour nommer les objets)')
    if p is None:
        L.append('Pas de profil de joueur (PlayerProfileData) dans ce fichier.')
    else:
        L += ['', 'Profil : instance {}, type {}'.format(p.instance, p.type_nom)]
        if p.absents:
            L.append('  membres releves absents : ' + ', '.join(p.absents))
        L.append('Objets possedes ({}) ; sac : {} ; debloques : {}'.format(len(p.possedes), p.sac_mode,
                                                                        p.debloques_mode))
        L.append('  {:>4}  {:<10}  {:>8}  {:<4}  {:<8}  {}'.format('#', 'hash', 'quantite', 'sac', 'debloque',
                                                                  'objet'))
        for e in p.possedes:
            L.append('  {:>4}  {:08x}    {:>8}  {:<4}  {:<8}  {}'.format(
                e.index, e.hash, '' if e.quantite is None else e.quantite, _yes(e.sac), _yes(e.debloque),
                nom.libelle(e.hash)))
        if p.trous:
            L.append('  ATTENTION : entrees non nulles apres une entree nulle (indices {}) : la liste n\'est pas '
                     'compacte'.format(', '.join(map(str, p.trous))))
        if p.sac_mode == MODE_HASHES:
            outside = [h for h in p.sac if h not in {e.hash for e in p.possedes}]
            if outside:
                L.append('  dans le sac sans etre possedes : ' + ', '.join(nom.court(h) for h in outside))
        L += ['', 'Emplacements (InventorySlot, indices du tableau) :']
        L += _slots_lines(p.emplacements, nom, '  ')
        L += ['', 'Loadouts ({}) :'.format(len(p.loadouts))]
        for label, slots in p.loadouts:
            L.append('  ' + label)
            L += _slots_lines(slots, nom, '    ')
        L += ['', 'WeaponData : {} entree(s) non vide(s)'.format(len(p.armes))]
        for i, w in p.armes:
            parts = []
            for k, v in w.items():
                s = '{}={}'.format(k, v)
                if isinstance(v, int) and not isinstance(v, bool) and v >= HASH_MIN and nom.connu(v):
                    s += ' ({})'.format(nom.court(v))
                parts.append(s)
            L.append('  [{}] {}'.format(i, ', '.join(parts)))
        unknown = [h for h in p.hashes() if not nom.connu(h)]
        L += ['', 'Hashes d\'objets inconnus : {}'.format(len(unknown))]
        for h in unknown:
            where = []
            if any(e.hash == h for e in p.possedes):
                where.append('possede')
            if p.sac_mode == MODE_HASHES and h in p.sac:
                where.append('sac')
            if p.debloques_mode == MODE_HASHES and h in p.debloques:
                where.append('debloque')
            if any(h in s for s in p.emplacements):
                where.append('emplacement')
            if any(h in s for _n, sl in p.loadouts for s in sl):
                where.append('loadout')
            L.append('  {:08x} : {}'.format(h, ', '.join(where)))
    if arbre:
        cotw_gui_path()
        from deca_gui.tree_items import adf_tree
        L += ['', 'Arbre de l\'ADF :']
        tree = adf_tree(adf, _NomsAdapter(nom), chemin)
        for child in tree.children():
            L += child.text_lines(1)
    return L


def cotw_gui_path():
    gui = Path(__file__).resolve().parent.parent / 'python' / 'deca_gui'
    if str(gui) not in sys.path:
        sys.path.insert(0, str(gui))


class _NomsAdapter:
    """Interface de noms.Noms (get, prefetch, wide) au-dessus d'un Nommeur, pour adf_tree."""

    def __init__(self, nom: Nommeur):
        self.nom = nom

    def prefetch(self, hashes: Iterable[int]):
        if self.nom.noms is not None:
            self.nom.noms.prefetch(hashes)

    def get(self, h):
        o = self.nom.index.get(h)
        if o is not None:
            return o.nom
        return self.nom.noms.get(h) if self.nom.noms is not None else None

    def wide(self, value, width):
        if width == 32:
            return self.get(value)
        return self.nom.noms.wide(value, width) if self.nom.noms is not None else None


@dataclass
class Lecture:
    """Tout ce que la lecture d'un fichier donne (utilise par l'interface)."""
    chemin: str
    conteneur: Conteneur
    adf: object
    profil: Optional[Profil]
    nommeur: Nommeur
    avertissements: List[str] = field(default_factory=list)


def lire(chemin, vfs=None, mods: Sequence[str] = (), langue: Optional[str] = 'en') -> Lecture:
    import noms as N
    data = Path(chemin).read_bytes()
    c = decoder(data)
    adf = lire_adf(c.charge)
    p = profil(adf)
    warnings: List[str] = []
    noms = N.Noms(vfs)
    idx = index_projet(vfs, langue, noms, warnings) if vfs is not None else Index()
    for m in mods:
        try:
            idx.ajouter_mod(m)
        except Exception as e:  # noqa: BLE001 - un mod illisible n'empeche pas la lecture
            warnings.append('mod {} illisible : {}'.format(m, e))
    nom = Nommeur(idx, noms)
    if p is not None:
        noms.prefetch(p.hashes())
    return Lecture(str(chemin), c, adf, p, nom, warnings)


def main(argv=None):
    ap = argparse.ArgumentParser(description='Lecture d\'une sauvegarde de theHunter: COTW (rien n\'est modifie).')
    ap.add_argument('sauvegarde')
    ap.add_argument('--projet', help='dossier du projet DECA (work\\hp) : noms des objets et noms affiches')
    ap.add_argument('--mod', action='append', default=[], help='fichier de mod (.cotwmod.json) : noms de ses objets')
    ap.add_argument('--langue', default='en', help='langue des noms affiches (en, fr, de...)')
    ap.add_argument('--charge', help='ecrire la charge decompressee (ADF) dans ce fichier')
    ap.add_argument('--arbre', action='store_true', help='ajouter l\'arbre complet de l\'ADF')
    args = ap.parse_args(argv)
    vfs = _projet.open_project(args.projet) if args.projet else None
    try:
        lec = lire(args.sauvegarde, vfs, args.mod, args.langue)
    except ESauvegarde as e:
        print('ERREUR : {}'.format(e), file=sys.stderr)
        return 1
    for w in lec.avertissements:
        print('AVERTISSEMENT : ' + w)
    print('\n'.join(rapport(lec.chemin, lec.conteneur, lec.adf, lec.profil, lec.nommeur, args.arbre)))
    if args.charge:
        Path(args.charge).write_bytes(lec.conteneur.charge)
        print('\nCharge decompressee ecrite : {} ({} octets)'.format(args.charge, len(lec.conteneur.charge)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
