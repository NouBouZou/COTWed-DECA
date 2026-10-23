#!/usr/bin/env python3
"""
audit_squelettes.py -- les maillages a os de COTW suivent-ils leur squelette ? (lecture seule, DECA ferme)

Pourquoi (2026-10-23) : 17-maillages montre que les indices d'os des 11 198 maillages a os restent dans la table
BoneIndexLookup de chaque maillage et que leurs poids somment a 1. Reste a savoir si cette table renvoie aux bons os
du squelette (.bsk, Havok), ce que l'export glTF suppose (JOINTS_0 = BoneIndexLookup[indice], skin.joints dans
l'ordre du squelette). Un modloader d'armes et d'animaux en depend.

Un maillage ne cite pas son squelette : ce sont les entites RTPC (.epe, .blo...) qui associent un modele et un
squelette (proprietes model_skeleton / skeleton, heritees par les noeuds enfants). L'audit les lit comme l'export
glTF de DECA (deca.export_import_rtpc.rtpc_node_model_skeleton), puis pour chaque couple (modele, squelette)
distinct :
  1. squelette lu par deca.ff_havok (pose de reference, matrices dans l'espace du modele) ;
  2. .modelc -> .meshc (deca.ff_adf_amf_gltf.load_meshc, formats bruts) ; premier groupe de LOD complet ;
  3. pour chaque sommet a os : os dominant (plus grand poids) passe par BoneIndexLookup ;
     - hors_squelette : part des sommets dont l'os depasse le squelette (0 attendu) ;
     - rang5 : part des sommets (2 000 au plus par maillage) dont l'os dominant est parmi les 5 os les plus proches
       (distance au segment articulation -> articulation de chaque enfant ; a l'articulation pour un os terminal) ;
     - rang5_temoin : la meme part pour un os tire au hasard : ce que donnerait une table fausse ;
     - distance : mediane de la distance a l'os dominant, en part de la taille du squelette.
Si la table et la pose sont bien lues, rang5 est proche de 1 et nettement au-dessus du temoin.

Sorties (dossier --sortie) : squelettes.csv (un couple par ligne), squelettes.md (statuts, quartiles, couples les
moins vraisemblables). Les modeles des dossiers jc* (Just Cause) sont ecartes.

Usage (DECA ferme) :
    python cotw\\audit_squelettes.py D:\\dev\\work\\hp --sortie D:\\dev\\audits\\18-squelettes
"""
import argparse
import collections
import csv
import multiprocessing
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _projet  # noqa: E402

RANG = 5                    # os les plus proches parmi lesquels l'os dominant doit se trouver
SOMMETS_PAR_MAILLAGE = 2000  # sommets tires par maillage pour rang5 et la distance
GRAINE = 1


class Statut(Exception):
    def __init__(self, statut: str, detail: str = ''):
        Exception.__init__(self, statut, detail)
        self.statut, self.detail = statut, detail


def _txt(b) -> str:
    return _projet.to_text(b) or ''


# ---------------------------------------------------------------------------------------------------------------
# 1. Couples (modele, squelette) des entites RTPC
# ---------------------------------------------------------------------------------------------------------------

def couples_rtpc(vfs, data: bytes) -> List[Tuple[bytes, bytes, bytes]]:
    """(modele, squelette, classe) de chaque noeud d'un RTPC qui cite un modele avec un squelette (herite ou non),
    selon la regle de l'export glTF."""
    from deca.ff_rtpc_write import rtpc_from_bytes
    from deca.export_import_rtpc import rtpc_node_model_skeleton
    rtpc = rtpc_from_bytes(data)
    out = []
    pile = [(rtpc.root_node, None)]
    while pile:
        node, squelette = pile.pop()
        try:
            classe, modele, squelette = rtpc_node_model_skeleton(node, vfs, squelette)
        except (KeyError, TypeError):    # classe connue sans sa propriete de modele : l'export echouerait aussi
            modele = None
        else:
            if modele is not None and squelette:
                out.append((bytes(modele), bytes(squelette), bytes(classe or b'')))
        pile.extend((c, squelette) for c in node.child_table)
    return out


# ---------------------------------------------------------------------------------------------------------------
# 2. Mesure d'un maillage contre un squelette
# ---------------------------------------------------------------------------------------------------------------

class Squelette:
    """Pose de reference d'un .bsk : articulations dans l'espace du modele, segments os -> enfants."""

    def __init__(self, bones: List[dict]):
        from deca.ff_adf_amf_gltf import skeleton_matrices
        self.noms = [b['name'] for b in bones]
        self.parents = [b.get('parent') for b in bones]
        _local, world = skeleton_matrices(bones)
        self.articulations = np.array([w[0:3, 3] for w in world], dtype=np.float64).reshape(-1, 3)
        n = len(bones)
        enfants: List[List[int]] = [[] for _ in range(n)]
        for i, p in enumerate(self.parents):
            if p is not None and 0 <= p < i:
                enfants[p].append(i)
        debuts, fins, proprio = [], [], []
        for b in range(n):
            for c in (enfants[b] or [b]):
                debuts.append(self.articulations[b])
                fins.append(self.articulations[c])
                proprio.append(b)
        self.debuts = np.array(debuts, dtype=np.float64).reshape(-1, 3)
        self.fins = np.array(fins, dtype=np.float64).reshape(-1, 3)
        self.proprio = np.array(proprio, dtype=np.int64)
        if n:
            ext = self.articulations.max(axis=0) - self.articulations.min(axis=0)
            self.taille = max(float(np.linalg.norm(ext)), 1e-6)
        else:
            self.taille = 1.0

    def __len__(self):
        return len(self.noms)

    def distances(self, v: np.ndarray) -> np.ndarray:
        """(N, os) distance de chaque point a chaque os (au plus proche de ses segments)."""
        d = np.full((len(v), len(self)), np.inf)
        for a, b, o in zip(self.debuts, self.fins, self.proprio):
            ab = b - a
            l2 = float(ab @ ab)
            t = np.clip((v - a) @ ab / l2, 0.0, 1.0) if l2 > 0 else np.zeros(len(v))
            dist = np.linalg.norm(v - (a + t[:, None] * ab), axis=1)
            d[:, o] = np.minimum(d[:, o], dist)
        return d


def mesurer_peau(mesh, vertex_buffers, sq: Squelette, rng) -> Optional[Dict[str, object]]:
    """Mesures d'un AmfMesh (formats bruts) a os contre un squelette ; None si le maillage n'a pas d'os.
    Cles : sommets (a poids non nuls), hors (os dominant au-dela du squelette), table_max (plus grand os cite par
    la table), tires, rang (os dominant parmi les RANG plus proches), temoin (idem pour un os au hasard),
    distances (liste, en part de la taille du squelette)."""
    import geometrie_maillages as GM
    sa_i = GM._attribut(mesh, b'AmfUsage_BoneIndex', b'AmfFormat_R8G8B8A8_UINT')
    sa_w = GM._attribut(mesh, b'AmfUsage_BoneWeight', b'AmfFormat_R8G8B8A8_UNORM')
    if sa_i is None or sa_w is None:
        return None
    pos = GM.positions(mesh, vertex_buffers)
    idx = GM._flux(mesh, vertex_buffers, sa_i, '4u1')
    w = GM._flux(mesh, vertex_buffers, sa_w, '4u1')
    if pos is None or idx is None or w is None:
        return None
    w = w.astype(np.float64)
    valides = w.sum(axis=1) > 0
    local = idx[np.arange(len(idx)), np.argmax(w, axis=1)].astype(np.int64)
    table = np.asarray(list(mesh.boneIndexLookup or []), dtype=np.int64)
    if len(table):
        hors_table = local >= len(table)
        dominant = np.where(hors_table, -1, table[np.minimum(local, len(table) - 1)])
    else:
        hors_table = np.zeros(len(local), dtype=bool)
        dominant = local
    n_os = len(sq)
    hors = valides & (hors_table | (dominant >= n_os) | (dominant < 0))
    out = {'sommets': int(valides.sum()), 'hors': int(hors.sum()),
           'table_max': int(table.max()) if len(table) else int(local.max(initial=0)),
           'tires': 0, 'rang': 0, 'temoin': 0, 'distances': []}
    choix = np.flatnonzero(valides & ~hors)
    if not len(choix) or not n_os:
        return out
    if len(choix) > SOMMETS_PAR_MAILLAGE:
        choix = np.sort(rng.choice(choix, SOMMETS_PAR_MAILLAGE, replace=False))
    d = sq.distances(pos[choix])
    dom = dominant[choix]
    d_dom = d[np.arange(len(choix)), dom]
    rang = np.sum(d < d_dom[:, None], axis=1)
    hasard = rng.integers(0, n_os, size=len(choix))
    d_has = d[np.arange(len(choix)), hasard]
    rang_h = np.sum(d < d_has[:, None], axis=1)
    out.update(tires=int(len(choix)), rang=int(np.sum(rang < RANG)), temoin=int(np.sum(rang_h < RANG)),
               distances=list(d_dom / sq.taille))
    return out


class Resultat:
    COLONNES = ['modele', 'squelette', 'statut', 'detail', 'sources', 'classes', 'exemple', 'os', 'maillages_os',
                'table_max', 'sommets', 'hors_squelette', 'rang5', 'rang5_temoin', 'distance']

    def __init__(self, modele: bytes, squelette: bytes):
        self.cle = (modele, squelette)
        self.modele, self.squelette = _txt(modele), _txt(squelette)
        self.statut, self.detail = 'ok', ''
        self.sources, self.classes, self.exemple = 0, '', ''
        self.os = 0
        self.maillages_os = 0
        self.table_max: Optional[int] = None
        self.sommets = 0
        self.hors_squelette: Optional[float] = None
        self.rang5: Optional[float] = None
        self.rang5_temoin: Optional[float] = None
        self.distance: Optional[float] = None

    def ligne(self):
        def f(v):
            return '' if v is None else ('{:.3f}'.format(v) if isinstance(v, float) else v)
        return [f(getattr(self, k)) for k in self.COLONNES]


_SQUELETTES: Dict[bytes, object] = {}


def charger_squelette(vfs, chemin: bytes) -> Squelette:
    """Le .bsk d'un chemin de squelette, comme l'export glTF (extension remplacee par .bsk) ; garde en memoire."""
    from deca.path import UniPath
    from deca.ff_adf_amf_gltf import node_of_type, TYPES_HAVOK_NODE
    from deca import ff_havok
    v_path = UniPath.splitext(chemin)[0] + b'.bsk'
    if v_path in _SQUELETTES:
        sq = _SQUELETTES[v_path]
        if isinstance(sq, Statut):
            raise sq
        return sq
    try:
        nodes = node_of_type(vfs, v_path, TYPES_HAVOK_NODE)
        if not nodes:
            raise Statut('squelette_absent', _txt(v_path))
        try:
            sks = ff_havok.skeletons(_projet.read_node(vfs, nodes[0]))
        except ff_havok.EHavok as exc:
            raise Statut('squelette_illisible', '{}: {}'.format(_txt(v_path), exc))
        if not sks or not sks[0]['bones']:
            raise Statut('squelette_illisible', '{}: pas de hkaSkeleton'.format(_txt(v_path)))
        sq = Squelette(sks[0]['bones'])
    except Statut as st:
        _SQUELETTES[v_path] = st
        raise
    _SQUELETTES[v_path] = sq
    return sq


def groupe_complet(header, buffers):
    """Premier groupe de LOD dont tous les tampons sont presents (le .hrmeshc peut manquer au LOD 0)."""
    for lg in header.lodGroups:
        if all(m.indexBufferIndex < len(buffers.indexBuffers)
               and all(i < len(buffers.vertexBuffers) for i in m.vertexBufferIndices) for m in lg.meshes):
            return lg
    return None


def auditer_couple(vfs, adf_db, modele: bytes, squelette: bytes) -> Resultat:
    from deca.ff_adf_amf import AmfModel
    from deca.ff_adf_amf_gltf import node_of_type, TYPES_ADF_NODE, load_meshc
    r = Resultat(modele, squelette)
    etape = 'squelette'
    try:
        sq = charger_squelette(vfs, squelette)
        r.os = len(sq)
        etape = 'modele'
        nodes = node_of_type(vfs, modele, TYPES_ADF_NODE)
        if not nodes:
            raise Statut('modele_absent')
        try:
            adf = adf_db.read_node(vfs, nodes[0])
        except Exception as exc:  # noqa: BLE001
            raise Statut('modele_illisible', '{}: {}'.format(type(exc).__name__, str(exc)[:120]))
        if adf is None:
            raise Statut('modele_illisible')
        model = AmfModel(adf, adf.table_instance_full_values[0])
        etape = 'maillage'
        if not model.mesh or not node_of_type(vfs, model.mesh, TYPES_ADF_NODE):
            raise Statut('maillage_absent', _txt(model.mesh))
        header, buffers = load_meshc(vfs, adf_db, model.mesh)
        lg = groupe_complet(header, buffers)
        if lg is None:
            raise Statut('tampon_absent', _txt(model.mesh))
        etape = 'mesure'
        rng = np.random.default_rng(GRAINE)
        hors = tires = rang = temoin = 0
        distances: List[float] = []
        for m in lg.meshes:
            res = mesurer_peau(m, buffers.vertexBuffers, sq, rng)
            if res is None:
                continue
            r.maillages_os += 1
            r.sommets += res['sommets']
            hors += res['hors']
            r.table_max = max(r.table_max or 0, res['table_max'])
            tires += res['tires']
            rang += res['rang']
            temoin += res['temoin']
            distances += res['distances']
        if not r.maillages_os:
            raise Statut('sans_os', _txt(model.mesh))
        r.hors_squelette = hors / r.sommets if r.sommets else 0.0
        if tires:
            r.rang5, r.rang5_temoin = rang / tires, temoin / tires
            r.distance = float(np.median(distances))
        if hors:
            r.statut = 'os_hors_squelette'
    except Statut as st:
        r.statut, r.detail = st.statut, st.detail
    except Exception as exc:  # noqa: BLE001 - un couple en echec est compte, pas fatal
        r.statut, r.detail = 'echec_' + etape, '{}: {}'.format(type(exc).__name__, str(exc)[:200])
    return r


# ---------------------------------------------------------------------------------------------------------------
# Travail en parallele (meme organisation que les autres audits)
# ---------------------------------------------------------------------------------------------------------------

_VFS = None
_ADF_DB = None


def _init_travail(projet: str):
    global _VFS, _ADF_DB
    from deca.ff_adf import AdfDatabase
    _VFS = _projet.open_project(projet)
    _ADF_DB = AdfDatabase(_VFS)


def _lot_rtpc(lot: List[Tuple[int, str, str]]):
    """(couples, fichiers lus, echecs) d'un lot de RTPC."""
    couples, lus, echecs = [], 0, 0
    for uid, chemin, _t in lot:
        try:
            for m, s, c in couples_rtpc(_VFS, _projet.read_node(_VFS, _VFS.node_where_uid(uid))):
                couples.append((m, s, c, chemin))
            lus += 1
        except Exception:  # noqa: BLE001 - RTPC en ligne (.smodc) ou illisible : pas d'entite
            echecs += 1
    return couples, lus, echecs


def _lot_couples(lot: List[Tuple[bytes, bytes]]) -> List[Resultat]:
    return [auditer_couple(_VFS, _ADF_DB, m, s) for m, s in lot]


def _carte(projet: str, fonction, lots, processus: int, nom: str, t0: float):
    if processus <= 1:
        _init_travail(projet)
        for lot in lots:
            yield fonction(lot)
        return
    with multiprocessing.Pool(processus, initializer=_init_travail, initargs=(projet,)) as pool:
        for i, res in enumerate(pool.imap_unordered(fonction, lots)):
            if (i + 1) % 25 == 0 or i + 1 == len(lots):
                print('  {} : {} / {} lots ({:.0f} s)'.format(nom, i + 1, len(lots), time.time() - t0), flush=True)
            yield res


# ---------------------------------------------------------------------------------------------------------------
# Rapport
# ---------------------------------------------------------------------------------------------------------------

def ecrire(sortie: Path, resultats: List[Resultat], projet: str, duree: float, n_rtpc: int, echecs_rtpc: int,
           jc: int) -> List[str]:
    sortie.mkdir(parents=True, exist_ok=True)
    resultats = sorted(resultats, key=lambda r: (r.statut != 'ok', r.modele, r.squelette))
    with open(sortie / 'squelettes.csv', 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f, delimiter=';')
        w.writerow(Resultat.COLONNES)
        for r in resultats:
            w.writerow(r.ligne())
    statuts = collections.Counter(r.statut for r in resultats)
    mesures = [r for r in resultats if r.rang5 is not None]
    L = ['# Audit des squelettes (maillages a os contre leur .bsk)', '',
         '- Projet : `{}` ; {} RTPC lus ({} illisibles ou sans entite lisible) ; duree : {:.0f} s'.format(
             projet, n_rtpc, echecs_rtpc, duree),
         '- **Couples (modele, squelette) distincts : {}** ; {} ecartes (modeles herites de Just Cause, dossiers jc*)'
         .format(len(resultats), jc),
         '- Squelettes distincts : {} ; modeles distincts : {}'.format(
             len({r.squelette for r in resultats}), len({r.modele for r in resultats})), '',
         '| Statut | Couples |', '|---|---:|']
    L += ['| {} | {} |'.format(s, n) for s, n in statuts.most_common()]
    L += ['', '## Vraisemblance', '',
          'rang5 : part des sommets dont l\'os dominant est parmi les {} os les plus proches (1 = juste) ; temoin : '
          'la meme part pour un os tire au hasard. Distance : mediane de la distance a l\'os dominant, en part de la '
          'taille du squelette. Os hors squelette : part des sommets dont l\'os depasse le squelette (0 '
          'attendu).'.format(RANG), '',
          '| Mesure | Couples | Q1 | Mediane | Q3 | Part >= 0,8 (distance : <= 0,1 ; hors : = 0) |',
          '|---|---:|---:|---:|---:|---:|']
    for k in ('rang5', 'rang5_temoin', 'distance', 'hors_squelette'):
        v = np.array([getattr(r, k) for r in resultats if getattr(r, k) is not None], dtype=np.float64)
        if not len(v):
            continue
        if k == 'distance':
            part = np.mean(v <= 0.1)
        elif k == 'hors_squelette':
            part = np.mean(v == 0.0)
        else:
            part = np.mean(v >= 0.8)
        q1, med, q3 = np.percentile(v, [25, 50, 75])
        L.append('| {} | {} | {:.3f} | {:.3f} | {:.3f} | {:.1f} % |'.format(k, len(v), q1, med, q3, 100 * part))
    if mesures:
        L += ['', '## Couples les moins vraisemblables (rang5)', '', '| Modele | Squelette | Os | rang5 | temoin |',
              '|---|---|---:|---:|---:|']
        for r in sorted(mesures, key=lambda r: r.rang5)[:20]:
            L.append('| `{}` | `{}` | {} | {:.3f} | {:.3f} |'.format(r.modele, r.squelette, r.os, r.rang5,
                                                                   r.rang5_temoin))
    autres = [r for r in resultats if r.statut not in ('ok',)]
    if autres:
        L += ['', '## Exemples par statut', '']
        par = collections.defaultdict(list)
        for r in autres:
            par[r.statut].append(r)
        for s, rs in sorted(par.items()):
            L.append('- **{}** ({}) : {}'.format(s, len(rs), ' ; '.join(
                '`{}` / `{}`{}'.format(r.modele, r.squelette, ' ({})'.format(r.detail) if r.detail else '')
                for r in rs[:3])))
    (sortie / 'squelettes.md').write_text('\n'.join(L) + '\n', encoding='utf-8')
    return L


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('projet', help='projet DECA (dossier contenant db\\core.db)')
    ap.add_argument('--sortie', required=True, help='dossier des resultats')
    ap.add_argument('--echantillon', type=int, default=0, help='nombre de RTPC tires au hasard (0 = tous)')
    ap.add_argument('--processus', type=int, default=max(1, (os.cpu_count() or 2) * 3 // 4),
                    help='processus de lecture (defaut : 3/4 des coeurs)')
    ap.add_argument('--lot', type=int, default=50, help='fichiers ou couples par lot envoye a un processus')
    args = ap.parse_args(argv)
    import audit_noms_internes as ANI
    from audit_maillages import herite_just_cause
    t0 = time.time()
    vfs = _projet.open_project(args.projet)
    rtpc = ANI.selectionner(vfs, ('rtpc',), args.echantillon)
    print('{} RTPC'.format(len(rtpc)), flush=True)
    sources: Dict[Tuple[bytes, bytes], list] = {}
    n_lus = n_echecs = 0
    lots = [rtpc[i:i + args.lot] for i in range(0, len(rtpc), args.lot)]
    for couples, lus, echecs in _carte(args.projet, _lot_rtpc, lots, args.processus, 'RTPC', t0):
        n_lus += lus
        n_echecs += echecs
        for m, s, c, chemin in couples:
            e = sources.setdefault((m, s), [0, collections.Counter(), chemin])
            e[0] += 1
            e[1][_txt(c)] += 1
    jc = [k for k in sources if herite_just_cause(_txt(k[0]))]
    for k in jc:
        del sources[k]
    cles = sorted(sources)
    print('{} couples (modele, squelette)'.format(len(cles)), flush=True)
    lots = [cles[i:i + args.lot] for i in range(0, len(cles), args.lot)]
    resultats: List[Resultat] = []
    for res in _carte(args.projet, _lot_couples, lots, args.processus, 'couples', t0):
        resultats += res
    for r in resultats:
        n, classes, exemple = sources[r.cle]
        r.sources, r.exemple = n, exemple
        r.classes = ' '.join('{}:{}'.format(c or '?', k) for c, k in classes.most_common(3))
    L = ecrire(Path(args.sortie), resultats, args.projet, time.time() - t0, n_lus, n_echecs, len(jc))
    fin = L.index('## Couples les moins vraisemblables (rang5)') if '## Couples les moins vraisemblables (rang5)' in L \
        else len(L)
    texte = '\n'.join(L[:fin]) + '\n\nResultats : {}'.format(Path(args.sortie))
    enc = sys.stdout.encoding or 'utf-8'
    print(texte.encode(enc, errors='replace').decode(enc))
    return 0


if __name__ == '__main__':
    multiprocessing.freeze_support()
    sys.exit(main())
