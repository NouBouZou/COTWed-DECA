#!/usr/bin/env python3
"""
deca_audit.py -- Audit en lecture seule d'un projet DECA (theHunter: Call of the Wild).

Mesure les deux lacunes principales de DECA sur COTW :
  1. les fichiers sans nom  (noeuds avec un hash mais sans chemin : v_path IS NULL)
  2. les formats non lus    (noeuds sans type reconnu, types reconnus mais jamais
                             explores, types ADF dont la definition manque)
et, par extension, ce que COTWed DECA sait afficher (visionneuses de l'interface), pour choisir les
formats a etudier ensuite.

Le script n'utilise que la bibliotheque standard de Python (aucune dependance DECA)
et ouvre la base en mode lecture seule : il ne peut pas modifier le projet.

Usage (invite de commandes, depuis C:\\dev\\deca, environnement virtuel active) :
    python cotw\\deca_audit.py C:\\dev\\work\\hp
    python cotw\\deca_audit.py C:\\dev\\work\\hp --out C:\\dev\\audits\\00-baseline
    python cotw\\deca_audit.py C:\\dev\\work\\hp --out C:\\dev\\audits\\01-essai --compare C:\\dev\\audits\\00-baseline\\metrics.json

Sorties (dans --out ; par defaut <dossier work>\\audits\\<projet>_<date-heure>, donc HORS du
dossier du projet, pour survivre a sa suppression quand on le recree) :
    rapport.md                 synthese lisible
    metrics.json               chiffres cles, pour comparer deux executions (--compare)
    orphelins_par_archive.csv  taux de resolution des noms par archive .tab
    hashes_orphelins.csv       liste complete des hashes sans nom (cible pour la resolution)
    types_inconnus_magic.csv   fichiers sans type, regroupes par leurs 4 premiers octets
    types_inconnus_ext.csv     fichiers nommes mais sans type, regroupes par extension
    types_non_explores.csv     types reconnus que DECA ne parcourt jamais
    adf_types_manquants.csv    types ADF references mais non definis
    couverture_extensions.csv  par extension : fichiers lisibles par une visionneuse de COTWed DECA, verdict
    extraire_echantillons.bat  commandes extraire.py : quelques fichiers des familles non lues (phase C)
    log_resume.txt             erreurs trouvees dans le journal DECA (si present)

Toutes les constantes ci-dessous sont recopiees du code de DECA (branche develop,
commit 9231177, 2025-12-10) ; la source exacte est indiquee a cote de chacune.
"""

import argparse
import collections
import csv
import json
import os
import posixpath
import re
import sqlite3
import struct
import sys
from array import array
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Constantes DECA
# ---------------------------------------------------------------------------

# python/deca/deca/db_types.py
FLAG_V_HASH_TYPE_MASK = 0x3 << 16
FLAG_V_HASH_TYPE_4 = 0x1 << 16
FLAG_V_HASH_TYPE_6 = 0x2 << 16
FLAG_V_HASH_TYPE_8 = 0x3 << 16
FLAG_TEMPORARY_FILE = 1 << 20
FLAG_PROCESSED_FILE_TYPE = 1 << 23

# python/deca/deca/ff_types.py
FTYPE_ARC = 'arc'
FTYPE_TAB = 'tab'
FTYPE_NOT_HANDLED = 'NOT'

# Types pour lesquels db_processor.py (VfsProcessor.process, liste inner_loop)
# possede une etape d'exploration qui pose FLAG_PROCESSED_FILE_TYPE.
# ATTENTION : DECA n'explore qu'UN noeud par v_hash (db_processor.src_indexes_process) ; les copies
# d'un meme fichier presentes dans d'autres archives ne recoivent jamais le flag. Un fichier n'est donc
# "non explore" que si AUCUN noeud portant son v_hash n'a le flag.
TYPES_EXPLORES = {
    'exe', 'arc', 'tab', 'gt0c', 'garc', 'sarc', 'gdc',
    'adf', 'adfb', 'adf0', 'adf5', 'rtpc', 'gfx', 'txt', 'symlink',
}

# python/deca/deca/db_types.py, ajoute par COTWed DECA (2026-10-05) : noeud dont le traitement a leve une
# exception pendant le scan (ecritures annulees, une ligne "loop_over_uid_wrapper: failed for" au journal).
FLAG_PROCESSING_FAILED = 1 << 25

# Visionneuses de COTWed DECA (python/deca_gui/deca_gui/dataviewwidget.py, VIEWER_TABS) : type DECA -> onglet qui
# montre le contenu structure. Hors de cette table, l'interface n'offre que Info et Raw/Hex (les octets).
# test_audit_couverture.py verifie que cette table et celle de l'interface concordent.
LECTEURS = {
    'txt': 'Text',
    'sarc': 'SARC',
    'avtx': 'Image', 'atx': 'Image', 'hmddsc': 'Image', 'dds': 'Image', 'bmp': 'Image',
    'adf': 'ADF', 'adf0': 'ADF', 'adf5': 'ADF',       # + Strings / Sheet selon le type d'instance
    'adfb': 'ADF/GDC',
    'rtpc': 'RTPC', 'rtpci': 'RTPC',                   # rtpci : RTPC en ligne (world.bin, .smodc), 2026-10-09
    'obc': 'OBC',                                      # versions 4 (DECA) et 1 (COTW, 2026-10-09)
    'rawc': 'Image',                                   # tableaux bruts du terrain (disposition devinee)
    'fmodguids': 'Structure', 'ttf': 'Structure',      # GUID d'evenements FMOD, police TrueType
    'btc': 'Structure',                                # arbres de comportement : en-tete, parametres (code opaque)
    'riff': 'Structure',                               # banques FMOD : chunks ; noms FMOD de la banque de chaines
    'h2014': 'Havok',                                  # packfiles Havok 2014 : objets et membres (2026-10-09)
    'pfx': 'Havok',                                    # .pfxc / .pfs : en-tete PFX + packfile Havok (2026-10-10)
    'FSB5c': 'Structure',                              # .wavc : FSB5 Vorbis, export .ogg (2026-10-10)
    'mdi': 'Structure',                                # .nl.mdic / .fl.mdic : instances de modeles (2026-10-10)
    'gfx': 'Structure',                                # Scaleform GFx : en-tete, balises, images externes
    'bik': 'Structure',                                # Bink 1 : en-tete et index des images (pas de decodage)
    'hikcc': 'Structure',                              # HumanIK : noeuds utilises et pose de reference (2026-10-10)
    'ivf': 'Structure', 'webm': 'Structure',           # videos VP9 : en-tete, images / arbre EBML (2026-10-11)
}
# Niveaux de mip d'une texture (python/deca/deca/ff_avtx.py, image_load) : la visionneuse ouvre <nom>.ddsc et lui
# ajoute <nom>.hmddsc et <nom>.atx1..15. Sans <nom>.ddsc dans le jeu (775 .hmddsc de COTW), elle devine depuis le
# 2026-10-10 le format et la taille (deca/ff_bc_raw.py) : statut mip_orphelin, compte comme lu.
TYPES_MIP = {'hmddsc', 'atx'}


def taille_chaine_bc(size):
    """Vrai si size est la taille d'une chaine de mips compressee par blocs (copie de deca.ff_bc_raw.layouts, sans
    dependance : test_audit_couverture.py verifie l'accord). Un fichier sans type de cette taille s'ouvre dans
    l'onglet Image, disposition devinee (dataviewwidget.is_guessed_texture) : statut texture_devinee."""
    if size is None or size < 4096:
        return False
    for bpb in (8, 16):
        for lw in range(2, 14):
            for asp in range(-2, 3):
                w = 1 << lw
                h = w >> asp if asp > 0 else w << -asp
                if h < 4 or h > 8192:
                    continue
                total = 0
                for k in range(14):
                    total += max(1, ((w >> k) + 3) // 4) * max(1, ((h >> k) + 3) // 4) * bpb
                    if total == size:
                        return True
                    if total >= size:
                        break
    return False
# Conteneurs explores par DECA : leur contenu devient des noeuds de l'arbre (pas de visionneuse propre).
CONTENEURS = {'gt0c', 'garc', 'gdc'}

# Statut d'un fichier pour la couverture de lecture, du meilleur au pire : un fichier present en plusieurs
# copies (archives) prend le meilleur statut de ses copies.
STATUTS = ('lisible', 'conteneur', 'mip_orphelin', 'texture_devinee', 'adf_sans_type', 'echec_scan',
           'type_sans_visionneuse', 'type_inconnu', 'lien')
# mip_orphelin et texture_devinee : lus avec une disposition devinee (2026-10-10)
STATUTS_LUS = ('lisible', 'conteneur', 'mip_orphelin', 'texture_devinee')
SEUIL_LU = 99.0        # % de fichiers lisibles au-dessus duquel une famille est "lue"

# db_processor.py : PRAGMA user_version = 2 est ecrit a la toute fin du traitement.
SCAN_TERMINE_VERSION = 2

# Motifs du journal DECA (db_commands.py) utiles pour l'audit.
# Note : quand un processus de travail echoue, DECA ecrit "process_N: EXCEPTION: [...]" dans le journal,
# puis leve Exception('Manager: PROCESSING FAILED') dans le processus principal. Ce second message
# n'apparait qu'en console, jamais dans le journal (db_commands.MultiProcessControl.do_map).
LOG_MOTIFS = {
    'exception_processus': re.compile(r'(process_\d+): EXCEPTION: \[<class \'([\w.]+)\'>, (.*)'),
    'echec_traitement_noeud': re.compile(r'loop_over_uid_wrapper: failed for: (.*)'),
    'type_adf_manquant': re.compile(r'Missing ADF Type ([0-9a-fA-F]+) in (.*)'),
    'compression_inconnue': re.compile(r'Unknown Compression Type (\S+) in (.*)'),
    'traitement_abandonne': re.compile(r'Manager: PROCESSING FAILED'),
    'scan_complet': re.compile(r'PROCESSING: COMPLETE'),
}

# Cadres de pile dans la representation Python d'une trace, telle que DECA l'ecrit dans le journal.
RX_TRACE_FRAME = re.compile(r'File \\?"([^"]+?)\\?", line (\d+), in (\w+)')


# ---------------------------------------------------------------------------
# Utilitaires de formatage
# ---------------------------------------------------------------------------

def fmt_hash(v_hash, flags):
    """Formate un hash de chemin selon sa largeur declaree dans les flags du noeud."""
    if v_hash is None:
        return ''
    width = {
        FLAG_V_HASH_TYPE_4: 8,
        FLAG_V_HASH_TYPE_6: 12,
        FLAG_V_HASH_TYPE_8: 16,
    }.get((flags or 0) & FLAG_V_HASH_TYPE_MASK, 8)
    return '{:0{w}x}'.format(v_hash & ((1 << (width * 4)) - 1), w=width)


def magic_bytes(magic):
    """
    DECA stocke les 4 premiers octets du fichier sous forme d'entier :
    struct.unpack('I', data[0:4]) dans ff_determine.py (petit-boutiste sur x86/x64).
    Le champ vaut NULL si le fichier fait moins de 20 octets.
    """
    if magic is None:
        return None
    return struct.pack('<I', magic & 0xFFFFFFFF)


def magic_label(magic):
    """Renvoie (hex, ascii) lisibles pour un champ magic, ou un libelle explicite si NULL."""
    b = magic_bytes(magic)
    if b is None:
        return '(fichier < 20 octets)', ''
    hx = ' '.join('{:02X}'.format(x) for x in b)
    asc = ''.join(chr(x) if 0x20 <= x < 0x7F else '.' for x in b)
    return hx, asc


def magic_short(magic):
    """Magic court pour un tableau : le texte s'il est lisible (RTPC, ' FDA', DDS ), l'hexadecimal sinon."""
    hx, asc = magic_label(magic)
    if asc and sum(1 for c in asc if c.isalnum()) >= 3 and not set(asc) & set('|`'):
        return asc.strip()
    return hx


def to_text(value):
    """v_path et p_path sont ecrits en bytes par DECA (colonne TEXT, stockage BLOB)."""
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode('utf-8', errors='replace')
    return str(value)


def extension_of(path):
    """Extension complete a partir du premier point du nom de fichier (ex: .nl.mdic)."""
    name = path.replace('\\', '/').rsplit('/', 1)[-1]
    if '.' not in name:
        return '(sans extension)'
    return '.' + name.split('.', 1)[1].lower()


def archive_label(p_path):
    """Chemin de l'archive relatif a archives_win64 quand c'est possible."""
    if p_path is None:
        return '(inconnue)'
    p = p_path.replace('\\', '/')
    marker = 'archives_win64/'
    idx = p.lower().find(marker)
    return p[idx + len(marker):] if idx >= 0 else p


def pct(part, total):
    return 0.0 if total == 0 else 100.0 * part / total


def human_bytes(n):
    for unit in ('o', 'Ko', 'Mo', 'Go'):
        if abs(n) < 1024 or unit == 'Go':
            return '{:.1f} {}'.format(n, unit) if unit != 'o' else '{} o'.format(n)
        n /= 1024.0
    return str(n)


# ---------------------------------------------------------------------------
# Localisation des fichiers du projet
# ---------------------------------------------------------------------------

def resolve_paths(target):
    """
    Accepte le dossier du projet (work/hp), le project.json, ou directement core.db.
    Renvoie (chemin_core_db, dossier_projet).
    Emplacement defini par VfsDatabase.__init__ (db_core.py) : <projet>/db/core.db
    """
    p = Path(target).expanduser().resolve()
    if p.is_file() and p.suffix.lower() == '.db':
        return p, p.parent.parent
    if p.is_file() and p.name.lower() == 'project.json':
        p = p.parent
    if p.is_dir():
        db = p / 'db' / 'core.db'
        if db.is_file():
            return db, p
        raise SystemExit('ERREUR : aucune base trouvee a {}'.format(db))
    raise SystemExit('ERREUR : chemin introuvable : {}'.format(p))


def find_logs(project_dir):
    """
    DECA ecrit son journal a working_dir + 'log.txt' (util.py, Logger.log_base).
    A la creation d'un projet, working_dir n'a pas de separateur final
    (db_processor.py, vfs_structure_new) : le journal atterrit alors dans
    work/hplog.txt au lieu de work/hp/log.txt. On cherche les deux.
    """
    candidates = [
        project_dir / 'log.txt',
        project_dir.parent / (project_dir.name + 'log.txt'),
    ]
    return [c for c in candidates if c.is_file()]


def open_readonly(db_path):
    """Ouverture SQLite en lecture seule stricte (mode=ro) : le projet ne peut pas etre modifie."""
    uri = db_path.as_uri() + '?mode=ro'
    conn = sqlite3.connect(uri, uri=True, timeout=30)
    return conn


def check_schema(conn):
    cols = {row[1] for row in conn.execute('PRAGMA table_info(core_nodes)')}
    needed = {'node_id', 'flags', 'parent_id', 'v_hash', 'v_path', 'p_path',
              'magic', 'file_type', 'size_c', 'size_u'}
    missing = needed - cols
    if missing:
        raise SystemExit(
            'ERREUR : la table core_nodes ne contient pas les colonnes attendues {}. '
            'Ce fichier ne vient pas de la version de DECA attendue.'.format(sorted(missing)))


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

class Audit:
    def __init__(self, conn, sample_size=5):
        self.conn = conn
        self.sample_size = sample_size
        self.m = {}  # metriques scalaires
        self.ddsc_paths = set()  # chemins .ddsc nommes (scan_coverage) : un .hmddsc/.atxN n'est lisible qu'avec le sien

    # -- hierarchie --------------------------------------------------------

    def load_hierarchy(self):
        """
        Hierarchie DECA (db_processor.find_initial_files puis db_commands.process_arc/process_tab) :
            ARC (racine, p_path = fichier .arc)
             └─ TAB (p_path = fichier .tab)
                 └─ entrees de la table (v_hash, v_path NULL tant que non resolu)
                     └─ contenu imbrique (SARC, gdcc, ...)
        On memorise le parent de chaque noeud dans un tableau indexe par node_id
        (node_id est la cle primaire entiere, donc quasi dense) pour rester econome en memoire.
        """
        max_id = self.conn.execute('SELECT MAX(node_id) FROM core_nodes').fetchone()[0] or 0
        self.parent = array('q', [-1]) * (max_id + 1)
        for node_id, parent_id in self.conn.execute('SELECT node_id, parent_id FROM core_nodes'):
            self.parent[node_id] = -1 if parent_id is None else parent_id

        self.tab_path = {}
        for node_id, p_path in self.conn.execute(
                'SELECT node_id, p_path FROM core_nodes WHERE file_type = ?', (FTYPE_TAB,)):
            self.tab_path[node_id] = archive_label(to_text(p_path))

        # cache : node_id -> node_id du TAB ancetre (-1 = aucun, -2 = pas encore calcule)
        self.tab_of = array('q', [-2]) * (max_id + 1)

    def tab_ancestor(self, node_id):
        """Remonte jusqu'au noeud TAB ancetre, avec memoisation du chemin parcouru."""
        path = []
        cur = node_id
        result = -1
        while cur >= 0:
            cached = self.tab_of[cur]
            if cached != -2:
                result = cached
                break
            if cur in self.tab_path:
                result = cur
                break
            path.append(cur)
            cur = self.parent[cur]
        for n in path:
            self.tab_of[n] = result
        return result

    # -- passe principale --------------------------------------------------

    def scan_nodes(self):
        per_archive = collections.defaultdict(lambda: collections.Counter())
        by_type = collections.defaultdict(lambda: collections.Counter())
        unknown_magic = {}
        unknown_ext = collections.Counter()
        explored = set()   # (file_type, v_hash) dont au moins un noeud a le flag "explore"
        pending = []       # noeuds types sans ce flag : (file_type, v_hash, size_u, v_path)
        orphan_rows = []
        orphan_hashes = set()
        named_hashes = set()

        totals = collections.Counter()

        q = ('SELECT node_id, flags, parent_id, v_hash, v_path, file_type, magic, size_u '
             'FROM core_nodes')
        for node_id, flags, parent_id, v_hash, v_path, file_type, magic, size_u in self.conn.execute(q):
            flags = flags or 0
            size_u = size_u or 0
            if flags & FLAG_TEMPORARY_FILE:
                totals['ignores_temporaires'] += 1
                continue

            totals['noeuds'] += 1

            # Fichiers physiques du dossier du jeu (exe, .arc, .tab, fichiers hors archive) :
            # ce sont des racines (parent NULL) ou des TAB ; ils n'ont pas de nom virtuel a resoudre.
            # Constate sur une base produite par DECA : exe/arc/tab ont v_hash et v_path NULL.
            if parent_id is None or file_type in (FTYPE_ARC, FTYPE_TAB):
                totals['fichiers_physiques'] += 1
                continue

            tab_id = self.tab_ancestor(node_id)
            archive = self.tab_path.get(tab_id, '(hors archive)')
            direct = parent_id is not None and parent_id in self.tab_path
            niveau = 'direct' if direct else 'imbrique'
            orphan = v_path is None and v_hash is not None
            path_txt = to_text(v_path)
            type_key = file_type if file_type is not None else '(aucun type)'

            # -- 1. fichiers sans nom
            totals['virtuels'] += 1
            totals['virtuels_' + niveau] += 1
            per_archive[archive][niveau] += 1
            by_type[type_key]['total'] += 1
            if orphan:
                totals['orphelins'] += 1
                totals['orphelins_' + niveau] += 1
                per_archive[archive]['orphelins_' + niveau] += 1
                by_type[type_key]['orphelins'] += 1
                h = fmt_hash(v_hash, flags)
                orphan_hashes.add(h)
                hx, asc = magic_label(magic)
                orphan_rows.append((h, archive, niveau, type_key, asc, size_u))
            elif v_hash is not None:
                named_hashes.add(fmt_hash(v_hash, flags))

            # -- 2a. aucun type reconnu
            if file_type is None or file_type == FTYPE_NOT_HANDLED:
                key = 'NOT' if file_type == FTYPE_NOT_HANDLED else magic
                entry = unknown_magic.get(key)
                if entry is None:
                    entry = unknown_magic[key] = {
                        'noeuds': 0, 'octets': 0, 'nommes': 0,
                        'extensions': collections.Counter(), 'exemples': [],
                        'not_handled': file_type == FTYPE_NOT_HANDLED,
                    }
                entry['noeuds'] += 1
                entry['octets'] += size_u
                totals['sans_type'] += 1
                totals['sans_type_octets'] += size_u
                if path_txt:
                    entry['nommes'] += 1
                    ext = extension_of(path_txt)
                    entry['extensions'][ext] += 1
                    unknown_ext[ext] += 1
                    if len(entry['exemples']) < self.sample_size:
                        entry['exemples'].append(path_txt)

            # -- 2b. type reconnu mais jamais explore (decide apres la passe, voir finalize_unexplored)
            elif file_type in TYPES_EXPLORES and (flags & FLAG_PROCESSED_FILE_TYPE):
                if v_hash is not None:
                    explored.add((file_type, v_hash))
            else:
                pending.append((file_type, v_hash, size_u, path_txt))

        unexplored, skipped = self.finalize_unexplored(pending, explored)
        totals['non_explores'] = sum(u['noeuds'] for u in unexplored.values())
        totals['non_explores_fichiers'] = sum(u['fichiers'] for u in unexplored.values())
        totals['copies_deja_explorees'] = skipped

        self.explored = explored
        self.per_archive = per_archive
        self.by_type = by_type
        self.unknown_magic = unknown_magic
        self.unknown_ext = unknown_ext
        self.unexplored = unexplored
        self.orphan_rows = orphan_rows
        # un hash peut exister sous plusieurs noeuds (meme fichier dans plusieurs archives) ;
        # il n'est "vraiment" orphelin que si aucun noeud portant ce hash n'a de nom.
        self.orphan_unique = orphan_hashes - named_hashes
        self.m.update(totals)
        self.m['hashes_orphelins_uniques'] = len(self.orphan_unique)

    def finalize_unexplored(self, pending, explored):
        """
        Classe les noeuds types mais sans flag "explore".
        DECA n'explore qu'un noeud par v_hash : un noeud dont une copie (meme type, meme v_hash) a ete
        exploree n'est pas un manque, il est ignore ici. Les volumes sont comptes une fois par fichier unique.
        Renvoie (dict (type, raison) -> stats, nombre de copies ignorees).
        """
        unexplored = collections.defaultdict(lambda: {'noeuds': 0, 'fichiers': 0, 'octets': 0, 'exemples': []})
        seen = set()
        skipped = 0
        for file_type, v_hash, size_u, path_txt in pending:
            if file_type in TYPES_EXPLORES:
                if v_hash is not None and (file_type, v_hash) in explored:
                    skipped += 1
                    continue
                raison = 'etape existante, jamais terminee pour ce fichier'
            else:
                raison = 'aucune etape d\'exploration dans DECA'
            u = unexplored[(file_type, raison)]
            u['noeuds'] += 1
            if v_hash is None or (file_type, v_hash) not in seen:
                if v_hash is not None:
                    seen.add((file_type, v_hash))
                u['fichiers'] += 1
                u['octets'] += size_u
            if path_txt and len(u['exemples']) < self.sample_size:
                u['exemples'].append(path_txt)
        return unexplored, skipped

    # -- ADF ------------------------------------------------------------------

    def scan_adf_missing(self):
        """
        core_adf_types (db_core.adf_type_map_save) : une ligne avec pickle vide et
        missing_in != 0 signifie "type ADF reference dans le noeud missing_in mais sans definition".
        Sans definition, DECA ne peut pas decoder le fichier ADF correspondant.
        """
        tables = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.adf_missing = []
        self.adf_missing_nodes = set()
        if 'core_adf_types' not in tables:
            self.m['adf_types_manquants'] = 0
            self.m['adf_noeuds_affectes'] = 0
            return

        grouped = collections.defaultdict(list)
        for type_hash, missing_in in self.conn.execute(
                'SELECT type_hash, missing_in FROM core_adf_types '
                'WHERE length(pickle) = 0 AND missing_in IS NOT NULL AND missing_in != 0'):
            grouped[type_hash & 0xFFFFFFFF].append(missing_in)

        has_strings = 'core_strings' in tables
        for type_hash, nodes in grouped.items():
            name = ''
            if has_strings:
                # le hash du nom de type ADF est un hash32 ; DECA le stocke tel quel
                row = self.conn.execute(
                    'SELECT string FROM core_strings WHERE hash32 = ? OR hash32 = ? LIMIT 1',
                    (type_hash, type_hash - (1 << 32))).fetchone()
                if row:
                    name = to_text(row[0])
            examples = []
            for nid in nodes[:self.sample_size]:
                r = self.conn.execute(
                    'SELECT v_path, v_hash, flags FROM core_nodes WHERE node_id = ?', (nid,)).fetchone()
                if r:
                    examples.append(to_text(r[0]) or '(sans nom) ' + fmt_hash(r[1], r[2]))
            self.adf_missing.append({
                'type_hash': '{:08x}'.format(type_hash),
                'nom_type': name,
                'noeuds': len(set(nodes)),
                'exemples': examples,
            })
        self.adf_missing.sort(key=lambda d: -d['noeuds'])
        self.adf_missing_nodes = {n for v in grouped.values() for n in v}
        self.m['adf_types_manquants'] = len(self.adf_missing)
        self.m['adf_noeuds_affectes'] = len(self.adf_missing_nodes)

    # -- couverture de lecture par extension (COTWed DECA) ------------------

    def node_status(self, node_id, flags, file_type, path_txt=None, size=None):
        if flags & FLAG_PROCESSING_FAILED:
            return 'echec_scan'
        if file_type in ('adf', 'adf0', 'adf5', 'adfb') and node_id in self.adf_missing_nodes:
            return 'adf_sans_type'
        if file_type == 'symlink':
            return 'lien'
        if file_type in TYPES_MIP:
            if path_txt and posixpath.splitext(path_txt)[0] + '.ddsc' in self.ddsc_paths:
                return 'lisible'
            return 'mip_orphelin'
        if file_type in LECTEURS:
            return 'lisible'
        if file_type in CONTENEURS:
            return 'conteneur'
        if file_type is None or file_type == FTYPE_NOT_HANDLED:
            return 'texture_devinee' if taille_chaine_bc(size) else 'type_inconnu'
        return 'type_sans_visionneuse'

    def scan_coverage(self):
        """
        Couverture de lecture par famille de fichiers : l'extension pour un fichier nomme, "(sans nom) <type>"
        sinon (sans nom, l'extension est inconnue). Un fichier = un v_hash (ses copies dans plusieurs archives
        comptent une fois, avec le meilleur statut de ses copies) ; sans v_hash, un noeud.

        Lisible = une visionneuse de COTWed DECA montre son contenu structure (LECTEURS), ou c'est un conteneur
        dont DECA a range le contenu dans l'arbre. Les liens (entrees sans contenu propre) ne comptent pas.
        A lancer apres scan_nodes (fichiers explores) et scan_adf_missing (ADF sans definition).
        """
        rank = {st: i for i, st in enumerate(STATUTS)}
        fams = {}
        self.ddsc_paths = {to_text(v) for (v,) in self.conn.execute(
            "SELECT DISTINCT v_path FROM core_nodes WHERE CAST(v_path AS TEXT) LIKE '%.ddsc'")}
        q = ('SELECT node_id, flags, parent_id, v_hash, v_path, file_type, magic, size_u FROM core_nodes')
        for node_id, flags, parent_id, v_hash, v_path, file_type, magic, size_u in self.conn.execute(q):
            flags = flags or 0
            if flags & FLAG_TEMPORARY_FILE or parent_id is None or file_type in (FTYPE_ARC, FTYPE_TAB):
                continue
            path_txt = to_text(v_path)
            if path_txt:
                key = extension_of(path_txt)
            else:
                key = '(sans nom) {}'.format(file_type if file_type not in (None, FTYPE_NOT_HANDLED)
                                             else 'type inconnu')
            f = fams.get(key)
            if f is None:
                f = fams[key] = {'noeuds': 0, 'fichiers': {}, 'types': collections.Counter(),
                                 'magics': collections.Counter(), 'exemples': [], 'candidats': []}
            f['noeuds'] += 1
            status = self.node_status(node_id, flags, file_type, path_txt, size_u)
            fid = v_hash if v_hash is not None else ('noeud', node_id)
            explored = file_type in TYPES_EXPLORES and v_hash is not None and (file_type, v_hash) in self.explored
            old = f['fichiers'].get(fid)
            if old is None:
                f['fichiers'][fid] = [status, size_u or 0, explored, file_type]
                f['types'][file_type or '(aucun)'] += 1
                f['magics'][magic_short(magic)] += 1
                if status not in STATUTS_LUS and status != 'lien':
                    if path_txt and len(f['exemples']) < self.sample_size:
                        f['exemples'].append(path_txt)
                    if not path_txt and len(f['candidats']) < 4096:
                        f['candidats'].append((node_id, (magic_short(magic), size_u or 0)))
            else:
                if rank[status] < rank[old[0]]:
                    old[0], old[3] = status, file_type
                old[1] = max(old[1], size_u or 0)
                old[2] = old[2] or explored

        rows = []
        for key, f in fams.items():
            statuses = collections.Counter(v[0] for v in f['fichiers'].values())
            n = len(f['fichiers'])
            liens = statuses.get('lien', 0)
            utiles = n - liens
            lus = sum(statuses.get(st, 0) for st in STATUTS_LUS)
            pct_lu = pct(lus, utiles)
            if utiles == 0:
                verdict = 'liens seulement'
            elif pct_lu >= SEUIL_LU:
                verdict = 'lu'
            elif lus == 0:
                verdict = 'non lu'
            else:
                verdict = 'partiel'
            lecteurs = collections.Counter(
                'Image (devinee)' if v[0] in ('mip_orphelin', 'texture_devinee')
                else LECTEURS.get(v[3], 'conteneur' if v[3] in CONTENEURS else 'octets')
                for v in f['fichiers'].values() if v[0] != 'lien')
            explorables = [v for v in f['fichiers'].values() if v[3] in TYPES_EXPLORES]
            rows.append({
                'famille': key,
                'fichiers': n,
                'noeuds': f['noeuds'],
                'octets': sum(v[1] for v in f['fichiers'].values()),
                'lisibles': lus,
                'pct_lisibles': pct_lu,
                'verdict': verdict,
                'non_lus': utiles - lus,
                'statuts': statuses,
                'lecteurs': lecteurs,
                'types': f['types'],
                'magics': f['magics'],
                'explores': sum(1 for v in explorables if v[2]),
                'explorables': len(explorables),
                'exemples': f['exemples'],
                'noeuds_exemples': self.varied(f['candidats']),
            })
        rows.sort(key=lambda r: (-r['fichiers'], r['famille']))
        self.coverage = rows
        utiles = sum(r['fichiers'] - r['statuts'].get('lien', 0) for r in rows)
        lus = sum(r['lisibles'] for r in rows)
        self.m['couverture_fichiers'] = utiles
        self.m['couverture_lisibles'] = lus
        self.m['couverture_pct'] = round(pct(lus, utiles), 2)
        self.m['familles'] = len(rows)
        self.m['familles_non_lues'] = sum(1 for r in rows if r['verdict'] == 'non lu')
        self.m['familles_partielles'] = sum(1 for r in rows if r['verdict'] == 'partiel')
        self.m['echecs_scan'] = sum(r['statuts'].get('echec_scan', 0) for r in rows)
        self.m['mips_orphelins'] = sum(r['statuts'].get('mip_orphelin', 0) for r in rows)
        self.m['textures_devinees'] = sum(r['statuts'].get('texture_devinee', 0) for r in rows)

    def varied(self, candidates):
        """Noeuds sans nom a extraire : un par (magic, taille) d'abord, puis les suivants (2026-10-11 : les 28 restes
        sans type etaient 22 fichiers identiques de 6 octets et 6 autres, que les 5 premiers noeuds ne montraient pas)."""
        picked, seen = [], set()
        for node_id, key in candidates:
            if key not in seen and len(picked) < self.sample_size:
                picked.append(node_id)
                seen.add(key)
        for node_id, _key in candidates:
            if len(picked) >= self.sample_size:
                break
            if node_id not in picked:
                picked.append(node_id)
        return picked

    def priorities(self, limit=15):
        """Familles a etudier en phase C : le plus de fichiers non lisibles d'abord. Une famille "lue" (99 % et plus)
        qui garde des fichiers non lisibles en fait partie (23 .fmod_guids d'une autre disposition, 2026-10-10)."""
        rows = [r for r in self.coverage if r['verdict'] != 'liens seulement' and r['non_lus'] > 0]
        rows.sort(key=lambda r: (-r['non_lus'], -r['octets'], r['famille']))
        return rows[:limit]

    # -- meta -----------------------------------------------------------------

    def scan_meta(self):
        self.m['user_version'] = self.conn.execute('PRAGMA user_version').fetchone()[0]
        self.m['scan_termine'] = self.m['user_version'] >= SCAN_TERMINE_VERSION
        try:
            self.m['chaines_connues'] = self.conn.execute('SELECT COUNT(*) FROM core_strings').fetchone()[0]
        except sqlite3.OperationalError:
            self.m['chaines_connues'] = 0
        self.m['archives_tab'] = len(self.tab_path)


def summarize_exception(match):
    """
    Resume une ligne "process_N: EXCEPTION: [<class 'X'>, X('msg'), [trace...]]" en une ligne lisible :
    processus, type, message, et les deux cadres de pile les plus profonds (fichier:ligne, fonction).
    """
    process, exc_type, rest = match.group(1), match.group(2), match.group(3)
    msg_end = rest.find(', [')
    message = rest[:msg_end] if msg_end >= 0 else rest[:200]
    frames = RX_TRACE_FRAME.findall(rest)
    where = ' <- '.join('{}:{} ({})'.format(os.path.basename(f.replace('\\\\', '\\').replace('\\', '/')), ln, fn)
                        for f, ln, fn in reversed(frames[-2:]))
    return '{} : {} -- {}'.format(process, message, where or 'trace illisible')


def scan_logs(log_paths, sample_size=20):
    counts = collections.Counter()
    samples = collections.defaultdict(list)
    for lp in log_paths:
        with open(lp, 'r', encoding='utf-8', errors='replace') as f:
            for line in f:
                for key, rx in LOG_MOTIFS.items():
                    mt = rx.search(line)
                    if mt:
                        counts[key] += 1
                        if len(samples[key]) < sample_size:
                            # les lignes d'exception contiennent toute la trace : on les resume
                            text = summarize_exception(mt) if key == 'exception_processus' else line.strip()
                            samples[key].append(text)
    return counts, samples


# ---------------------------------------------------------------------------
# Ecriture des resultats
# ---------------------------------------------------------------------------

def write_csv(path, header, rows, sep):
    # utf-8-sig : Excel (Windows, locale FR) detecte l'encodage ; ';' est le separateur attendu.
    with open(path, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f, delimiter=sep)
        w.writerow(header)
        w.writerows(rows)


def fmt_counter(counter, n=3):
    return ', '.join('{} ({})'.format(k, v) for k, v in counter.most_common(n))


def sample_dir_name(famille):
    """Nom de dossier d'une famille : '.nl.mdic' -> 'nl.mdic', '(sans nom) rtpc' -> 'sans_nom_rtpc'."""
    name = famille.strip('.').replace('(', '').replace(')', '').replace(' ', '_')
    return ''.join(c for c in name if c.isalnum() or c in '._-') or 'famille'


def sample_command(row, project_dir, out_dir, n):
    """Commande extraire.py qui copie quelques fichiers de la famille (phase C : etude du format)."""
    target = out_dir / 'echantillons' / sample_dir_name(row['famille'])
    base = 'python {} "{}"'.format(Path('cotw') / 'extraire.py', project_dir)
    if row['famille'].startswith('(sans nom)'):
        if not row['noeuds_exemples']:
            return None
        sel = '--noeuds {}'.format(' '.join(str(i) for i in row['noeuds_exemples']))
    elif row['famille'] == '(sans extension)' or (row['verdict'] in ('partiel', 'lu') and row['exemples']):
        # famille lue en partie (.hmddsc sans .ddsc...) : les fichiers non lisibles eux-memes, pas un tirage
        if not row['exemples']:
            return None
        sel = ' '.join('"{}"'.format(x) for x in row['exemples'])
    else:
        sel = '--extension {} --echantillons {}'.format(row['famille'], n)
    # --a-plat : le pont du bureau ne copie pas au-dela de 7 sous-dossiers (chemins virtuels profonds)
    return '{} {} --a-plat --sortie "{}"'.format(base, sel, target)


def write_sample_script(path, rows, project_dir, out_dir, n):
    repo = Path(__file__).resolve().parent.parent
    lines = ['@echo off',
             'rem Echantillons des familles de fichiers que COTWed DECA ne sait pas encore lire (deca_audit.py,',
             'rem {}). DECA ferme ; l\'environnement virtuel (venv\\ du depot) est active par ce script.'.format(
                 datetime.now().isoformat(timespec='seconds')),
             'cd /d "{}"'.format(repo),
             'if exist venv\\Scripts\\activate.bat (call venv\\Scripts\\activate.bat) else (echo venv introuvable dans %CD% & exit /b 1)']
    count = 0
    for r in rows:
        cmd = sample_command(r, project_dir, out_dir, n)
        if cmd is None:
            continue
        lines.append('rem {} : {} fichiers non lisibles sur {} ({})'.format(
            r['famille'], r['non_lus'], r['fichiers'], r['verdict']))
        lines.append(cmd)
        count += 1
    with open(path, 'w', encoding='utf-8', newline='\r\n') as f:
        f.write('\n'.join(lines) + '\n')
    return count


def build_outputs(a, out_dir, sep, log_counts, log_samples, log_paths, db_path, compare, project_dir=None,
                  n_samples=5):
    out_dir.mkdir(parents=True, exist_ok=True)
    m = a.m

    # orphelins par archive
    arch_rows = []
    for archive, c in a.per_archive.items():
        tot = c['direct'] + c['imbrique']
        orph = c['orphelins_direct'] + c['orphelins_imbrique']
        arch_rows.append((archive, c['direct'], c['orphelins_direct'],
                          '{:.1f}'.format(pct(c['direct'] - c['orphelins_direct'], c['direct'])),
                          c['imbrique'], c['orphelins_imbrique'], tot, orph))
    arch_rows.sort(key=lambda r: (-r[7], r[0]))
    write_csv(out_dir / 'orphelins_par_archive.csv',
              ['archive', 'entrees_directes', 'orphelins_directs', 'pct_resolu_direct',
               'contenu_imbrique', 'orphelins_imbriques', 'total', 'orphelins_total'],
              arch_rows, sep)

    orphan_rows = sorted(a.orphan_rows, key=lambda r: (r[1], r[0]))
    write_csv(out_dir / 'hashes_orphelins.csv',
              ['hash', 'archive', 'niveau', 'type_detecte', 'magic_ascii', 'taille_octets'],
              orphan_rows, sep)

    magic_rows = []
    for key, e in a.unknown_magic.items():
        if e['not_handled']:
            hx, asc = 'NOT', '(DECA ne peut pas ouvrir ce noeud)'
        else:
            hx, asc = magic_label(key)
        top_ext = ', '.join('{} ({})'.format(x, n) for x, n in e['extensions'].most_common(5))
        magic_rows.append((hx, asc, e['noeuds'], e['octets'], e['nommes'], top_ext,
                           ' | '.join(e['exemples'])))
    magic_rows.sort(key=lambda r: (-r[3], -r[2]))
    write_csv(out_dir / 'types_inconnus_magic.csv',
              ['magic_hex', 'magic_ascii', 'noeuds', 'octets', 'dont_nommes', 'extensions', 'exemples'],
              magic_rows, sep)

    write_csv(out_dir / 'types_inconnus_ext.csv', ['extension', 'noeuds'],
              a.unknown_ext.most_common(), sep)

    unexp_rows = [(ft, raison, u['fichiers'], u['noeuds'], u['octets'], ' | '.join(u['exemples']))
                  for (ft, raison), u in a.unexplored.items()]
    unexp_rows.sort(key=lambda r: -r[2])
    write_csv(out_dir / 'types_non_explores.csv',
              ['type', 'raison', 'fichiers_uniques', 'noeuds', 'octets_fichiers_uniques', 'exemples'],
              unexp_rows, sep)

    write_csv(out_dir / 'adf_types_manquants.csv',
              ['type_hash', 'nom_type', 'noeuds_affectes', 'exemples'],
              [(d['type_hash'], d['nom_type'], d['noeuds'], ' | '.join(d['exemples']))
               for d in a.adf_missing], sep)

    cov_rows = [(r['famille'], r['verdict'], r['fichiers'], r['lisibles'], '{:.1f}'.format(r['pct_lisibles']),
                 r['non_lus'], r['octets'], r['noeuds'], fmt_counter(r['lecteurs'], 4), fmt_counter(r['types'], 4),
                 fmt_counter(r['magics'], 3), '{}/{}'.format(r['explores'], r['explorables']),
                 r['statuts'].get('echec_scan', 0), r['statuts'].get('adf_sans_type', 0),
                 r['statuts'].get('mip_orphelin', 0), r['statuts'].get('lien', 0), ' | '.join(r['exemples']),
                 ' '.join(str(i) for i in r['noeuds_exemples']))
                for r in a.coverage]
    write_csv(out_dir / 'couverture_extensions.csv',
              ['famille', 'verdict', 'fichiers_uniques', 'lisibles', 'pct_lisibles', 'non_lisibles', 'octets',
               'noeuds', 'visionneuses', 'types_detectes', 'magics', 'explores_par_deca', 'echecs_scan',
               'adf_sans_type', 'mips_orphelins', 'liens', 'exemples_non_lisibles', 'noeuds_exemples'],
              cov_rows, sep)
    prio = a.priorities()
    n_cmds = write_sample_script(out_dir / 'extraire_echantillons.bat', prio, project_dir or db_path.parent.parent,
                                 out_dir, n_samples)

    if log_paths:
        with open(out_dir / 'log_resume.txt', 'w', encoding='utf-8') as f:
            f.write('Journaux lus : {}\n\n'.format(', '.join(str(p) for p in log_paths)))
            for key in LOG_MOTIFS:
                f.write('== {} : {} occurrence(s)\n'.format(key, log_counts.get(key, 0)))
                for s in log_samples.get(key, []):
                    f.write('   {}\n'.format(s))
                f.write('\n')

    metrics = dict(m)
    metrics['date'] = datetime.now().isoformat(timespec='seconds')
    metrics['base'] = str(db_path)
    metrics['par_archive_orphelins'] = {r[0]: r[7] for r in arch_rows}
    metrics['par_magic_noeuds'] = {'{} {}'.format(r[0], r[1]).strip(): r[2] for r in magic_rows}
    metrics['journal'] = dict(log_counts)
    metrics['par_famille_pct_lisibles'] = {r['famille']: round(r['pct_lisibles'], 1) for r in a.coverage}
    metrics['commandes_echantillons'] = n_cmds
    with open(out_dir / 'metrics.json', 'w', encoding='utf-8') as f:
        json.dump(metrics, f, indent=2, ensure_ascii=False)

    comparison = compare_metrics(compare, metrics) if compare else None
    write_report(out_dir / 'rapport.md', a, arch_rows, magic_rows, unexp_rows,
                 log_counts, log_samples, log_paths, db_path, comparison, prio, n_cmds)
    return metrics, comparison


SCALAIRES_COMPARES = [
    ('orphelins_direct', 'Entrees .tab sans nom', True),
    ('orphelins_imbrique', 'Contenu imbrique sans nom', True),
    ('hashes_orphelins_uniques', 'Hashes orphelins uniques', True),
    ('sans_type', 'Noeuds sans type reconnu', True),
    ('non_explores_fichiers', 'Fichiers de type reconnu jamais explores', True),
    ('adf_types_manquants', 'Types ADF manquants', True),
    ('adf_noeuds_affectes', 'Fichiers ADF affectes', True),
    ('couverture_lisibles', 'Fichiers lisibles par une visionneuse', False),
    ('familles_non_lues', 'Familles (extensions) non lues', True),
    ('echecs_scan', 'Fichiers en echec au scan', True),
    ('mips_orphelins', 'Niveaux de mip sans leur .ddsc', True),
    ('textures_devinees', 'Textures sans type ni en-tete (devinees)', True),
    ('chaines_connues', 'Chaines connues (dictionnaire)', False),
    ('virtuels', 'Fichiers virtuels total', False),
]


def compare_metrics(old_path, new):
    with open(old_path, 'r', encoding='utf-8') as f:
        old = json.load(f)
    rows = []
    for key, label, lower_is_better in SCALAIRES_COMPARES:
        o, n = old.get(key, 0), new.get(key, 0)
        d = n - o
        verdict = '=' if d == 0 else ('mieux' if (d < 0) == lower_is_better else 'moins bien')
        rows.append((label, o, n, d, verdict))
    return {'reference': old_path, 'date_reference': old.get('date', '?'), 'lignes': rows}


def write_report(path, a, arch_rows, magic_rows, unexp_rows, log_counts, log_samples, log_paths, db_path,
                 comparison, prio=(), n_cmds=0):
    m = a.m
    L = []
    L.append('# Audit DECA -- theHunter: Call of the Wild')
    L.append('')
    L.append('- Base : `{}`'.format(db_path))
    L.append('- Date : {}'.format(datetime.now().isoformat(timespec='seconds')))
    etat = 'termine' if m['scan_termine'] else 'INCOMPLET (user_version={}) : chiffres partiels'.format(
        m['user_version'])
    L.append('- Scan DECA : **{}**'.format(etat))
    for summary in log_samples.get('exception_processus', [])[:3]:
        L.append('- Exception d\'un processus DECA (journal) : `{}`'.format(summary))
    L.append('- Archives .tab : {}  |  chaines connues : {}'.format(m['archives_tab'], m['chaines_connues']))
    L.append('')

    if comparison:
        L.append('## Evolution depuis `{}` ({})'.format(comparison['reference'], comparison['date_reference']))
        L.append('')
        L.append('| Mesure | Avant | Apres | Ecart | |')
        L.append('|---|---:|---:|---:|---|')
        for label, o, n, d, v in comparison['lignes']:
            L.append('| {} | {} | {} | {:+d} | {} |'.format(label, o, n, d, v))
        L.append('')

    L.append('## 1. Fichiers sans nom')
    L.append('')
    vd, od = m.get('virtuels_direct', 0), m.get('orphelins_direct', 0)
    vi, oi = m.get('virtuels_imbrique', 0), m.get('orphelins_imbrique', 0)
    L.append('| Niveau | Total | Sans nom | Resolu |')
    L.append('|---|---:|---:|---:|')
    L.append('| Entrees directes des .tab | {} | {} | {:.1f} % |'.format(vd, od, pct(vd - od, vd)))
    L.append('| Contenu imbrique (SARC, gdcc...) | {} | {} | {:.1f} % |'.format(vi, oi, pct(vi - oi, vi)))
    L.append('')
    L.append('Hashes orphelins uniques (aucun noeud portant ce hash n\'a de nom) : **{}**'.format(
        m.get('hashes_orphelins_uniques', 0)))
    L.append('')
    L.append('Archives les plus touchees (liste complete : `orphelins_par_archive.csv`) :')
    L.append('')
    L.append('| Archive | Entrees | Sans nom | Resolu |')
    L.append('|---|---:|---:|---:|')
    for r in arch_rows[:15]:
        L.append('| `{}` | {} | {} | {} % |'.format(r[0], r[1], r[2], r[3]))
    L.append('')

    L.append('## 2. Formats non lus')
    L.append('')
    L.append('### 2a. Aucun type reconnu : {} noeuds, {}'.format(
        m.get('sans_type', 0), human_bytes(m.get('sans_type_octets', 0))))
    L.append('')
    L.append('Regroupes par leurs 4 premiers octets, tries par volume. '
             'Chaque ligne est un candidat a un nouveau parseur.')
    L.append('')
    L.append('Colonne Nommes : sans nom, un fichier ne peut pas etre type par son extension ; '
             'une ligne a 0 nomme est souvent un probleme de nom avant d\'etre un probleme de format.')
    L.append('')
    L.append('| Magic (hex) | ASCII | Noeuds | Nommes | Volume | Extensions vues |')
    L.append('|---|---|---:|---:|---:|---|')
    for r in magic_rows[:20]:
        asc = '`{}`'.format(r[1]) if r[1] else '-'
        L.append('| `{}` | {} | {} | {} | {} | {} |'.format(
            r[0], asc, r[2], r[4], human_bytes(r[3]), r[5] or '-'))
    L.append('')
    L.append('### 2b. Types reconnus mais jamais explores : {} fichiers uniques ({} noeuds)'.format(
        m.get('non_explores_fichiers', 0), m.get('non_explores', 0)))
    L.append('')
    L.append('DECA sait identifier ces fichiers mais n\'en extrait ni contenu ni chaines, '
             'donc ils ne contribuent jamais a la resolution de noms. DECA n\'explore qu\'une copie par '
             'hash : {} noeuds dont une copie a ete exploree sont ignores ici. '
             'Volume = fichiers uniques.'.format(m.get('copies_deja_explorees', 0)))
    L.append('')
    L.append('| Type | Raison | Fichiers uniques | Noeuds | Volume |')
    L.append('|---|---|---:|---:|---:|')
    for r in unexp_rows[:20]:
        L.append('| `{}` | {} | {} | {} | {} |'.format(r[0], r[1], r[2], r[3], human_bytes(r[4])))
    L.append('')
    L.append('### 2c. Types ADF sans definition : {} types, {} fichiers affectes'.format(
        m.get('adf_types_manquants', 0), m.get('adf_noeuds_affectes', 0)))
    L.append('')
    if a.adf_missing:
        L.append('| Hash du type | Nom (si connu) | Fichiers | Exemple |')
        L.append('|---|---|---:|---|')
        for d in a.adf_missing[:20]:
            L.append('| `{}` | {} | {} | {} |'.format(
                d['type_hash'], d['nom_type'] or '-', d['noeuds'], d['exemples'][0] if d['exemples'] else '-'))
        L.append('')

    L.append('## 3. Couverture de lecture par extension (COTWed DECA)')
    L.append('')
    L.append('Fichiers uniques lisibles par une visionneuse de l\'interface (RTPC, ADF, Strings, Sheet, SARC, '
             'Image, Text, OBC, Structure, Havok) ou ranges par DECA dans l\'arbre (conteneurs) : **{} / {} ({:.1f} %)**. '
             'Familles : {} ; non lues : {} ; partielles : {}. Echecs au scan : {}. Lus avec une disposition '
             'devinee (onglet Image, format et taille devines) : {} niveaux de mip sans leur `.ddsc`, {} fichiers '
             'sans type de la taille d\'une chaine de mips.'.format(
                 m.get('couverture_lisibles', 0), m.get('couverture_fichiers', 0), m.get('couverture_pct', 0.0),
                 m.get('familles', 0), m.get('familles_non_lues', 0), m.get('familles_partielles', 0),
                 m.get('echecs_scan', 0), m.get('mips_orphelins', 0), m.get('textures_devinees', 0)))
    L.append('')
    L.append('Famille = extension pour un fichier nomme (a partir du premier point : `.nl.mdic`), '
             '`(sans nom) <type>` sinon. Verdict : *lu* a partir de {:.0f} % de fichiers lisibles. Detail de '
             'toutes les familles : `couverture_extensions.csv`.'.format(SEUIL_LU))
    L.append('')
    L.append('| Famille | Fichiers | Lisibles | Verdict | Visionneuse | Types detectes | Magic |')
    L.append('|---|---:|---:|---|---|---|---|')
    for r in a.coverage[:30]:
        L.append('| `{}` | {} | {:.1f} % | {} | {} | {} | {} |'.format(
            r['famille'], r['fichiers'], r['pct_lisibles'], r['verdict'], fmt_counter(r['lecteurs'], 2) or '-',
            fmt_counter(r['types'], 2) or '-', fmt_counter(r['magics'], 2) or '-'))
    L.append('')
    L.append('### Priorites (phase C : etude des formats)')
    L.append('')
    if prio:
        L.append('Familles avec le plus de fichiers non lisibles. `extraire_echantillons.bat` ({} commandes) en '
                 'copie quelques-uns par famille, repartis par taille, dans `echantillons\\` : ce sont les '
                 'fichiers a apporter sur le portable pour l\'etude.'.format(n_cmds))
        L.append('')
        L.append('| Famille | Non lisibles | Volume | Types detectes | Magic | Exemple |')
        L.append('|---|---:|---:|---|---|---|')
        for r in prio:
            ex = r['exemples'][0] if r['exemples'] else (
                'noeud {}'.format(r['noeuds_exemples'][0]) if r['noeuds_exemples'] else '-')
            L.append('| `{}` | {} | {} | {} | {} | `{}` |'.format(
                r['famille'], r['non_lus'], human_bytes(r['octets']), fmt_counter(r['types'], 2) or '-',
                fmt_counter(r['magics'], 2) or '-', ex))
    else:
        L.append('Aucune famille non lue.')
    L.append('')

    L.append('## 4. Journal DECA')
    L.append('')
    if log_paths:
        L.append('Lu : ' + ', '.join('`{}`'.format(p) for p in log_paths))
        L.append('')
        for key in LOG_MOTIFS:
            L.append('- {} : {}'.format(key, log_counts.get(key, 0)))
        L.append('')
        if log_counts.get('scan_complet', 0) == 0:
            L.append('**`PROCESSING: COMPLETE` absent du journal : le scan ne s\'est pas termine.**')
            L.append('')
        for summary in log_samples.get('exception_processus', []):
            L.append('- `{}`'.format(summary))
        L.append('')
        L.append('Detail et exemples : `log_resume.txt`.')
    else:
        L.append('Aucun journal trouve (ni `log.txt` dans le projet, ni `hplog.txt` a cote).')
    L.append('')

    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(L) + '\n')


def print_summary(m, out_dir, comparison, log_samples):
    p = print
    p('')
    p('=== Audit DECA ===')
    if not m['scan_termine']:
        p('ATTENTION : scan DECA incomplet (user_version={}), chiffres partiels.'.format(m['user_version']))
    for summary in log_samples.get('exception_processus', [])[:3]:
        p('Exception DECA (journal)     : {}'.format(summary))
    vd, od = m.get('virtuels_direct', 0), m.get('orphelins_direct', 0)
    vi, oi = m.get('virtuels_imbrique', 0), m.get('orphelins_imbrique', 0)
    p('Archives .tab                : {}'.format(m['archives_tab']))
    p('Entrees .tab sans nom        : {} / {}  ({:.1f} % resolues)'.format(od, vd, pct(vd - od, vd)))
    p('Contenu imbrique sans nom    : {} / {}  ({:.1f} % resolus)'.format(oi, vi, pct(vi - oi, vi)))
    p('Hashes orphelins uniques     : {}'.format(m.get('hashes_orphelins_uniques', 0)))
    p('Noeuds sans type reconnu     : {}  ({})'.format(m.get('sans_type', 0), human_bytes(m.get('sans_type_octets', 0))))
    p('Types reconnus non explores  : {} fichiers uniques ({} noeuds)'.format(
        m.get('non_explores_fichiers', 0), m.get('non_explores', 0)))
    p('Types ADF manquants          : {}  ({} fichiers affectes)'.format(
        m.get('adf_types_manquants', 0), m.get('adf_noeuds_affectes', 0)))
    p('Fichiers lisibles (COTWed)   : {} / {}  ({:.1f} %) ; familles non lues : {}, partielles : {}'.format(
        m.get('couverture_lisibles', 0), m.get('couverture_fichiers', 0), m.get('couverture_pct', 0.0),
        m.get('familles_non_lues', 0), m.get('familles_partielles', 0)))
    if m.get('echecs_scan', 0):
        p('Fichiers en echec au scan    : {}  (journal : loop_over_uid_wrapper: failed for)'.format(m['echecs_scan']))
    if m.get('mips_orphelins', 0):
        p('Mips sans leur .ddsc         : {}  (.hmddsc/.atxN, disposition devinee)'.format(m['mips_orphelins']))
    if m.get('textures_devinees', 0):
        p('Textures sans en-tete        : {}  (sans type, disposition devinee)'.format(m['textures_devinees']))
    if comparison:
        p('')
        p('--- Evolution depuis {} ---'.format(comparison['date_reference']))
        for label, o, n, d, v in comparison['lignes']:
            p('{:<40} {:>9} -> {:>9}  ({:+d}, {})'.format(label, o, n, d, v))
    p('')
    p('Resultats ecrits dans : {}'.format(out_dir))


def main(argv=None):
    # La console Windows n'est pas toujours en UTF-8 : on evite qu'un nom de fichier fasse planter l'affichage.
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(errors='replace')

    ap = argparse.ArgumentParser(description='Audit en lecture seule d\'un projet DECA (COTW).')
    ap.add_argument('projet', help='dossier du projet DECA (ex: C:\\dev\\work\\hp), project.json ou core.db')
    ap.add_argument('--out', help='dossier de sortie (defaut : <work>\\audits\\<projet>_<date-heure>)')
    ap.add_argument('--compare', help='metrics.json d\'un audit precedent, pour afficher l\'evolution')
    ap.add_argument('--sep', default=';', help='separateur CSV (defaut ";" pour Excel en francais)')
    ap.add_argument('--exemples', type=int, default=5, help='nombre d\'exemples par ligne (defaut 5)')
    ap.add_argument('--echantillons', type=int, default=5,
                    help='fichiers par famille dans extraire_echantillons.bat (defaut 5)')
    args = ap.parse_args(argv)

    db_path, project_dir = resolve_paths(args.projet)
    if args.out:
        out_dir = Path(args.out).expanduser().resolve()
    else:
        stamp = datetime.now().strftime('%Y-%m-%d_%H%M%S')
        out_dir = project_dir.parent / 'audits' / '{}_{}'.format(project_dir.name, stamp)
    print('Base   : {}'.format(db_path))
    print('Sortie : {}'.format(out_dir))

    try:
        conn = open_readonly(db_path)
        check_schema(conn)
        audit = Audit(conn, sample_size=args.exemples)
        print('Lecture de la hierarchie...')
        audit.load_hierarchy()
        print('Analyse des noeuds...')
        audit.scan_nodes()
        audit.scan_adf_missing()
        audit.scan_meta()
        print('Couverture de lecture par extension...')
        audit.scan_coverage()
        conn.close()
    except sqlite3.OperationalError as e:
        raise SystemExit('ERREUR SQLite : {}. Fermez DECA avant de lancer l\'audit.'.format(e))

    log_paths = find_logs(project_dir)
    log_counts, log_samples = scan_logs(log_paths)
    metrics, comparison = build_outputs(audit, out_dir, args.sep, log_counts, log_samples,
                                        log_paths, db_path, args.compare, project_dir, args.echantillons)
    print_summary(metrics, out_dir, comparison, log_samples)
    return 0


if __name__ == '__main__':
    sys.exit(main())
