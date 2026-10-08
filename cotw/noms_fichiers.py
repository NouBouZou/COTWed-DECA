"""
Noms de fichiers d'un projet DECA : etat du nommage et controle de type d'un nom candidat.

Partage par renommer.py (ajouter des noms a un projet deja scanne) et generer_noms.py (proposer des noms).

Pourquoi un controle de type : un nom candidat n'est verifie que par son hash32, soit 2^32 valeurs. Avec
10 000 fichiers sans nom, un candidat au hasard en touche un avec une probabilite de 10 000 / 2^32 ; sur des
millions de candidats, des faux positifs sont certains. Le type que DECA a detecte dans le fichier (magic) est
une seconde preuve independante : un ".wavc" doit tomber sur un son FSB5, pas sur un ADF. Les types attendus
pour une extension sont appris dans le projet lui-meme, sur les fichiers deja nommes.

Aucune fonction de ce module n'ecrit dans la base.
"""
import collections
from typing import Dict, Iterable, List, Optional, Set, Tuple

# Types que DECA attribue d'apres le nom, et non d'apres le contenu : avant d'etre nomme, un tel fichier n'a
# aucun type (process_vhash_final pose FTYPE_HMDDSC / FTYPE_ATX en meme temps que le nom).
TYPES_PAR_NOM = {'hmddsc', 'atx'}
# Un lien (entree de SARC ou de TAB sans contenu) peut porter n'importe quel nom.
TYPE_LIEN = 'symlink'
# Part minimale d'un type parmi les fichiers nommes d'une extension pour qu'il soit attendu. En dessous :
# collision de hash ou nom faux (ex. un seul ".meshc" sans type sur 21 774).
PART_MINIMALE = 0.01
# Nombre minimal de fichiers nommes pour qu'une extension soit apprise.
EFFECTIF_MINIMAL = 1

SANS_TYPE = None


def to_text(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode('utf-8', 'replace')
    return str(value)


def extension_complete(path: str) -> str:
    """Extension a partir du premier point du nom (".nl.mdic"), en minuscules ; '' sans extension.
    Meme regle que deca_audit.py."""
    name = path.replace('\\', '/').rsplit('/', 1)[-1]
    if '.' not in name:
        return ''
    return '.' + name.split('.', 1)[1].lower()


def extension_derniere(path: str) -> str:
    """Derniere extension (".mdic"), en minuscules ; '' sans extension. Regle de os.path.splitext."""
    name = path.replace('\\', '/').rsplit('/', 1)[-1]
    if '.' not in name.lstrip('.'):
        return ''
    return '.' + name.rsplit('.', 1)[1].lower()


class Etat:
    """
    Etat du nommage d'un projet, lu en une requete.

    - nommes : chemins virtuels presents dans la base ;
    - sans_nom : hash32 -> types des noeuds de ce hash, pour les hashes dont AUCUN noeud n'a de nom
      (definition de deca_audit.py, "hashes orphelins") ;
    - types_par_extension : extension complete -> Counter des types des noeuds nommes (un noeud par hash).
    """

    def __init__(self):
        self.nommes: Set[str] = set()
        self.sans_nom: Dict[int, Set[Optional[str]]] = {}
        self.types_par_extension: Dict[str, collections.Counter] = {}
        self.types_par_derniere: Dict[str, collections.Counter] = {}

    @classmethod
    def depuis_base(cls, vfs) -> 'Etat':
        rows = vfs.db_query_all('SELECT v_hash, v_path, file_type FROM core_nodes WHERE v_hash IS NOT NULL',
                                dbg='cotw_noms_etat')
        return cls.depuis_lignes((v_hash, to_text(v_path), to_text(file_type)) for v_hash, v_path, file_type in rows)

    @classmethod
    def depuis_lignes(cls, rows: Iterable[Tuple[int, Optional[str], Optional[str]]]) -> 'Etat':
        """rows : (v_hash, v_path ou None, file_type ou None), un par noeud."""
        etat = cls()
        nomme_par_hash: Dict[int, str] = {}
        types_par_hash: Dict[int, Set[Optional[str]]] = collections.defaultdict(set)
        type_nomme: Dict[Tuple[int, str], Optional[str]] = {}
        for v_hash, v_path, file_type in rows:
            v_hash &= 0xFFFFFFFF
            if v_path:
                nomme_par_hash[v_hash] = v_path
                key = (v_hash, v_path)
                # un fichier = un hash : on garde un type par (hash, nom), un vrai type plutot qu'un lien
                if key not in type_nomme or (type_nomme[key] in (None, TYPE_LIEN) and file_type not in (None, TYPE_LIEN)):
                    type_nomme[key] = file_type
            else:
                types_par_hash[v_hash].add(file_type)
        etat.nommes = set(nomme_par_hash.values()) | {p for (_h, p) in type_nomme}
        etat.sans_nom = {h: t for h, t in types_par_hash.items() if h not in nomme_par_hash}
        for (_h, path), file_type in type_nomme.items():
            if file_type == TYPE_LIEN:
                continue
            etat.types_par_extension.setdefault(extension_complete(path), collections.Counter())[file_type] += 1
            etat.types_par_derniere.setdefault(extension_derniere(path), collections.Counter())[file_type] += 1
        return etat


class ModeleTypes:
    """Types attendus pour une extension, appris sur les fichiers nommes, completes par hp.json (file_assoc)."""

    def __init__(self, etat: Etat, file_assoc: Optional[List[dict]] = None):
        self.attendus: Dict[str, Set[Optional[str]]] = {}
        self.attendus_derniere: Dict[str, Set[Optional[str]]] = {}
        for source, cible in ((etat.types_par_extension, self.attendus),
                              (etat.types_par_derniere, self.attendus_derniere)):
            for ext, counter in source.items():
                total = sum(counter.values())
                if total < EFFECTIF_MINIMAL:
                    continue
                types = {t for t, n in counter.items() if n / total >= PART_MINIMALE}
                if types & TYPES_PAR_NOM:
                    types.add(SANS_TYPE)
                cible[ext] = types
        # hp.json : {b'.hmddsc': ['atx', 'META-NO-TYPE'], ...}. Ajoute aux types appris : un type declare mais
        # rare (2 ".ddsc" en DDS sur 24 831) reste attendu malgre PART_MINIMALE.
        for groupe in file_assoc or []:
            for ext, types in groupe.items():
                ext = to_text(ext).lower()
                types = [types] if isinstance(types, str) else list(types)
                attendus = set()
                for t in types:
                    if t == 'META-NO-TYPE':
                        attendus.add(SANS_TYPE)
                    elif t and t != 'META-ANY-TYPE':
                        attendus.add(t)
                if attendus:
                    self.attendus.setdefault(ext, set()).update(attendus)

    def types_pour(self, path: str) -> Optional[Set[Optional[str]]]:
        """Types attendus pour ce nom, ou None si l'extension n'a jamais ete vue (pas de controle possible)."""
        types = self.attendus.get(extension_complete(path))
        if types is None:
            types = self.attendus_derniere.get(extension_derniere(path))
        return types

    def accepte(self, path: str, types_noeuds: Set[Optional[str]]) -> Optional[bool]:
        """True : le type d'au moins un noeud du hash est attendu pour ce nom. False : aucun ne l'est (faux
        positif probable). None : extension inconnue, pas de controle."""
        attendus = self.types_pour(path)
        if attendus is None:
            return None
        noeuds = {t for t in types_noeuds if t != TYPE_LIEN}
        if not noeuds:
            return True   # seulement des liens : aucun contenu a comparer
        return bool(noeuds & attendus)


def masque_deca(types: Optional[Set[Optional[str]]]) -> int:
    """Masque possible_file_types de DECA (ff_types.ftype_list) pour ces types ; 0 = tout type accepte.
    process_vhash_final ne donne le nom qu'aux noeuds dont le type est dans le masque."""
    from deca.ff_types import ftype_list, FTYPE_NO_TYPE, FTYPE_SYMLINK
    if types is None:
        return 0
    mask = ftype_list[FTYPE_SYMLINK]
    for t in types:
        key = FTYPE_NO_TYPE if t is None else t
        mask |= ftype_list.get(key, 0)
    return mask


def faux_positifs_attendus(candidats: int, cibles: int, largeur: int = 32) -> float:
    """Nombre de candidats au hasard qui tomberaient sur l'une des cibles : candidats x cibles / 2^largeur."""
    return candidats * cibles / float(1 << largeur)


def associes(name: str, file_assoc: List[dict]) -> List[Tuple[str, List[str]]]:
    """Noms associes a name, comme DECA (db_processor.find_vpath_by_assoc) : si la DERNIERE extension de name
    appartient a un groupe de hp.json, chaque autre extension du groupe donne un nom, avec ses types."""
    out = []
    dot = name.rfind('.')
    slash = name.rfind('/')
    if dot <= slash + 1:
        return out
    stem, ext = name[:dot], name[dot:]
    for groupe in file_assoc:
        cles = {to_text(k): v for k, v in groupe.items()}
        if ext in cles:
            for autre, types in cles.items():
                if autre != ext:
                    out.append((stem + autre, [types] if isinstance(types, str) else list(types)))
    return out
