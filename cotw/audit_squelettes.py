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

Pieces rapportees (2026-10-28, 18-squelettes) : les CModelAttachementWeaponComponent des armes (cartouches, douilles,
chargeurs, rails, hausses) pendent a un os nomme dans l'entite (propriete 0xdd3acc80, nom inconnu : 'ammo',
'ammo_used_03', 'weapon_handle'...) ; leurs sommets sont donnes dans le repere de cet os et la matrice 'world' de
l'entite est relative a lui. Elles sont mesurees a leur place : monde de l'os fois matrice locale. Une meme piece peut
pendre a plusieurs os (six cartouches d'un barillet) : un couple par os.

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

def couples_rtpc(vfs, data: bytes) -> List[Tuple[bytes, bytes, bytes, bytes, tuple]]:
    """(modele, squelette, classe, os d'attache, matrice locale) de chaque noeud d'un RTPC qui cite un modele avec
    un squelette (herite ou non), selon la regle de l'export glTF. Os d'attache (b'' sinon) et matrice locale (16
    flottants par colonnes, () sinon) : pieces rapportees des armes (export_import_rtpc.rtpc_node_attach)."""
    from deca.ff_rtpc_write import rtpc_from_bytes
    from deca.export_import_rtpc import rtpc_node_model_skeleton, rtpc_node_attach
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
                os_attache, local = rtpc_node_attach(node, vfs)
                out.append((bytes(modele), bytes(squelette), bytes(classe or b''), os_attache or b'',
                            tuple(round(float(v), 6) for v in local) if os_attache and local else ()))
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
        self.mondes = np.array(world, dtype=np.float64).reshape(-1, 4, 4)
        self.articulations = self.mondes[:, 0:3, 3].copy()
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


def mesurer_peau(mesh, vertex_buffers, sq: Squelette, rng, placement: Optional[np.ndarray] = None
                 ) -> Optional[Dict[str, object]]:
    """Mesures d'un AmfMesh (formats bruts) a os contre un squelette ; None si le maillage n'a pas d'os.
    placement : matrice 4x4 appliquee aux sommets avant la mesure (piece rapportee : monde de l'os d'attache fois la
    matrice locale de l'entite).
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
    brut = pos
    if placement is not None:
        pos = pos @ placement[0:3, 0:3].T + placement[0:3, 3]
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
           'tires': 0, 'rang': 0, 'temoin': 0, 'rang_local': 0, 'distances': [], 'groupes': collections.Counter(),
           'pivot': 0, 'pivot_temoin': 0}
    choix = np.flatnonzero(valides & ~hors)
    if not len(choix) or not n_os:
        return out
    # pieces rigides (armes) : les sommets d'un meme os dominant forment une piece ; son articulation (pivot) doit
    # tomber dans la boite de la piece, elargie de 5 % de la taille du squelette ; temoin : un os au hasard
    marge = 0.05 * sq.taille
    for b in np.unique(dominant[choix]):
        pts = pos[choix[dominant[choix] == b]]
        lo, hi = pts.min(axis=0) - marge, pts.max(axis=0) + marge
        n = len(pts)
        out['groupes'][int(b)] += n
        if np.all((sq.articulations[b] >= lo) & (sq.articulations[b] <= hi)):
            out['pivot'] += n
        h = int(rng.integers(0, n_os))
        if np.all((sq.articulations[h] >= lo) & (sq.articulations[h] <= hi)):
            out['pivot_temoin'] += n
    if len(choix) > SOMMETS_PAR_MAILLAGE:
        choix = np.sort(rng.choice(choix, SOMMETS_PAR_MAILLAGE, replace=False))
    d = sq.distances(pos[choix])
    dom = dominant[choix]
    d_dom = d[np.arange(len(choix)), dom]
    rang = np.sum(d < d_dom[:, None], axis=1)
    hasard = rng.integers(0, n_os, size=len(choix))
    d_has = d[np.arange(len(choix)), hasard]
    rang_h = np.sum(d < d_has[:, None], axis=1)
    # hypothese 'repere de l'os' : le sommet serait donne dans le repere de son os dominant (piece rapportee)
    m = sq.mondes[dom]
    v_loc = np.einsum('nij,nj->ni', m[:, 0:3, 0:3], brut[choix]) + m[:, 0:3, 3]     # sommets du fichier, non places
    d_loc = sq.distances(v_loc)
    rang_l = np.sum(d_loc < d_loc[np.arange(len(choix)), dom][:, None], axis=1)
    out.update(tires=int(len(choix)), rang=int(np.sum(rang < RANG)), temoin=int(np.sum(rang_h < RANG)),
               rang_local=int(np.sum(rang_l < RANG)), distances=list(d_dom / sq.taille))
    return out


def detail_maillage(mesh, vertex_buffers, n_max: int = 400) -> Optional[dict]:
    """Pour --details : table, boite et un echantillon de sommets (position, 4 os passes par la table, poids)."""
    import geometrie_maillages as GM
    sa_i = GM._attribut(mesh, b'AmfUsage_BoneIndex', b'AmfFormat_R8G8B8A8_UINT')
    sa_w = GM._attribut(mesh, b'AmfUsage_BoneWeight', b'AmfFormat_R8G8B8A8_UNORM')
    pos = GM.positions(mesh, vertex_buffers)
    if pos is None:
        return None
    out = {'sommets': int(mesh.vertexCount), 'table': [int(v) for v in (mesh.boneIndexLookup or [])],
           'min': pos.min(axis=0).tolist(), 'max': pos.max(axis=0).tolist(),
           'proprietes': {str(k): (v if isinstance(v, (int, float, str)) else repr(v))
                          for k, v in (mesh.meshProperties or {}).items()}}
    pas = max(1, len(pos) // n_max)
    sel = np.arange(0, len(pos), pas)[:n_max]
    ech = {'p': pos[sel].round(5).tolist()}
    if sa_i is not None and sa_w is not None:
        idx = GM._flux(mesh, vertex_buffers, sa_i, '4u1')
        w = GM._flux(mesh, vertex_buffers, sa_w, '4u1')
        if idx is not None and w is not None:
            table = out['table']
            ech['os'] = [[table[i] if i < len(table) else -1 - int(i) for i in row] if table else row.tolist()
                         for row in idx[sel].astype(int)]
            ech['w'] = w[sel].astype(int).tolist()
    out['echantillon'] = ech
    return out


class Resultat:
    COLONNES = ['modele', 'squelette', 'attache', 'statut', 'detail', 'sources', 'classes', 'exemple', 'os',
                'maillages_os',
                'table_max', 'sommets', 'hors_squelette', 'rang5', 'rang5_temoin', 'distance', 'rang5_repere_os',
                'pivot', 'pivot_temoin', 'os_dominants', 'principaux', 'rang5_attache', 'rang5_attache_temoin']

    def __init__(self, modele: bytes, squelette: bytes, attache: bytes = b'', local: tuple = ()):
        self.cle = (modele, squelette, attache, local)
        self.modele, self.squelette, self.attache = _txt(modele), _txt(squelette), _txt(attache)
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
        self.rang5_repere_os: Optional[float] = None
        self.pivot: Optional[float] = None
        self.pivot_temoin: Optional[float] = None
        self.os_dominants = 0
        self.principaux = ''
        self.rang5_attache: Optional[float] = None
        self.rang5_attache_temoin: Optional[float] = None

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


def ecrire_details(dossier: Path, r: 'Resultat', sq: Squelette, maillage: bytes, maillages: List[Optional[dict]]):
    import json
    dossier.mkdir(parents=True, exist_ok=True)
    nom = r.modele.replace('/', '_').replace('\\', '_') + '__' + Path(r.squelette).stem + (
        '__' + r.attache if r.attache else '') + '.json'
    d = {'modele': r.modele, 'squelette': r.squelette, 'attache': r.attache, 'local': list(r.cle[3]),
         'maillage': _txt(maillage), 'taille': sq.taille,
         'os': [{'nom': n, 'parent': p, 'monde': m.round(6).tolist()}
                for n, p, m in zip(sq.noms, sq.parents, sq.mondes)],
         'maillages': maillages}
    (dossier / nom).write_text(json.dumps(d), encoding='utf-8')


def groupe_complet(header, buffers):
    """Premier groupe de LOD dont tous les tampons sont presents (le .hrmeshc peut manquer au LOD 0)."""
    for lg in header.lodGroups:
        if all(m.indexBufferIndex < len(buffers.indexBuffers)
               and all(i < len(buffers.vertexBuffers) for i in m.vertexBufferIndices) for m in lg.meshes):
            return lg
    return None


def mesurer_attache(mesh, vertex_buffers, sq: Squelette, placement: np.ndarray, os_attache: int, rng
                    ) -> Optional[Tuple[int, int, int]]:
    """Piece rapportee (avec ou sans flux d'os) : sommets places (monde de l'os d'attache fois matrice locale), puis
    (tires, os d'attache parmi les RANG os les plus proches, idem pour un os au hasard). C'est l'os d'attache qui
    compte : l'export pose la piece rigide sur lui, et l'os de la peau du maillage n'est qu'un modele (une meme
    cartouche, peinte sur bullet_01, pend a bullet_02... bullet_05). None sans positions."""
    import geometrie_maillages as GM
    pos = GM.positions(mesh, vertex_buffers)
    if pos is None or not len(pos) or not len(sq):
        return None
    pos = pos @ placement[0:3, 0:3].T + placement[0:3, 3]
    choix = np.arange(len(pos))
    if len(choix) > SOMMETS_PAR_MAILLAGE:
        choix = np.sort(rng.choice(choix, SOMMETS_PAR_MAILLAGE, replace=False))
    d = sq.distances(pos[choix])
    rang = np.sum(d < d[:, os_attache][:, None], axis=1)
    hasard = rng.integers(0, len(sq), size=len(choix))
    rang_h = np.sum(d < d[np.arange(len(choix)), hasard][:, None], axis=1)
    return len(choix), int(np.sum(rang < RANG)), int(np.sum(rang_h < RANG))


def placement_attache(sq: Squelette, attache: bytes, local: tuple) -> np.ndarray:
    """Monde de l'os d'attache fois la matrice locale de l'entite (16 flottants par colonnes ; identite sinon)."""
    nom = _txt(attache)
    if nom not in sq.noms:
        raise Statut('os_attache_absent', nom)
    m = np.array(local, dtype=np.float64).reshape(4, 4).T if len(local) == 16 else np.eye(4)
    return sq.mondes[sq.noms.index(nom)] @ m


def auditer_couple(vfs, adf_db, modele: bytes, squelette: bytes, attache: bytes = b'', local: tuple = (),
                   details: Optional[str] = None) -> Resultat:
    """attache, local : piece rapportee, mesuree a sa place (placement_attache). details : dossier ou ecrire
    <modele>.json (squelette et sommets) pour une etude a la main."""
    from deca.ff_adf_amf import AmfModel
    from deca.ff_adf_amf_gltf import node_of_type, TYPES_ADF_NODE, load_meshc
    r = Resultat(modele, squelette, attache, local)
    etape = 'squelette'
    try:
        sq = charger_squelette(vfs, squelette)
        r.os = len(sq)
        placement = placement_attache(sq, attache, local) if attache else None
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
        hors = tires = rang = temoin = rang_local = pivot = pivot_t = 0
        distances: List[float] = []
        groupes: collections.Counter = collections.Counter()
        for m in lg.meshes:
            res = mesurer_peau(m, buffers.vertexBuffers, sq, rng, placement)
            if res is None:
                continue
            r.maillages_os += 1
            r.sommets += res['sommets']
            hors += res['hors']
            r.table_max = max(r.table_max or 0, res['table_max'])
            tires += res['tires']
            rang += res['rang']
            temoin += res['temoin']
            rang_local += res['rang_local']
            distances += res['distances']
            groupes.update(res['groupes'])
            pivot += res['pivot']
            pivot_t += res['pivot_temoin']
        if placement is not None:
            a_tires = a_rang = a_temoin = 0
            os_attache = sq.noms.index(_txt(attache))
            for m in lg.meshes:
                res_a = mesurer_attache(m, buffers.vertexBuffers, sq, placement, os_attache, rng)
                if res_a is not None:
                    a_tires += res_a[0]
                    a_rang += res_a[1]
                    a_temoin += res_a[2]
            if a_tires:
                r.rang5_attache, r.rang5_attache_temoin = a_rang / a_tires, a_temoin / a_tires
        if details:
            ecrire_details(Path(details), r, sq, model.mesh, [detail_maillage(m, buffers.vertexBuffers)
                                                              for m in lg.meshes])
        if not r.maillages_os:
            if r.rang5_attache is not None:
                return r        # piece rapportee sans flux d'os (hausse, munition) : mesuree par son os d'attache
            raise Statut('sans_os', _txt(model.mesh))
        r.hors_squelette = hors / r.sommets if r.sommets else 0.0
        if tires:
            r.rang5, r.rang5_temoin = rang / tires, temoin / tires
            r.rang5_repere_os = rang_local / tires
            r.distance = float(np.median(distances))
            total = sum(groupes.values())
            r.pivot, r.pivot_temoin = pivot / total, pivot_t / total
            r.os_dominants = len(groupes)
            r.principaux = ' '.join('{}:{:.0f}%'.format(sq.noms[b], 100.0 * n / total)
                                    for b, n in groupes.most_common(3))
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
_DETAILS = {'motif': None, 'dossier': None}


def _init_travail(projet: str, motif: Optional[str] = None, dossier: Optional[str] = None):
    global _VFS, _ADF_DB
    from deca.ff_adf import AdfDatabase
    _VFS = _projet.open_project(projet)
    _ADF_DB = AdfDatabase(_VFS)
    _DETAILS['motif'], _DETAILS['dossier'] = motif, dossier


def _lot_rtpc(lot: List[Tuple[int, str, str]]):
    """(couples, fichiers lus, echecs) d'un lot de RTPC."""
    couples, lus, echecs = [], 0, 0
    for uid, chemin, _t in lot:
        try:
            for m, s, c, a, l in couples_rtpc(_VFS, _projet.read_node(_VFS, _VFS.node_where_uid(uid))):
                couples.append((m, s, c, a, l, chemin))
            lus += 1
        except Exception:  # noqa: BLE001 - RTPC en ligne (.smodc) ou illisible : pas d'entite
            echecs += 1
    return couples, lus, echecs


def _lot_couples(lot: List[Tuple[bytes, bytes, bytes, tuple]]) -> List[Resultat]:
    import re
    motif = _DETAILS['motif']
    return [auditer_couple(_VFS, _ADF_DB, m, s, a, l,
                           _DETAILS['dossier'] if motif and re.search(motif, _txt(m)) else None)
            for m, s, a, l in lot]


def _carte(projet: str, fonction, lots, processus: int, nom: str, t0: float, motif: Optional[str] = None,
           dossier: Optional[str] = None):
    if processus <= 1:
        _init_travail(projet, motif, dossier)
        for lot in lots:
            yield fonction(lot)
        return
    with multiprocessing.Pool(processus, initializer=_init_travail, initargs=(projet, motif, dossier)) as pool:
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
    resultats = sorted(resultats, key=lambda r: (r.statut != 'ok', r.modele, r.squelette, r.attache))
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
         '- **Couples (modele, squelette, os d\'attache) distincts : {}** dont {} pieces rapportees ; {} ecartes '
         '(modeles herites de Just Cause, dossiers jc*)'.format(len(resultats), sum(1 for r in resultats if r.attache),
                                                              jc),
         '- Squelettes distincts : {} ; modeles distincts : {}'.format(
             len({r.squelette for r in resultats}), len({r.modele for r in resultats})), '',
         '| Statut | Couples |', '|---|---:|']
    L += ['| {} | {} |'.format(s, n) for s, n in statuts.most_common()]
    L += ['', '## Vraisemblance', '',
          'rang5 : part des sommets dont l\'os dominant est parmi les {} os les plus proches (1 = juste) ; temoin : '
          'la meme part pour un os tire au hasard. Distance : mediane de la distance a l\'os dominant, en part de la '
          'taille du squelette. Os hors squelette : part des sommets dont l\'os depasse le squelette (0 '
          'attendu). rang5_repere_os : rang5 si les sommets etaient donnes dans le repere de leur os dominant. '
          'Pivot (pieces rigides, armes) : part des sommets dont la piece (sommets d\'un meme os dominant) contient '
          'l\'articulation de son os, a 5 % de la taille du squelette pres ; temoin : un os au hasard. '
          'rang5_attache (pieces rapportees, avec ou sans os) : part des sommets places dont l\'os d\'attache est '
          'parmi les {} os les plus proches ; temoin : un os au hasard.'.format(RANG, RANG),
          '',
          '| Mesure | Couples | Q1 | Mediane | Q3 | Part >= 0,8 (distance : <= 0,1 ; hors : = 0) |',
          '|---|---:|---:|---:|---:|---:|']
    for k in ('rang5', 'rang5_temoin', 'rang5_repere_os', 'pivot', 'pivot_temoin', 'distance', 'hors_squelette',
              'rang5_attache', 'rang5_attache_temoin'):
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
        L += ['', '## Par famille de modeles et par placement', '',
              'Pieces rapportees : sommets places au monde de leur os d\'attache fois la matrice locale de l\'entite '
              '(regle de l\'export glTF depuis le 28/10).', '',
              '| Famille | Couples | rang5 | temoin | repere de l\'os | pivot | temoin |',
              '|---|---:|---:|---:|---:|---:|---:|']
        par_famille = collections.defaultdict(list)
        for r in mesures:
            par_famille['/'.join(r.modele.split('/')[:2]) + (' (rapportees)' if r.attache else '')].append(r)
        for fam, rs in sorted(par_famille.items()):
            L.append('| `{}` | {} | {:.3f} | {:.3f} | {:.3f} | {:.3f} | {:.3f} |'.format(
                fam, len(rs), *(float(np.median([getattr(r, k) for r in rs])) for k in
                                ('rang5', 'rang5_temoin', 'rang5_repere_os', 'pivot', 'pivot_temoin'))))
        L += ['', '## Couples les moins vraisemblables (rang5)', '',
              '| Modele | Squelette | Attache | Os | rang5 | temoin | repere de l\'os | pivot | Os dominants |',
              '|---|---|---|---:|---:|---:|---:|---:|---|']
        for r in sorted(mesures, key=lambda r: r.rang5)[:20]:
            L.append('| `{}` | `{}` | {} | {} | {:.3f} | {:.3f} | {:.3f} | {:.3f} | {} |'.format(
                r.modele, r.squelette, r.attache, r.os, r.rang5, r.rang5_temoin, r.rang5_repere_os, r.pivot,
                r.principaux))
    rapportees = [r for r in resultats if r.rang5_attache is not None]
    if rapportees:
        L += ['', '## Pieces rapportees, mesurees sur leur os d\'attache', '',
              '| Pieces | Couples | rang5_attache | temoin | Part >= 0,8 |', '|---|---:|---:|---:|---:|']
        for nom, rs in (('avec flux d\'os', [r for r in rapportees if r.maillages_os]),
                        ('sans flux d\'os', [r for r in rapportees if not r.maillages_os]), ('toutes', rapportees)):
            if rs:
                v = np.array([r.rang5_attache for r in rs])
                L.append('| {} | {} | {:.3f} | {:.3f} | {:.1f} % |'.format(
                    nom, len(rs), float(np.median(v)), float(np.median([r.rang5_attache_temoin for r in rs])),
                    100.0 * float(np.mean(v >= 0.8))))
        L += ['', '| Modele | Squelette | Attache | Os de la peau | rang5_attache | temoin |', '|---|---|---|---|---:|---:|']
        for r in sorted(rapportees, key=lambda r: r.rang5_attache)[:15]:
            L.append('| `{}` | `{}` | {} | {} | {:.3f} | {:.3f} |'.format(
                r.modele, r.squelette, r.attache, r.principaux or '-', r.rang5_attache, r.rang5_attache_temoin))
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
    ap.add_argument('--details', help='expression reguliere sur le chemin du modele : squelette et sommets des couples '
                                      'choisis dans <sortie>\\details\\*.json, pour une etude a la main')
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
        for m, s, c, a, l, chemin in couples:
            e = sources.setdefault((m, s, a, l), [0, collections.Counter(), chemin])
            e[0] += 1
            e[1][_txt(c)] += 1
    jc = [k for k in sources if herite_just_cause(_txt(k[0]))]
    for k in jc:
        del sources[k]
    cles = sorted(sources)
    print('{} couples (modele, squelette, os d\'attache)'.format(len(cles)), flush=True)
    lots = [cles[i:i + args.lot] for i in range(0, len(cles), args.lot)]
    resultats: List[Resultat] = []
    dossier = str(Path(args.sortie) / 'details') if args.details else None
    for res in _carte(args.projet, _lot_couples, lots, args.processus, 'couples', t0, args.details, dossier):
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
