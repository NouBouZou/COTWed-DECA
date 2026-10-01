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
      une chaine egale au chemin d'une entree renommee (avec / ou \\, et pour un fichier compile, sans le "c"
      final : le .epe de la .243 SP designe "....ammotune" alors que l'entree s'appelle "....ammotunec")
      -> son nouveau chemin (tuning_file) ;
      un u32 egal au hash32 de l'un de ces textes -> le hash32 du nouveau texte ;
      chaque _object_id (objid) -> un identifiant nouveau, deterministe, qui garde les 16 bits de poids fort :
      deux entites chargees en meme temps ne doivent pas declarer les memes objets ;
  - recopie a l'identique les autres entrees incluses (ADF compris) ;
  - signale ce qui, dans le nouveau .ee, cite ENCORE la source (texte, hash32 du nom ou d'un ancien chemin) :
    DECA ne sait pas reecrire un ADF, donc le .ammotunec garde ses citations ; le rapport les montre au lieu
    de les taire.
"""
import struct
import sys
from pathlib import Path
from typing import Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sarc as S  # noqa: E402

import deca.ff_rtpc_write as W  # noqa: E402
from deca.ff_rtpc import k_type_str, k_type_u32, k_type_array_u32, k_type_objid  # noqa: E402

H_OBJECT_ID = 0xcfff8405  # _object_id


class EEntite(Exception):
    pass


def variants(path: str) -> List[str]:
    """Le chemin avec '/' et avec '\\' ; pour un fichier compile (extension finissant par c), aussi sans le c."""
    forms = [path]
    stem, dot, ext = path.rpartition('.')
    if dot and len(ext) > 1 and ext.endswith('c'):
        forms.append(stem + '.' + ext[:-1])
    out = []
    for f in forms:
        for v in (f.replace('\\', '/'), f.replace('/', '\\')):
            if v not in out:
                out.append(v)
    return out


def rename_path(path: str, old: str, new: str) -> str:
    return path.replace(old, new)


def text_map(source: str, new: str, renames: Dict[str, str]) -> Dict[bytes, bytes]:
    """Texte exact -> remplacement, chemins pris sous leurs deux separateurs (le jeu ecrit souvent '\\')."""
    m = {source.encode('utf-8'): new.encode('utf-8')}
    for old_p, new_p in renames.items():
        for a, b in zip(variants(old_p), variants(new_p)):
            m[a.encode('utf-8')] = b.encode('utf-8')
    return m


def new_object_id(old: int, new_name: str) -> int:
    """Identifiant d'objet nouveau et reproductible : 16 bits de poids fort gardes (0xffff dans COTW),
    48 bits tires du nouveau nom et de l'ancien identifiant."""
    import mmh3
    low = mmh3.hash128('{}:{:016x}'.format(new_name, old).encode('utf-8'), x64arch=True) & 0xFFFFFFFFFFFF
    return (old & 0xFFFF000000000000) | low


def patch_rtpc(data: bytes, mapping: Dict[bytes, bytes], new_name: str = None) -> Tuple[bytes, List[str]]:
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
            elif p.type == k_type_objid and new_name is not None and p.name_hash == H_OBJECT_ID:
                old_id = p.data
                p.data = new_object_id(old_id, new_name)
                changes.append('{} : _object_id 0x{:016x} -> 0x{:016x}'.format(where, old_id, p.data))
        for i, c in enumerate(node.child_table):
            walk(c, '{}.{}'.format(path, i))

    walk(tree.root_node, '0')
    out = W.rtpc_to_binary(tree)
    if not changes and out != data:
        raise EEntite('reecriture RTPC differente de l\'original sans modification : ecrivain non fidele')
    return out, changes


def residual_citations(payload: bytes, source: str, renames: Dict[str, str]) -> List[str]:
    """Citations de la source qui restent dans une entree : le nom en texte (sous-chaine), le hash32 du nom
    et le hash32 de chaque ancien chemin, en u32 little-endian a n'importe quel offset. Pour une entree de
    quelques Ko, une coincidence de hash est improbable (taille / 2^32)."""
    found = []
    n = payload.count(source.encode('utf-8'))
    if n:
        found.append('texte "{}" x{}'.format(source, n))
    texts = [source] + [v for p in renames for v in variants(p)]
    for t in dict.fromkeys(texts):
        h = W.name_hash(t.encode('utf-8'))
        n = payload.count(struct.pack('<I', h))
        if n:
            found.append('hash32 de "{}" (0x{:08x}) x{}'.format(t, h, n))
    return found


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
            payload, changes = patch_rtpc(payload, mapping, new)
            report += ['  {} :'.format(path)] + ['    ' + c for c in changes] if changes else ['  {} : RTPC sans changement'.format(path)]
        if path != e.path:
            report.append('  entree renommee : {} -> {}'.format(e.path, path))
        left = residual_citations(payload, source, renames)
        if left:
            report.append('  ATTENTION : {} cite encore la source ({}) : {}'.format(
                path, 'RTPC' if payload[:4] == W.RTPC_MAGIC else 'non modifiable par DECA', ', '.join(left)))
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
