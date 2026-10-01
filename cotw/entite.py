"""
Copie de l'entite (.ee, archive SARC) d'un objet pour un objet ajoute.

Constat en jeu (2026-10-01) : une munition ajoutee qui reutilise l'entite de sa source s'achete et s'equipe,
mais l'arme charge et recharge les munitions de la SOURCE. Le .epe de l'entite (RTPC) porte item_id = nom de
l'objet : l'arme passe par l'entite, donc par l'objet source.

La copie :
  - renomme chaque entree INCLUSE dont le chemin contient le nom de la source (le .epe, le .ammotunec...) ;
    deux archives qui contiennent un meme chemin risquent de partager la ressource : un .epe modifie mais
    garde sous son nom pourrait etre ignore ;
  - garde les liens (entrees qui designent un fichier ailleurs dans le jeu : modeles, textures...) ;
  - dans chaque RTPC inclus, remplace :
      une chaine egale au nom de la source -> le nouveau nom (item_id) ;
      une chaine egale au chemin d'une entree renommee (avec / ou \\) -> son nouveau chemin (tuning_file) ;
      un u32 egal au hash32 de l'un de ces textes -> le hash32 du nouveau texte ;
  - recopie a l'identique les autres entrees incluses (ADF compris).
"""
import sys
from pathlib import Path
from typing import Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sarc as S  # noqa: E402

import deca.ff_rtpc_write as W  # noqa: E402
from deca.ff_rtpc import k_type_str, k_type_u32, k_type_array_u32  # noqa: E402


class EEntite(Exception):
    pass


def variants(path: str) -> List[str]:
    a, b = path.replace('\\', '/'), path.replace('/', '\\')
    return [a] if a == b else [a, b]


def rename_path(path: str, old: str, new: str) -> str:
    return path.replace(old, new)


def text_map(source: str, new: str, renames: Dict[str, str]) -> Dict[bytes, bytes]:
    """Texte exact -> remplacement, chemins pris sous leurs deux separateurs (le jeu ecrit souvent '\\')."""
    m = {source.encode('utf-8'): new.encode('utf-8')}
    for old_p, new_p in renames.items():
        for a, b in zip(variants(old_p), variants(new_p)):
            m[a.encode('utf-8')] = b.encode('utf-8')
    return m


def patch_rtpc(data: bytes, mapping: Dict[bytes, bytes]) -> Tuple[bytes, List[str]]:
    tree = W.rtpc_from_bytes(data)
    hashes = {W.name_hash(k): W.name_hash(v) for k, v in mapping.items()}
    changes = []

    def walk(node, path):
        for p in node.prop_table:
            where = '{} propriete 0x{:08x}'.format(path, p.name_hash)
            if p.type == k_type_str and bytes(p.data) in mapping:
                new = mapping[bytes(p.data)]
                changes.append('{} : "{}" -> "{}"'.format(where, bytes(p.data).decode('utf-8', 'replace'), new.decode('utf-8')))
                p.data = new
            elif p.type == k_type_u32 and p.data in hashes:
                changes.append('{} : u32 0x{:08x} -> 0x{:08x} (hash)'.format(where, p.data, hashes[p.data]))
                p.data = hashes[p.data]
            elif p.type == k_type_array_u32 and any(v in hashes for v in p.data):
                p.data = [hashes.get(v, v) for v in p.data]
                changes.append('{} : tableau u32, hashes remplaces'.format(where))
        for i, c in enumerate(node.child_table):
            walk(c, '{}.{}'.format(path, i))

    walk(tree.root_node, '0')
    out = W.rtpc_to_binary(tree)
    if not changes and out != data:
        raise EEntite('reecriture RTPC differente de l\'original sans modification : ecrivain non fidele')
    return out, changes


def clone_entity(data: bytes, source: str, new: str) -> Tuple[bytes, Dict[str, str], List[str]]:
    """(octets du nouveau .ee, renommages d'entrees, rapport)."""
    if not new or source == new:
        raise EEntite('nouveau nom vide ou identique a la source')
    s = S.parse(data)
    renames = {}
    for e in s.entries:
        if not e.is_link and source in e.path:
            renames[e.path] = rename_path(e.path, source, new)
    if not renames:
        raise EEntite('aucune entree incluse ne porte le nom "{}" : structure d\'entite inattendue'.format(source))
    mapping = text_map(source, new, renames)
    report = ['entite : SARC v{}, {} entrees dont {} liens'.format(
        s.version, len(s.entries), sum(1 for e in s.entries if e.is_link))]
    entries = []
    for e in s.entries:
        if e.is_link:
            entries.append(S.Entry(e.path, None, e.length))
            continue
        path = renames.get(e.path, e.path)
        payload = e.data
        if payload[:4] == W.RTPC_MAGIC:
            payload, changes = patch_rtpc(payload, mapping)
            report += ['  {} :'.format(path)] + ['    ' + c for c in changes] if changes else ['  {} : RTPC sans changement'.format(path)]
        if path != e.path:
            report.append('  entree renommee : {} -> {}'.format(e.path, path))
        entries.append(S.Entry(path, payload, len(payload)))
    out, _offsets = S.build(S.Sarc(s.version, entries))
    verify(data, out, renames)
    return out, renames, report


def verify(original: bytes, produced: bytes, renames: Dict[str, str]):
    """Relit le nouveau .ee : memes entrees dans le meme ordre, liens identiques, entrees non RTPC recopiees."""
    a, b = S.parse(original), S.parse(produced)
    if len(a.entries) != len(b.entries) or a.version != b.version:
        raise EEntite('verification : nombre d\'entrees ou version differents')
    for x, y in zip(a.entries, b.entries):
        if renames.get(x.path, x.path) != y.path or x.is_link != y.is_link:
            raise EEntite('verification : entree {} devenue {}'.format(x.path, y.path))
        if x.is_link and x.length != y.length:
            raise EEntite('verification : taille du lien {} modifiee'.format(x.path))
        if not x.is_link and x.data[:4] != W.RTPC_MAGIC and x.data != y.data:
            raise EEntite('verification : contenu de {} modifie'.format(x.path))
