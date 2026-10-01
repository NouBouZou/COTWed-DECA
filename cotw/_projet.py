"""
Acces en lecture a un projet DECA, partage par les outils de cotw/.

Pourquoi un module commun : rtpc_roundtrip.py, rtpc_dump.py et deca_refs.py ouvrent tous le meme projet
de la meme facon (VfsDatabase sans lancer de traitement, journal muet) et lisent des fichiers du jeu par
chemin virtuel ou par hash.

Aucune fonction ici n'ecrit dans la base. Lire un fichier compresse peut ajouter un fichier dans le
cache de DECA (work\\hp\\__CACHE__), comme quand on le parcourt dans l'interface.
"""
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

if not sys.platform.startswith('win'):
    # Sous Linux, deca importe zugbruecke (pont vers les DLL Windows, qui exige Wine) : inutile pour COTW.
    import ctypes
    sys.modules.setdefault('zugbruecke.ctypes', ctypes)


class SilentLogger:
    """Journal muet : on n'ecrit pas dans le journal du projet DECA."""
    def log(self, *a, **k): pass
    trace = debug = warning = error = log


def to_text(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode('utf-8', 'replace')
    return str(value)


def is_project_dir(path: Path) -> bool:
    return (path / 'db' / 'core.db').is_file() and (path / 'project.json').is_file()


def open_project(project_dir):
    """Ouvre un projet DECA existant sans lancer de scan. Renvoie un deca.db_core.VfsDatabase."""
    from deca.db_core import VfsDatabase
    project_dir = Path(project_dir).expanduser().resolve()
    if not is_project_dir(project_dir):
        raise SystemExit('ERREUR : {} n\'est pas un projet DECA (db\\core.db ou project.json absent)'.format(project_dir))
    working_dir = str(project_dir).replace('\\', '/') + '/'
    return VfsDatabase(str(project_dir / 'project.json').replace('\\', '/'), working_dir, SilentLogger())


def nodes_by_path(vfs, v_path: str):
    """Noeuds portant ce chemin virtuel (un meme fichier peut exister dans plusieurs archives)."""
    return vfs.nodes_where_match(v_path=v_path.replace('\\', '/'))


def read_node(vfs, node) -> bytes:
    with vfs.file_obj_from(node) as f:
        return f.read(node.size_u)


def read_by_path(vfs, v_path: str) -> bytes:
    nodes = [n for n in nodes_by_path(vfs, v_path) if n.offset is not None]
    if not nodes:
        raise SystemExit('ERREUR : aucun fichier "{}" dans le projet (chemin virtuel inconnu ou sans contenu)'.format(v_path))
    return read_node(vfs, nodes[0])


DICTIONARY_PATH = Path(__file__).resolve().parent / 'dictionnaires' / 'hp_noms.tsv'
_dictionary_cache: Dict[Path, Dict[Tuple[int, int], List[str]]] = {}


def load_dictionary(path: Optional[Path] = None) -> Dict[Tuple[int, int], List[str]]:
    """Noms du dictionnaire du fork : (largeur en bits, hash) -> noms. Fichier absent = dictionnaire vide.

    Format (ecrit par resoudre_hashes.py) : une ligne par nom, colonnes separees par des tabulations :
    hash en hexadecimal, largeur (32, 48 ou 64), nom, puis des colonnes de provenance ignorees ici.
    """
    path = Path(path) if path is not None else DICTIONARY_PATH
    if path in _dictionary_cache:
        return _dictionary_cache[path]
    entries: Dict[Tuple[int, int], List[str]] = {}
    if path.is_file():
        for line in path.read_text(encoding='utf-8').splitlines():
            if not line.strip() or line.startswith('#'):
                continue
            cols = line.split('\t')
            if len(cols) < 3:
                continue
            key = (int(cols[1]), int(cols[0], 16))
            if cols[2] not in entries.setdefault(key, []):
                entries[key].append(cols[2])
    _dictionary_cache[path] = entries
    return entries


def forget_dictionary_cache():
    _dictionary_cache.clear()


def resolve_hash32(vfs, hashes: Iterable[int], batch: int = 500, use_dictionary: bool = True) -> Dict[int, List[str]]:
    """Chaines connues pour chaque hash32 demande : table core_strings de DECA, plus le dictionnaire du fork
    (cotw/dictionnaires/hp_noms.tsv) sauf si use_dictionary=False."""
    wanted = sorted({h & 0xFFFFFFFF for h in hashes if h is not None})
    found: Dict[int, List[str]] = {}
    if use_dictionary:
        dictionary = load_dictionary()
        for h in wanted:
            for name in dictionary.get((32, h), []):
                found.setdefault(h, []).append(name)
    for i in range(0, len(wanted), batch):
        chunk = wanted[i:i + batch]
        rows = vfs.db_query_all(
            'SELECT hash32, string FROM core_strings WHERE hash32 IN ({})'.format(','.join('?' * len(chunk))),
            chunk, dbg='cotw_resolve_hash32')
        for h, s in rows:
            found.setdefault(h, []).append(to_text(s))
    for h in found:
        found[h] = sorted(set(found[h]))
    return found


def node_label(node) -> str:
    """Chemin virtuel du noeud, ou son hash s'il n'a pas de nom."""
    name = to_text(node.v_path)
    if name:
        return name
    if node.v_hash is not None:
        return '(sans nom) {:08x}'.format(node.v_hash & 0xFFFFFFFF)
    return to_text(node.p_path) or '(noeud {})'.format(node.uid)
