#!/usr/bin/env python3
"""
renommer.py -- donne un nom aux fichiers sans nom d'un projet DECA deja scanne, sans rescanner.

Pourquoi : DECA ne charge ses sources de noms qu'a la creation d'un projet (PRAGMA user_version < 2). Essayer une
liste de noms coutait donc un rescan complet (64 min sur COTW). Ce script fait le meme travail que la fin du scan,
mais seulement pour les fichiers sans nom :
  1. les noms candidats dont le hash32 est celui d'un fichier sans nom sont gardes, ainsi que leurs noms associes
     (".ddsc" -> ".hmddsc", ".ee" -> ".epe"..., regle de db_processor.find_vpath_by_assoc) ;
  2. controle de type : le type que DECA a detecte dans le fichier doit etre un type deja vu pour cette extension
     (noms_fichiers.ModeleTypes) ; sinon le nom est refuse (faux positif de hash probable) ;
  3. un hash qui recoit deux noms acceptes est laisse sans nom (ambigu) ;
  4. les noms gardes sont ajoutes a core_strings avec leurs types (possible_file_types), puis DECA lui-meme
     (db_commands.Processor.process_vhash_final, le code du scan) nomme les noeuds de ces hashes. Il pose aussi
     le type des .hmddsc et .atx, comme au scan ;
  5. vpaths.txt est reecrit.

Tout ce qui est ecrit est journalise dans <projet>\\cotw_renommages.tsv ; "annuler" remet la base dans son etat
precedent (noms retires, chaines et references ajoutees supprimees). DECA doit etre ferme (base verrouillee).

Usage, depuis la racine du depot, environnement virtuel active :
    python cotw\\renommer.py ajouter D:\\dev\\work\\hp --noms noms.txt [--noms autres.txt] [--exe] [--simulation]
    python cotw\\renommer.py annuler D:\\dev\\work\\hp
Un fichier de noms contient un chemin virtuel par ligne ; les lignes vides ou commencant par '#' sont ignorees,
et seule la premiere colonne d'une ligne a tabulations est lue (format de vpaths.txt).
"""
import argparse
import collections
import datetime
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _projet  # noqa: E402  (installe le substitut de zugbruecke sous Linux)
import noms_fichiers as NF  # noqa: E402

JOURNAL = 'cotw_renommages.tsv'


# ---------------------------------------------------------------------------------------------------------
# Lecture des candidats
# ---------------------------------------------------------------------------------------------------------

def lire_noms(paths: Iterable[str]) -> List[str]:
    noms = []
    for p in paths:
        with open(p, 'r', encoding='utf-8', errors='replace') as f:
            for line in f:
                line = line.rstrip('\r\n')
                if not line.strip() or line.lstrip().startswith('#'):
                    continue
                nom = line.split('\t', 1)[0].strip().replace('\\', '/')
                if nom:
                    noms.append(nom)
    return noms


def noms_de_l_exe(vfs) -> List[str]:
    from deca.exe_strings import exe_path_strings
    exe = Path(vfs.game_info.game_dir) / vfs.game_info.exe_name
    if not exe.is_file():
        raise SystemExit('ERREUR : executable du jeu introuvable : {}'.format(exe))
    paths, _patterns = exe_path_strings(str(exe))
    return sorted(p.decode('ascii') for p in paths)


# ---------------------------------------------------------------------------------------------------------
# Plan : quels noms donner a quels hashes
# ---------------------------------------------------------------------------------------------------------

class Plan:
    def __init__(self):
        self.retenus: Dict[int, Tuple[str, Optional[Set[Optional[str]]], str]] = {}  # hash -> (nom, types, origine)
        self.non_controles: List[str] = []           # extension jamais vue : pas de controle de type
        self.refus_type: List[Tuple[str, int, Set[Optional[str]], Set[Optional[str]]]] = []
        self.ambigus: Dict[int, List[str]] = {}
        self.deja_nommes = 0                          # nom deja present dans la base
        self.collisions: List[Tuple[str, int]] = []   # hash d'un fichier deja nomme sous un autre nom
        self.hors_projet = 0                          # hash d'aucun fichier du projet
        self.non_ascii: List[str] = []
        self.candidats = 0
        self.associes = 0


def preparer(etat: NF.Etat, modele: NF.ModeleTypes, noms: Iterable[str], file_assoc: List[dict],
             strict: bool = False) -> Plan:
    from deca.hashes import hash32_func
    from deca.ff_types import ftype_list
    plan = Plan()
    candidats: Dict[str, str] = {}
    for nom in noms:
        candidats.setdefault(nom, 'liste')
    for nom in list(candidats):
        for autre, _types in NF.associes(nom, file_assoc):
            if autre not in candidats:
                candidats[autre] = 'association'
                plan.associes += 1
    plan.candidats = len(candidats)

    hashes_nommes = None
    acceptes: Dict[int, List[Tuple[str, Optional[Set[Optional[str]]], str]]] = collections.defaultdict(list)
    for nom, origine in candidats.items():
        try:
            data = nom.encode('ascii')
        except UnicodeEncodeError:
            plan.non_ascii.append(nom)
            continue
        h = int(hash32_func(data)) & 0xFFFFFFFF
        types_noeuds = etat.sans_nom.get(h)
        if types_noeuds is None:
            if nom in etat.nommes:
                plan.deja_nommes += 1
            else:
                if hashes_nommes is None:
                    hashes_nommes = {int(hash32_func(p.encode('utf-8', 'replace'))) & 0xFFFFFFFF for p in etat.nommes}
                if h in hashes_nommes:
                    plan.collisions.append((nom, h))
                else:
                    plan.hors_projet += 1
            continue
        if any(t is not None and t not in ftype_list for t in types_noeuds):
            # process_vhash_final indexe ftype_list par le type du noeud : un type hors liste ('NOT') le ferait
            # echouer au milieu de la session
            plan.refus_type.append((nom, h, types_noeuds, set()))
            continue
        verdict = modele.accepte(nom, types_noeuds)
        if verdict is False:
            plan.refus_type.append((nom, h, types_noeuds, modele.types_pour(nom)))
            continue
        if verdict is None:
            if strict:
                plan.refus_type.append((nom, h, types_noeuds, set()))
                continue
            plan.non_controles.append(nom)
        acceptes[h].append((nom, modele.types_pour(nom), origine))

    for h, liste in acceptes.items():
        if len({nom for nom, _t, _o in liste}) > 1:
            plan.ambigus[h] = sorted({nom for nom, _t, _o in liste})
        else:
            plan.retenus[h] = liste[0]
    return plan


# ---------------------------------------------------------------------------------------------------------
# Application et journal
# ---------------------------------------------------------------------------------------------------------

class Comm:
    """Interface attendue par db_commands.Processor (log, trace, status, exception, error)."""
    def __init__(self, verbose=False):
        self.verbose = verbose
        self.erreurs: List[str] = []

    def log(self, msg):
        if self.verbose:
            print(msg)

    trace = log

    def status(self, i, n):
        pass

    def exception(self, exc):
        self.erreurs.append(str(exc))

    def error(self, msg):
        self.erreurs.append(str(msg))


def ecrire_vpaths(vfs) -> Path:
    """vpaths.txt du projet, au format de db_processor.dump_vpaths (que DECA n'ecrit que s'il est absent)."""
    rows = vfs.nodes_select_distinct_vpath_content_hash()
    lignes = sorted({(v or '', c or '') for v, c in rows})
    path = Path(vfs.working_dir) / 'vpaths.txt'
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        for v, c in lignes:
            f.write('{}\t{}\n'.format(v, c))
    return path


def _noeuds(vfs, where: str, params: List) -> List[tuple]:
    out = []
    for i in range(0, len(params), 500):
        chunk = params[i:i + 500]
        out += vfs.db_query_all(
            'SELECT node_id, v_hash, v_path, file_type, ext_hash, used_at_runtime_depth FROM core_nodes '
            'WHERE {} IN ({})'.format(where, ','.join('?' * len(chunk))), chunk, dbg='cotw_renommer_noeuds')
    return out


def appliquer(vfs, plan: Plan, journal_path: Path, comm: Optional[Comm] = None) -> Dict[str, object]:
    """Ecrit les noms du plan dans la base. Renvoie le bilan : noeuds nommes, par extension, etc."""
    from deca.db_types import make_hash_string_tuple
    from deca.db_commands import Processor
    comm = comm or Comm()
    if not plan.retenus:
        return {'hashes': 0, 'noeuds': 0, 'par_extension': collections.Counter(), 'autres_noms': []}

    stamp = datetime.datetime.now().isoformat(timespec='seconds')
    hashes = sorted(plan.retenus)
    noms = [plan.retenus[h][0] for h in hashes]
    conn = vfs.db_conn
    cur = conn.cursor()

    with open(journal_path, 'a', encoding='utf-8', newline='\n') as journal:
        journal.write('# session\t{}\t{} hashes\n'.format(stamp, len(hashes)))

        # 1. chaines absentes de core_strings
        presents = {}
        for i in range(0, len(noms), 500):
            chunk = noms[i:i + 500]
            for rowid, s in cur.execute('SELECT rowid, string FROM core_strings WHERE string IN ({})'.format(
                    ','.join('?' * len(chunk))), chunk):
                presents[_projet.to_text(s)] = rowid
        rowids: Dict[str, int] = dict(presents)
        for nom in noms:
            if nom in rowids:
                continue
            s, h32, h48, h64, ext32 = make_hash_string_tuple(nom.encode('ascii'))
            cur.execute('INSERT INTO core_strings VALUES (?,?,?,?,?)', (nom, h32, h48, h64, ext32))
            rowids[nom] = cur.lastrowid
            journal.write('chaine\t{}\t{}\n'.format(cur.lastrowid, nom))

        # 2. une reference par nom, avec les types attendus : process_vhash_final ne nomme que les noeuds dont
        #    le type est dans le masque (le controle de type est donc aussi fait par DECA)
        for h in hashes:
            nom, types, _origine = plan.retenus[h]
            mask = NF.masque_deca(types)
            cur.execute('INSERT INTO core_string_references VALUES (?,?,?,?,?)', (rowids[nom], None, 0, None, mask))
            journal.write('reference\t{}\t{}\n'.format(cur.lastrowid, nom))
        conn.commit()

        # 3. etat des noeuds cibles avant (pour annuler)
        avant = _noeuds(vfs, 'v_hash', hashes)
        avant = [r for r in avant if r[2] is None]
        for node_id, v_hash, _v, file_type, ext_hash, depth in avant:
            journal.write('cible\t{}\t{:08x}\t{}\t{}\t{}\t{}\n'.format(
                node_id, v_hash & 0xFFFFFFFF, file_type or '', '' if ext_hash is None else ext_hash,
                '' if depth is None else depth, plan.retenus[v_hash & 0xFFFFFFFF][0]))
        journal.flush()

        # 4. DECA nomme les noeuds (code du scan)
        Processor(vfs, comm).process_command('process_vhash_final', [hashes])

        # 5. resultat
        apres = {r[0]: r for r in _noeuds(vfs, 'node_id', [r[0] for r in avant])}
        bilan_ext = collections.Counter()
        autres = []
        nommes = set()
        for node_id, v_hash, _v, *_rest in avant:
            r = apres.get(node_id)
            if r is None or r[2] is None:
                continue
            v_path = _projet.to_text(r[2])
            attendu = plan.retenus[v_hash & 0xFFFFFFFF][0]
            journal.write('noeud\t{}\t{}\t{}\n'.format(node_id, v_path, r[3] or ''))
            nommes.add(v_hash & 0xFFFFFFFF)
            bilan_ext[NF.extension_complete(v_path)] += 1
            if v_path != attendu:
                autres.append((node_id, attendu, v_path))
    ecrire_vpaths(vfs)
    return {'hashes': len(nommes), 'noeuds': sum(bilan_ext.values()), 'par_extension': bilan_ext,
            'autres_noms': autres, 'non_nommes': sorted(set(hashes) - nommes), 'erreurs': comm.erreurs}


def annuler(vfs, journal_path: Path) -> Dict[str, int]:
    """Annule toutes les sessions du journal : noms retires des noeuds (s'ils portent encore un nom de la
    session), references puis chaines ajoutees supprimees (une chaine encore referencee ailleurs est gardee)."""
    cibles: Dict[int, Tuple[str, Optional[int], Optional[int], Set[str]]] = {}
    noms_noeud: Dict[int, Set[str]] = collections.defaultdict(set)
    refs: List[int] = []
    chaines: List[int] = []
    for line in journal_path.read_text(encoding='utf-8').splitlines():
        cols = line.split('\t')
        if cols[0] == 'chaine':
            chaines.append(int(cols[1]))
        elif cols[0] == 'reference':
            refs.append(int(cols[1]))
        elif cols[0] == 'cible':
            node_id = int(cols[1])
            ext_hash = int(cols[4]) if cols[4] else None
            depth = int(cols[5]) if cols[5] else None
            if node_id not in cibles:     # premiere session qui l'a touche : etat d'origine
                cibles[node_id] = (cols[3] or None, ext_hash, depth, set())
            noms_noeud[node_id].add(cols[6])
        elif cols[0] == 'noeud':
            noms_noeud[int(cols[1])].add(cols[2])

    conn = vfs.db_conn
    cur = conn.cursor()
    bilan = {'noeuds': 0, 'noeuds_modifies_depuis': 0, 'references': 0, 'chaines': 0, 'chaines_gardees': 0}
    for node_id, (file_type, ext_hash, depth, _s) in cibles.items():
        row = cur.execute('SELECT v_path FROM core_nodes WHERE node_id = ?', (node_id,)).fetchone()
        if row is None or row[0] is None:
            continue
        if _projet.to_text(row[0]) not in noms_noeud[node_id]:
            bilan['noeuds_modifies_depuis'] += 1      # renomme depuis par autre chose : on n'y touche pas
            continue
        cur.execute('UPDATE core_nodes SET v_path = NULL, file_type = ?, ext_hash = ?, used_at_runtime_depth = ? '
                    'WHERE node_id = ?', (file_type, ext_hash, depth, node_id))
        bilan['noeuds'] += 1
    for rowid in refs:
        bilan['references'] += cur.execute('DELETE FROM core_string_references WHERE rowid = ?', (rowid,)).rowcount
    for rowid in chaines:
        n = cur.execute('DELETE FROM core_strings WHERE rowid = ? AND rowid NOT IN '
                        '(SELECT string_rowid FROM core_string_references)', (rowid,)).rowcount
        bilan['chaines'] += n
        bilan['chaines_gardees'] += 1 - n
    conn.commit()
    ecrire_vpaths(vfs)
    journal_path.rename(journal_path.with_name(
        journal_path.name + '.annule-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S')))
    return bilan


# ---------------------------------------------------------------------------------------------------------
# Rapport
# ---------------------------------------------------------------------------------------------------------

def fmt_types(types) -> str:
    if types is None:
        return '?'
    return ', '.join(sorted('(aucun)' if t is None else t for t in types)) or '-'


def rapport(plan: Plan, bilan: Optional[dict], sans_nom_avant: int, simulation: bool, limite: int = 30) -> List[str]:
    L = ['# renommer.py -- {}'.format('simulation' if simulation else 'application'), '']
    L += ['| Mesure | Valeur |', '|---|---:|',
          '| Fichiers sans nom avant | {} |'.format(sans_nom_avant),
          '| Noms candidats (dont associes) | {} ({}) |'.format(plan.candidats, plan.associes),
          '| Deja presents dans la base | {} |'.format(plan.deja_nommes),
          '| Hash d\'aucun fichier du projet | {} |'.format(plan.hors_projet),
          '| Hash d\'un fichier deja nomme autrement (collision) | {} |'.format(len(plan.collisions)),
          '| **Refuses : type incoherent** | {} |'.format(len(plan.refus_type)),
          '| Ambigus (plusieurs noms pour un hash) | {} |'.format(len(plan.ambigus)),
          '| Retenus | {} |'.format(len(plan.retenus)),
          '| dont extension jamais vue (sans controle de type) | {} |'.format(len(plan.non_controles))]
    if bilan is not None:
        L += ['| **Fichiers nommes** | {} |'.format(bilan['hashes']),
              '| Noeuds nommes | {} |'.format(bilan['noeuds']),
              '| Fichiers sans nom apres | {} |'.format(sans_nom_avant - bilan['hashes'])]
    L.append('')
    if bilan is not None and bilan['par_extension']:
        L += ['## Noeuds nommes par extension', '', '| Extension | Noeuds |', '|---|---:|']
        L += ['| `{}` | {} |'.format(e or '(sans)', n) for e, n in bilan['par_extension'].most_common()]
        L.append('')
        if bilan.get('non_nommes'):
            L += ['{} hash(es) retenus que DECA n\'a pas nommes (type hors du masque) : {}'.format(
                len(bilan['non_nommes']), ', '.join('{:08x}'.format(h) for h in bilan['non_nommes'][:limite])), '']
        if bilan.get('autres_noms'):
            L += ['{} noeud(s) nommes par DECA sous un autre nom que celui propose (chaine deja connue pour ce hash) :'
                  .format(len(bilan['autres_noms']))]
            L += ['- noeud {} : `{}` au lieu de `{}`'.format(n, v, a) for n, a, v in bilan['autres_noms'][:limite]]
            L.append('')
    if plan.refus_type:
        L += ['## Refuses pour type incoherent (faux positifs probables)', '',
              '| Nom | Hash | Type du fichier | Types attendus |', '|---|---|---|---|']
        L += ['| `{}` | {:08x} | {} | {} |'.format(n, h, fmt_types(t), fmt_types(a))
              for n, h, t, a in plan.refus_type[:limite]]
        if len(plan.refus_type) > limite:
            L.append('| ... {} autres | | | |'.format(len(plan.refus_type) - limite))
        L.append('')
    if plan.ambigus:
        L += ['## Ambigus (laisses sans nom)', '']
        L += ['- {:08x} : {}'.format(h, ', '.join('`{}`'.format(n) for n in noms))
              for h, noms in list(plan.ambigus.items())[:limite]]
        L.append('')
    if plan.collisions:
        L += ['## Collisions avec un fichier deja nomme', '']
        L += ['- `{}` ({:08x})'.format(n, h) for n, h in plan.collisions[:limite]]
        L.append('')
    if plan.non_controles:
        L += ['## Retenus sans controle de type (extension jamais vue)', '']
        L += ['- `{}`'.format(n) for n in plan.non_controles[:limite]]
        L.append('')
    return L


# ---------------------------------------------------------------------------------------------------------
# Ligne de commande
# ---------------------------------------------------------------------------------------------------------

def cmd_ajouter(args) -> int:
    vfs = _projet.open_project(args.projet)
    noms = lire_noms(args.noms)
    if args.exe:
        noms += noms_de_l_exe(vfs)
    if not noms:
        raise SystemExit('ERREUR : aucun nom candidat (--noms ou --exe)')
    etat = NF.Etat.depuis_base(vfs)
    file_assoc = vfs.game_info.file_assoc()
    modele = NF.ModeleTypes(etat, file_assoc)
    plan = preparer(etat, modele, noms, file_assoc, strict=args.strict)
    bilan = None
    if not args.simulation:
        journal = Path(args.projet) / JOURNAL
        bilan = appliquer(vfs, plan, journal, Comm(args.verbeux))
    lignes = rapport(plan, bilan, len(etat.sans_nom), args.simulation)
    texte = '\n'.join(lignes) + '\n'
    if args.rapport:
        Path(args.rapport).write_text(texte, encoding='utf-8')
    print(texte)
    if not args.simulation and bilan and bilan['hashes']:
        print('Journal : {}'.format(Path(args.projet) / JOURNAL))
        print('Pour revenir en arriere : python cotw\\renommer.py annuler {}'.format(args.projet))
    return 0


def cmd_annuler(args) -> int:
    vfs = _projet.open_project(args.projet)
    journal = Path(args.projet) / JOURNAL
    if not journal.is_file():
        print('Rien a annuler ({} absent).'.format(journal))
        return 0
    b = annuler(vfs, journal)
    print('{} noeud(s) remis sans nom, {} reference(s) et {} chaine(s) supprimees ({} chaine(s) gardees : '
          'referencees ailleurs ; {} noeud(s) renommes depuis, laisses tels quels).'.format(
              b['noeuds'], b['references'], b['chaines'], b['chaines_gardees'], b['noeuds_modifies_depuis']))
    return 0


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(errors='replace')
    ap = argparse.ArgumentParser(description='Nomme des fichiers sans nom d\'un projet DECA, sans rescanner.')
    sub = ap.add_subparsers(dest='commande', required=True)
    a = sub.add_parser('ajouter', help='donner des noms aux fichiers sans nom')
    a.add_argument('projet', help='projet DECA (dossier contenant db\\core.db)')
    a.add_argument('--noms', action='append', default=[], help='fichier de noms (un chemin virtuel par ligne)')
    a.add_argument('--exe', action='store_true', help='ajouter les chaines en forme de chemin de l\'executable du jeu')
    a.add_argument('--strict', action='store_true', help='refuser les noms dont l\'extension n\'a jamais ete vue')
    a.add_argument('--simulation', action='store_true', help='rapport seulement, rien n\'est ecrit')
    a.add_argument('--rapport', help='ecrire aussi le rapport dans ce fichier (markdown)')
    a.add_argument('--verbeux', action='store_true', help='afficher le journal de DECA')
    a.set_defaults(func=cmd_ajouter)
    u = sub.add_parser('annuler', help='annuler tous les renommages du journal')
    u.add_argument('projet')
    u.set_defaults(func=cmd_annuler)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    sys.exit(main())
