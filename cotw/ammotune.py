#!/usr/bin/env python3
"""
ammotune.py -- reglage balistique d'une munition (.ammotunec, ADF "AmmunitionTuning") : lecture et modification.

Le jeu calcule la balistique dans l'exe ; le .ammotunec n'en donne que les parametres. Une munition ajoutee avec une
entite propre (cloner_munition --entite-propre) a son propre .ammotunec dans son .ee, sous un nom d'instance propre :
changer ses valeurs ne touche ni la source ni les autres munitions.

Format (releve sur les .ammotunec du jeu le 2026-10-03/06, types dans le fichier) :
  AmmunitionTuning (128 octets) : ammunition_class A[AmmunitionClass{level u8}] @0, max_range @16, diameter @20,
  length @24, drag_coefficient @28, kinetic_energy @32, mass @36, projectile_penetration @40, projectile_damage @44,
  projectile_expansion_rate @48, projectile_contraction_rate @52, projectile_max_expansion @56 (f32),
  projectiles_per_shot u8 @60, projectile_entity_spawn_tag, projectile_model_path, broken_projectile_model_path
  (StringHash 32 bits, 0xdeadbeef = vide) @64..72, scatter_multiplier @76, velocity_deviance @80 (f32),
  tracer_settings Tracer @84 {enabled u8, flash_freq, radius, tracer_scale_factor, position_offset (f32),
  color TracerColor {red, green, blue}}, pickup_settings PickupData @116 {collision_path, never_break, dog_requires_skill}.

Modification :
  - un champ de taille fixe (nombre, booleen, hash) est ecrit sur place ; on le designe par son chemin
    ("tracer_settings.enabled") ou par une fin de chemin unique ("enabled", "color.red") ;
  - les classes (ammunition_class) : sur place si leur nombre ne grandit pas (nombre d'elements mis a jour), sinon
    le tableau est recopie a la fin de l'instance (adf_ecriture : chaine de relocation tenue a jour, tables
    decalees) ; un reglage sans classe (tableau vide, pas de chaine de relocation) n'en recoit pas ;
  - le fichier produit est relu par DECA : chaque champ vaut l'ancienne valeur ou la nouvelle (arrondie en f32), et
    rien d'autre ne change.

Usage (lecture), depuis la racine du depot, environnement virtuel active, DECA ferme :
    python cotw\\ammotune.py D:\\dev\\work\\hp equipment_ammo_243_sp_01
    python cotw\\ammotune.py D:\\dev\\work\\hp equipment_ammo_243_test_03 --base D:\\dev\\mods\\test15\\dropzone
"""
import argparse
import io
import math
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import adf_ecriture as AE  # noqa: E402

TUNING_TYPE = 0x5bb59511
CLASSES = 'ammunition_class'
CLASS_MIN, CLASS_MAX = 1, 9          # classes d'animaux du jeu (1 a 9)
STRINGHASH_META = 9
STRUCT_META = 1


class EAmmotune(Exception):
    pass


@dataclass
class Champ:
    chemin: str        # ex : "tracer_settings.color.red"
    offset: int        # relatif au debut de l'instance
    fmt: str           # format struct ('<f', '<B'...)
    genre: str         # 'reel', 'entier', 'hash'


def decode(data: bytes):
    from deca.ff_adf import Adf
    from deca.file import ArchiveFile
    adf = Adf()
    adf.deserialize(ArchiveFile(io.BytesIO(data[AE.adf_base(data):])))
    return adf


def _formats():
    from deca import ff_adf as A
    return {A.typedef_u8: ('<B', 'entier'), A.typedef_s8: ('<b', 'entier'), A.typedef_u16: ('<H', 'entier'),
            A.typedef_s16: ('<h', 'entier'), A.typedef_u32: ('<I', 'entier'), A.typedef_s32: ('<i', 'entier'),
            A.typedef_u64: ('<Q', 'entier'), A.typedef_s64: ('<q', 'entier'), A.typedef_f32: ('<f', 'reel'),
            A.typedef_f64: ('<d', 'reel')}


def tuning_index(adf) -> int:
    found = [i for i, ins in enumerate(adf.table_instance) if ins.type_hash == TUNING_TYPE]
    if len(found) != 1:
        raise EAmmotune('{} instance(s) AmmunitionTuning (type 0x{:08x}), 1 attendue'.format(len(found), TUNING_TYPE))
    return found[0]


def champs(adf) -> List[Champ]:
    """Champs de taille fixe de l'AmmunitionTuning, structures imbriquees comprises, d'apres les types du fichier."""
    types = adf.extended_map_typedef
    formats = _formats()
    out = []

    def walk(type_hash: int, prefix: str, base: int, depth: int):
        td = types.get(type_hash)
        if td is None or td.metatype != STRUCT_META or depth > 8:
            raise EAmmotune('type 0x{:08x} : structure attendue'.format(type_hash))
        for m in td.members:
            path = prefix + m.name_utf8
            if m.type_hash in formats:
                fmt, genre = formats[m.type_hash]
                out.append(Champ(path, base + m.offset, fmt, genre))
                continue
            sub = types.get(m.type_hash)
            if sub is None:
                continue
            if sub.metatype == STRUCT_META:
                walk(m.type_hash, path + '.', base + m.offset, depth + 1)
            elif sub.metatype == STRINGHASH_META and sub.size == 4:
                out.append(Champ(path, base + m.offset, '<I', 'hash'))
            # tableaux (ammunition_class) : traites a part

    walk(TUNING_TYPE, '', 0, 0)
    return out


def resolve(fields: Sequence[Champ], name: str) -> Champ:
    """Champ designe par son chemin complet, ou par une fin de chemin unique ("enabled", "color.red")."""
    name = name.strip()
    exact = [c for c in fields if c.chemin == name]
    if exact:
        return exact[0]
    last = [c for c in fields if c.chemin.endswith('.' + name)]
    if len(last) == 1:
        return last[0]
    if len(last) > 1:
        raise EAmmotune('champ "{}" ambigu : {}'.format(name, ', '.join(c.chemin for c in last)))
    raise EAmmotune('champ "{}" inconnu ; champs : {}'.format(name, ', '.join(c.chemin for c in fields)))


def parse_value(c: Champ, text: str):
    t = str(text).strip()
    try:
        if c.genre == 'reel':
            v = float(t.replace(',', '.'))
            if not math.isfinite(v):
                raise ValueError
            if c.fmt == '<f' and abs(v) > 3.4e38:
                raise ValueError
            return v
        v = int(t, 0)
    except ValueError:
        raise EAmmotune('{} : valeur "{}" invalide ({} attendu)'.format(
            c.chemin, text, 'nombre' if c.genre == 'reel' else 'entier, ou 0x... pour un hash'))
    size = struct.calcsize(c.fmt)
    signed = c.fmt[-1].islower()
    lo, hi = (-(1 << (8 * size - 1)), (1 << (8 * size - 1)) - 1) if signed else (0, (1 << (8 * size)) - 1)
    if not lo <= v <= hi:
        raise EAmmotune('{} : {} hors de l\'intervalle [{}, {}]'.format(c.chemin, v, lo, hi))
    return v


def parse_classes(text) -> List[int]:
    """"3;4;5;6", "3-6" ou une liste d'entiers."""
    if isinstance(text, (list, tuple)):
        items = list(text)
    else:
        t = str(text).strip()
        if '-' in t and ';' not in t:
            a, _, b = t.partition('-')
            try:
                lo, hi = int(a), int(b)
            except ValueError:
                raise EAmmotune('classes "{}" : forme "3-6" ou "3;4;5;6" attendue'.format(text))
            items = list(range(lo, hi + 1))
        else:
            items = [x for x in t.split(';') if x.strip()]
    try:
        levels = [int(x) for x in items]
    except (TypeError, ValueError):
        raise EAmmotune('classes "{}" : entiers attendus'.format(text))
    if not levels or any(not CLASS_MIN <= v <= CLASS_MAX for v in levels):
        raise EAmmotune('classes "{}" : au moins une, chacune de {} a {}'.format(text, CLASS_MIN, CLASS_MAX))
    return levels


def lire(data: bytes) -> Tuple[Dict[str, object], List[int], List[Champ]]:
    """(valeur de chaque champ, classes, champs)."""
    adf = decode(data)
    i = tuning_index(adf)
    fields = champs(adf)
    start = AE.adf_base(data) + adf.table_instance[i].offset
    values = {c.chemin: struct.unpack_from(c.fmt, data, start + c.offset)[0] for c in fields}
    levels = [e['level'] for e in AE.plain_values(adf, i)[CLASSES]]
    return values, levels, fields


def _f32(v: float) -> float:
    return struct.unpack('<f', struct.pack('<f', v))[0]


def modifier(data: bytes, valeurs: Dict[str, str], classes=None) -> Tuple[bytes, List[str]]:
    """Ecrit les valeurs demandees (champ -> texte) et, au besoin, les classes ("3;4;5;6", "3-6" ou liste
    d'entiers) ; verifie et renvoie (nouveau fichier, lignes du rapport)."""
    adf = decode(data)
    i = tuning_index(adf)
    fields = champs(adf)
    old_values, old_levels, _f = lire(data)
    wanted: Dict[str, object] = {}
    for name, text in valeurs.items():
        c = resolve(fields, name)
        if c.chemin in wanted:
            raise EAmmotune('champ "{}" donne deux fois'.format(c.chemin))
        wanted[c.chemin] = parse_value(c, text)
    by_path = {c.chemin: c for c in fields}
    out = bytearray(data)
    start = AE.adf_base(data) + adf.table_instance[i].offset
    lines = []
    for path, v in wanted.items():
        c = by_path[path]
        struct.pack_into(c.fmt, out, start + c.offset, v)
        new = struct.unpack_from(c.fmt, out, start + c.offset)[0]
        lines.append('balistique : {} : {} -> {}'.format(path, _show(c, old_values[path]), _show(c, new)))
    levels = parse_classes(classes) if classes is not None else None
    if levels is not None:
        if not old_levels:
            # tableau vide (cible 0) : l'instance n'a alors aucun champ pointeur, donc pas de chaine de relocation ;
            # on ignore ou le jeu en noterait la tete (releve 2026-10-06 : clay_pigeon, 22_hp, 50_jhp, 762x54r_fmj)
            raise EAmmotune('classes : ce reglage n\'en a aucune ; en ajouter n\'est pas pris en charge (pas de chaine '
                            'de relocation a laquelle rattacher le tableau) : partir d\'une munition qui en a')
        if len(levels) <= len(old_levels):
            # sur place : memes octets, nombre d'elements mis a jour ; un element en trop reste en place, inutilise
            ed = AE.InstanceEditor(adf, bytes(out), i)
            header, _t, size, _a = ed.array(adf.table_instance_full_values[i].value[CLASSES])
            offset, _count = ed.array_state(header)
            out[start + offset:start + offset + len(levels) * size] = bytes(levels)
            struct.pack_into('<I', out, start + header + 8, len(levels))
        else:
            ed = AE.InstanceEditor(decode(bytes(out)), bytes(out), i)
            full = ed.adf.table_instance_full_values[i].value
            ed.replace(full[CLASSES], [bytes([v]) for v in levels])
            try:
                out = bytearray(ed.result())
            except AE.EAdfEcriture as e:
                raise EAmmotune('classes : {}'.format(e))
        lines.append('balistique : {} : {} -> {}'.format(CLASSES, ';'.join(map(str, old_levels)), ';'.join(map(str, levels))))
    result = bytes(out)
    verifier(data, result, wanted, levels)
    return result, lines


def _show(c: Champ, v) -> str:
    if c.genre == 'hash':
        return '0x{:08x}'.format(v)
    if c.genre == 'reel':
        return '{:.9g}'.format(v)
    return str(v)


def verifier(old: bytes, new: bytes, wanted: Dict[str, object], levels: Optional[List[int]]):
    """DECA relit le fichier produit : chaque champ vaut la nouvelle valeur (arrondie au format) s'il est demande,
    l'ancienne sinon ; classes comme demande ; autres instances identiques ; chaine de relocation complete, meme
    tete ; instance et types inchanges."""
    a, b = decode(old), decode(new)
    if len(a.table_instance) != len(b.table_instance) or len(a.table_typedef) != len(b.table_typedef):
        raise EAmmotune('verification : instances ou types differents')
    ov, ol, fields = lire(old)
    nv, nl, _f = lire(new)
    for c in fields:
        expect = wanted.get(c.chemin, ov[c.chemin])
        if c.genre == 'reel' and c.fmt == '<f':
            expect = _f32(expect)
        if nv[c.chemin] != expect and not (isinstance(expect, float) and math.isnan(expect)):
            raise EAmmotune('verification : {} vaut {} au lieu de {}'.format(c.chemin, nv[c.chemin], expect))
    if nl != (levels if levels is not None else ol):
        raise EAmmotune('verification : classes {} au lieu de {}'.format(nl, levels if levels is not None else ol))
    i = tuning_index(a)
    for j in range(len(a.table_instance)):
        if j != i and AE.normalize(AE.plain_values(a, j)) != AE.normalize(AE.plain_values(b, j)):
            raise EAmmotune('verification : instance {} modifiee'.format(j))
        if a.table_instance[j].name != b.table_instance[j].name or a.table_instance[j].type_hash != b.table_instance[j].type_hash:
            raise EAmmotune('verification : instance {} renommee ou retypee'.format(j))
    try:
        head = AE.verify_chain(a, old, i)
        AE.verify_chain(b, new, i, head[0] if head else None)
    except AE.EAdfEcriture as e:
        raise EAmmotune('verification : {}'.format(e))


# ---------------------------------------------------------------------------
# Dans une entite (.ee) : le .ammotunec est une entree incluse du SARC
# ---------------------------------------------------------------------------

def entree_reglage(sarc) -> int:
    """Index de l'unique entree incluse qui est un ADF AmmunitionTuning."""
    found = []
    for k, e in enumerate(sarc.entries):
        if e.is_link or not e.data or not (e.data[:4] == b' FDA' or e.data[5:9] == b' FDA'):
            continue
        try:
            if any(ins.type_hash == TUNING_TYPE for ins in decode(e.data).table_instance):
                found.append(k)
        except Exception:
            continue
    if len(found) != 1:
        raise EAmmotune('{} reglage(s) balistique(s) dans l\'entite, 1 attendu'.format(len(found)))
    return found[0]


def modifier_entite(ee: bytes, valeurs: Dict[str, str], classes=None) -> Tuple[bytes, List[str]]:
    """Modifie le .ammotunec inclus dans l'entite ; les autres entrees sont recopiees a l'identique (verifie)."""
    import sarc as S
    s = S.parse(ee)
    k = entree_reglage(s)
    new_tune, lines = modifier(s.entries[k].data, valeurs, classes)
    entries = [S.Entry(e.path, None, e.length) if e.is_link else
               S.Entry(e.path, new_tune if j == k else e.data, len(new_tune) if j == k else len(e.data))
               for j, e in enumerate(s.entries)]
    out, _offsets = S.build(S.Sarc(s.version, entries))
    t = S.parse(out)
    if [(e.path, e.is_link) for e in t.entries] != [(e.path, e.is_link) for e in s.entries]:
        raise EAmmotune('verification : entrees de l\'entite changees')
    for j, (x, y) in enumerate(zip(s.entries, t.entries)):
        if x.is_link:
            if x.length != y.length:
                raise EAmmotune('verification : lien {} modifie'.format(x.path))
        elif y.data != (new_tune if j == k else x.data):
            raise EAmmotune('verification : entree {} modifiee'.format(x.path))
    return out, ['{} : {}'.format(s.entries[k].path, line) for line in lines]


def lire_entite(ee: bytes) -> Tuple[str, Dict[str, object], List[int], List[Champ]]:
    import sarc as S
    s = S.parse(ee)
    k = entree_reglage(s)
    values, levels, fields = lire(s.entries[k].data)
    return s.entries[k].path, values, levels, fields


# ---------------------------------------------------------------------------
# Lecture en ligne de commande
# ---------------------------------------------------------------------------

def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(errors='replace')
    ap = argparse.ArgumentParser(description='Affiche le reglage balistique (.ammotunec) d\'une munition.')
    ap.add_argument('projet', help='projet DECA (dossier contenant db\\core.db)')
    ap.add_argument('objet', help='munition, ex : equipment_ammo_243_sp_01')
    ap.add_argument('--base', help='dossier au format dropzone (mod construit) ou lire d\'abord les fichiers')
    args = ap.parse_args(argv)
    import _projet
    import cloner_munition as C
    import deca.ff_rtpc_write as W
    vfs = _projet.open_project(args.projet)
    base = _projet.base_dir(args.base)
    sp_bytes, _src = C.load(vfs, base, C.SPAWN_AMMO)
    try:
        _, entry = C.find_item(W.rtpc_from_bytes(sp_bytes).root_node, args.objet, C.H_SPAWN_NAME)
    except C.EClone as e:
        raise SystemExit('ERREUR : spawn_ammunition_defs : {}'.format(e))
    entity = bytes(W.node_get_prop(entry, C.H_ENTITY).data).decode('utf-8').replace('\\', '/')
    ee, src = C.load(vfs, base, entity)
    try:
        path, values, levels, fields = lire_entite(ee)
    except EAmmotune as e:
        raise SystemExit('ERREUR : {}'.format(e))
    print('{} : {}'.format(args.objet, path))
    print('Origine : {}'.format(src))
    print('  {:<40} {}'.format(CLASSES, ';'.join(map(str, levels))))
    for c in fields:
        print('  {:<40} {:<14} ({})'.format(c.chemin, _show(c, values[c.chemin]), c.genre))
    return 0


if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    sys.exit(main())
