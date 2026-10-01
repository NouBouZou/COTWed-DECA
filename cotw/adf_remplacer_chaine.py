#!/usr/bin/env python3
"""
adf_remplacer_chaine.py -- remplace une chaine dans un fichier ADF, sur place, sans changer sa taille.

Pourquoi : DECA ne sait pas reecrire un ADF. Pour un essai, on peut pourtant reaffecter une ligne existante
d'une feuille (equipment_stats_ui.bin, XLSBook) a un autre objet en renommant la chaine qui la designe.
Le nouveau texte doit etre au plus aussi long que l'ancien : il est ecrit a la meme place, complete par des
octets nuls, et le champ longueur de chaque en-tete de chaine concerne est mis a jour. Aucun offset ne bouge.

Une chaine ADF (type String) est un en-tete de 8 octets dans la structure : u32 offset (relatif au debut de
l'instance), puis un u32 que DECA appelle "longueur" ; le texte, termine par un octet nul, est a cet offset.
Dans les XLSBook de COTW ce second u32 vaut 8 quelle que soit la chaine (releve sur equipment_stats_ui.bin) :
ce n'est pas une longueur. On ne le met a jour que s'il vaut la longueur de l'ancien texte (avec ou sans
l'octet nul) ; sinon on le laisse tel quel. Controles avant ecriture :
  - chaque en-tete pointe bien sur le texte attendu ;
  - aucune autre chaine ne partage ces octets (une chaine qui en serait le suffixe serait corrompue) ;
  - apres ecriture, DECA relit le fichier et seules les chaines remplacees ont change.

Usage, depuis la racine du depot, environnement virtuel active, DECA ferme :
    python cotw\\adf_remplacer_chaine.py D:\\dev\\work\\hp settings/hp_settings/equipment_stats_ui.bin ^
        --remplacer equipment_ammo_762x54r_fmj_01=equipment_ammo_243_test_01 --sortie D:\\dev\\mods\\test4
"""
import argparse
import struct
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _projet  # noqa: E402
import citations as CI  # noqa: E402
import cloner_munition as C  # noqa: E402
import resoudre_hashes as RH  # noqa: E402


class EPatch(Exception):
    pass


def adf_base(data: bytes) -> int:
    """Position de l'en-tete ' FDA' : 0, ou 5 pour les ADF precedes d'un petit en-tete (adf5)."""
    if data[:4] == b' FDA':
        return 0
    if data[5:9] == b' FDA':
        return 5
    raise EPatch('pas un fichier ADF (en-tete " FDA" absent)')


def decode(reader: RH.FileReader, data: bytes):
    base = adf_base(data)
    adf = reader.adf_db._load_adf(data[base:])
    if adf is None:
        raise EPatch('ADF illisible par DECA (types manquants ?)')
    return adf, base


def string_leaves(adf) -> List[CI.Leaf]:
    return [l for l in CI.adf_leaves(adf) if l.kind == 'chaine' and l.header is not None]


def plan_replacements(adf, data: bytes, base: int, pairs: List[Tuple[str, str]]) -> List[Tuple[int, int, bytes, bytes, int, bool]]:
    """Pour chaque en-tete a modifier : (en-tete, texte, ancien, nouveau, longueur actuelle, longueur avec nul)."""
    leaves = string_leaves(adf)
    instance_offsets = {ins.offset for ins in adf.table_instance}
    plans = []
    for old, new in pairs:
        old_b, new_b = old.encode('utf-8'), new.encode('utf-8')
        if not new_b or b'\0' in new_b:
            raise EPatch('nouveau texte vide ou contenant un octet nul : "{}"'.format(new))
        if len(new_b) > len(old_b):
            raise EPatch('"{}" ({} octets) est plus long que "{}" ({} octets) : un remplacement sur place est '
                         'impossible'.format(new, len(new_b), old, len(old_b)))
        targets = [l for l in leaves if bytes(l.value) == old_b]
        if not targets:
            raise EPatch('aucune chaine "{}" dans le fichier'.format(old))
        spans = {l.offset for l in targets}
        for other in leaves:
            if other.offset in spans or bytes(other.value) == old_b:
                continue
            o_start, o_end = other.offset, other.offset + len(bytes(other.value)) + 1
            for start in spans:
                if o_start < start + len(old_b) + 1 and start < o_end:
                    raise EPatch('la chaine "{}" partage des octets avec "{}" ({}) : remplacement refuse'.format(
                        old, bytes(other.value).decode('utf-8', 'replace'), other.path))
        for l in targets:
            text_at = base + l.offset
            if data[text_at:text_at + len(old_b) + 1] != old_b + b'\0':
                raise EPatch('{} : le texte attendu n\'est pas a l\'offset 0x{:x}'.format(l.path, l.offset))
            rel, length = struct.unpack_from('<II', data, base + l.header)
            if not any(io + rel == l.offset for io in instance_offsets):
                raise EPatch('{} : l\'en-tete (offset relatif 0x{:x}) ne pointe pas sur le texte'.format(l.path, rel))
            # longueur : None si le second u32 n'est pas une longueur (il est alors laisse intact)
            if length == len(old_b):
                new_length = len(new_b)
            elif length == len(old_b) + 1:
                new_length = len(new_b) + 1
            else:
                new_length = None
            plans.append((base + l.header, text_at, old_b, new_b, length, new_length))
    return plans


def apply(data: bytes, plans) -> bytes:
    out = bytearray(data)
    for header, text_at, old_b, new_b, _length, new_length in plans:
        out[text_at:text_at + len(old_b)] = new_b + b'\0' * (len(old_b) - len(new_b))
        if new_length is not None:
            struct.pack_into('<I', out, header + 4, new_length)
    return bytes(out)


def verify(reader, original: bytes, patched: bytes, pairs: List[Tuple[str, str]]):
    """Relit les deux versions avec DECA : memes feuilles, seules les chaines remplacees different."""
    a, _ = decode(reader, original)
    b, _ = decode(reader, patched)
    la, lb = list(CI.adf_leaves(a)), list(CI.adf_leaves(b))
    if len(la) != len(lb):
        raise EPatch('verification : {} valeurs avant, {} apres'.format(len(la), len(lb)))
    mapping = {o.encode('utf-8'): n.encode('utf-8') for o, n in pairs}
    changed = 0
    for x, y in zip(la, lb):
        if x.path != y.path or x.kind != y.kind or x.offset != y.offset:
            raise EPatch('verification : structure differente en {}'.format(x.path))
        if x.value != y.value:
            if x.kind != 'chaine' or mapping.get(bytes(x.value)) != bytes(y.value):
                raise EPatch('verification : valeur inattendue en {} : {!r} -> {!r}'.format(x.path, x.value, y.value))
            changed += 1
    return changed


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(errors='replace')
    ap = argparse.ArgumentParser(description='Remplace une chaine dans un ADF, sur place.')
    ap.add_argument('projet', help='projet DECA (dossier contenant db\\core.db)')
    ap.add_argument('fichier', help='chemin virtuel, ex : settings/hp_settings/equipment_stats_ui.bin')
    ap.add_argument('--remplacer', action='append', required=True, metavar='ANCIEN=NOUVEAU',
                    help='chaine a remplacer (option repetable) ; NOUVEAU doit etre au plus aussi long que ANCIEN')
    ap.add_argument('--sortie', required=True, help='dossier de sortie ; le fichier est ecrit dans SORTIE\\dropzone')
    ap.add_argument('--base', help='dossier au format dropzone dont partir, s\'il contient deja ce fichier')
    ap.add_argument('--ecraser', action='store_true', help='remplacer le fichier s\'il existe deja dans la sortie')
    args = ap.parse_args(argv)

    pairs = []
    for spec in args.remplacer:
        old, sep, new = spec.partition('=')
        if not sep:
            raise SystemExit('ERREUR : attendu ANCIEN=NOUVEAU, recu "{}"'.format(spec))
        pairs.append((old.strip(), new.strip()))
    target = Path(args.sortie).expanduser().resolve() / 'dropzone' / Path(args.fichier)
    if target.exists() and not args.ecraser:
        raise SystemExit('ERREUR : {} existe deja (option --ecraser pour le remplacer)'.format(target))

    vfs = _projet.open_project(args.projet)
    base = Path(args.base).expanduser().resolve() if args.base else None
    data, src = C.load(vfs, base, args.fichier)
    reader = RH.FileReader(vfs)
    try:
        adf, offset = decode(reader, data)
        plans = plan_replacements(adf, data, offset, pairs)
        patched = apply(data, plans)
        changed = verify(reader, data, patched, pairs)
    except EPatch as e:
        raise SystemExit('ERREUR : {}'.format(e))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(patched)
    lines = ['Origine : {}'.format(src)]
    for header, text_at, old_b, new_b, length, new_length in plans:
        lines.append('  "{}" -> "{}" : texte @0x{:x}, en-tete @0x{:x}, {}'.format(
            old_b.decode(), new_b.decode(), text_at, header,
            'longueur {} -> {}'.format(length, new_length) if new_length is not None
            else 'second champ de l\'en-tete laisse a {} (pas une longueur)'.format(length)))
    lines += ['Verification : relu par DECA, {} valeur(s) changee(s), rien d\'autre ; taille identique ({} octets).'.format(
        changed, len(patched)), 'Ecrit : {}'.format(target)]
    print('\n'.join(lines))
    return 0


if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    sys.exit(main())
