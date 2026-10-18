#!/usr/bin/env python3
"""
audit_maillages.py -- les maillages de COTW passent-ils par le chemin d'export de DECA ? (lecture seule, DECA ferme)

Pourquoi (2026-10-18) : la lecture des fichiers est complete (185 726 / 185 751) et les noms le sont a 96 %. Reste le
decodage (L2), et d'abord les maillages, dont un modloader aura besoin pour lire et ecrire des armes. Le README de
kk49 et le code (ff_adf_amf.py) signalent des formats de sommets non implementes ; on ne sait pas lesquels COTW
utilise, ni ce qui casse d'autre. Cet audit refait, pour chaque .meshc, ce que fait DECA avant d'ecrire un glTF
(ff_adf_amf_gltf.Deca3dMeshc.get_meshes) sans rien ecrire :
  1. lecture de l'ADF et controle des types attendus (en-tete 0xea60065d / 0x6f841426, tampons 0xe6834477) ;
  2. AmfMeshHeader et AmfMeshBuffers, puis le .hrmeshc (highLodPath, avec ou sans le prefixe intermediate/) ;
  3. amf_meshc_reformat (conversion des flux de sommets) ;
  4. formats de sortie connus de l'export glTF ; indices de chaque sous-maillage inferieurs au nombre de sommets ;
     positions finies ;
  5. vraisemblance (geometrie_maillages.py, avant la conversion) : positions dans la boite englobante, normales et
     tangentes comparees a celles des faces pour plusieurs decodages candidats.

Sorties (dossier --sortie) :
    maillages.csv        chemin ; statut ; detail ; lods ; maillages ; sommets ; hrmesh ; formats (usage:format)
    maillages.md         statuts, erreurs les plus frequentes, formats de sommets vus (et s'ils sont convertis)
    extraire_echantillons.bat  quelques .meshc (et leur .hrmeshc) par statut en echec

Mode --modeles : chaque .modelc est exporte en glTF comme le fait DECA (DecaGltf.export_modelc), dans un dossier
temporaire efface ensuite : materiaux (RenderBlockId traites ou non), textures citees (presentes ou non ; decodees et
ecrites seulement avec --textures, plus lent), maillage cite, erreurs. Sorties modeles.csv, modeles.md.

Les fichiers herites de Just Cause (dossiers jc*) sont ecartes : ils sont dans les archives, pas dans le jeu.
Quand deux fichiers portent le meme nom (collision de hash32), le noeud ADF est pris (meme regle que l'export,
ff_adf_amf_gltf.node_of_type).

Usage (DECA ferme) :
    python cotw\\audit_maillages.py D:\\dev\\work\\hp --sortie D:\\dev\\audits\\14-maillages
    python cotw\\audit_maillages.py D:\\dev\\work\\hp --modeles --sortie D:\\dev\\audits\\15-modeles
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _projet  # noqa: E402

TYPES_ENTETE = {0xea60065d, 0x6f841426, 0x7A2C9B73}
TYPES_TAMPONS = {0x67b3a453, 0xe6834477, 0x0E1C0800}
# formats de flux que l'export glTF sait decrire (ff_adf_amf_gltf.Deca3dMeshc.get_meshes, accesseurs)
FORMATS_EXPORT = {
    b'AmfFormat_R16G16B16_SNORM', b'AmfFormat_R16G16_SNORM', b'AmfFormat_R16G16_UNORM', b'AmfFormat_R16_SNORM',
    b'AmfFormat_R32_UINT', b'AmfFormat_R8G8B8A8_UNORM', b'AmfFormat_R8G8B8A8_UINT', b'AmfFormat_R16G16B16A16_SINT',
    b'AmfFormat_R32_UNIT_VEC_AS_FLOAT', b'AmfFormat_R32_R8G8B8A8_UNORM_AS_FLOAT', b'AmfFormat_R32_FLOAT',
    b'AmfFormat_R32G32_FLOAT', b'AmfFormat_R32G32B32_FLOAT', b'AmfFormat_R32G32B32A32_FLOAT',
    b'DecaFormat_R32G32B32A32_FLOAT_P1', b'DecaFormat_R32G32B32A32_FLOAT_N1',
}
STATUTS_OK = ('ok',)
ECHANTILLONS_PAR_STATUT = 5


class Resultat:
    __slots__ = ('chemin', 'uid', 'statut', 'detail', 'lods', 'maillages', 'sommets', 'hrmesh', 'formats',
                 'hr_uid', 'geo')

    def __init__(self, chemin: str, uid: int):
        self.chemin, self.uid = chemin, uid
        self.statut, self.detail = 'ok', ''
        self.lods = self.maillages = self.sommets = 0
        self.hrmesh = '-'
        self.hr_uid: Optional[int] = None
        self.formats: collections.Counter = collections.Counter()
        self.geo: Dict[str, float] = {}

    def ligne(self):
        return (self.chemin, self.statut, self.detail, self.lods, self.maillages, self.sommets, self.hrmesh,
                ' '.join('{}:{}x{}'.format(u, f, n) for (u, f), n in sorted(self.formats.items()))) + tuple(
                    '' if self.geo.get(k) is None else '{:.3f}'.format(self.geo[k]) for k in CLES_GEO)


CLES_GEO = ('boite', 'normale:deca', 'normale:z_corrige', 'normale:z_corrige_2v-1', 'tangente_orthogonale:deca',
            'tangente_orthogonale:z_corrige', 'tangente_orthogonale:z_corrige_2v-1', 'tangent_space')


def _txt(b) -> str:
    return b.decode('utf-8', 'replace') if isinstance(b, bytes) else str(b)


def _court(fmt) -> str:
    return _txt(fmt).replace('AmfFormat_', '').replace('DecaFormat_', 'Deca:').replace('AmfUsage_', '')


def verifier_indices(mesh, index_data: bytes) -> Optional[str]:
    """None si chaque sous-maillage n'utilise que des sommets existants ; sinon la premiere anomalie."""
    import numpy as np
    stride = mesh.indexBufferStride
    dtype = {2: np.uint16, 4: np.uint32}.get(stride)
    if dtype is None:
        return 'pas d\'indices de {} octets'.format(stride)
    for sm in mesh.subMeshes:
        debut = mesh.indexBufferOffset + sm.indexStreamOffset
        fin = debut + sm.indexCount * stride
        if fin > len(index_data):
            return 'sous-maillage {} : indices hors du tampon ({} > {})'.format(_txt(sm.subMeshId), fin, len(index_data))
        idx = np.frombuffer(index_data[debut:fin], dtype=dtype)
        if len(idx) and int(idx.max()) >= mesh.vertexCount:
            return 'sous-maillage {} : indice {} >= {} sommets'.format(_txt(sm.subMeshId), int(idx.max()),
                                                                     mesh.vertexCount)
    return None


def auditer_meshc(vfs, adf_db, uid: int, chemin: str) -> Resultat:
    """Le chemin d'export de DECA, sans ecriture. Voir le docstring du module."""
    import numpy as np
    from deca.ff_adf_amf import AmfMeshHeader, AmfMeshBuffers, amf_meshc_reformat
    from deca.util import remove_prefix_if_present
    r = Resultat(chemin, uid)
    etape = 'lecture'
    try:
        node = vfs.node_where_uid(uid)
        adf = adf_db.read_node(vfs, node)
        if adf is None:
            r.statut, r.detail = 'adf_illisible', ''
            return r
        etape = 'types'
        types = [t.type_hash for t in adf.table_instance]
        if len(types) != 2 or types[0] not in TYPES_ENTETE or types[1] not in TYPES_TAMPONS:
            r.statut, r.detail = 'types_inattendus', ' '.join('{:08x}'.format(t) for t in types)
            return r
        etape = 'en-tete'
        header = AmfMeshHeader(adf, adf.table_instance_full_values[0], merged_buffers=types[0] == 0x7A2C9B73)
        buffers = AmfMeshBuffers(adf, adf.table_instance_full_values[1], merged_buffers=types[1] == 0x0E1C0800)
        r.lods = len(header.lodGroups)
        for lg in header.lodGroups:
            for m in lg.meshes:
                r.maillages += 1
                r.sommets += m.vertexCount
                for sa in m.streamAttributes:
                    r.formats[(_court(sa.usage[1]), _court(sa.format[1]))] += 1
        etape = 'hrmesh'
        hr_path = header.highLodPath
        if hr_path:
            candidats = [hr_path]
            p2 = remove_prefix_if_present(b'intermediate/', hr_path)
            if p2 is not None:
                candidats.append(p2)
            hr_node = None
            from deca.ff_adf_amf_gltf import node_of_type, TYPES_ADF_NODE
            for c in candidats:
                nodes = node_of_type(vfs, c, TYPES_ADF_NODE)
                if nodes:
                    hr_node = nodes[0]
                    break
            if hr_node is None:
                # un maillage a un seul LOD cite souvent un .hrmeshc qui n'existe pas : ses tampons sont tous ici
                r.hrmesh = 'introuvable' if len(header.lodGroups) > 1 else 'inutile'
            else:
                r.hr_uid = hr_node.uid
                hr_adf = adf_db.read_node(vfs, hr_node)
                if hr_adf is None or len(hr_adf.table_instance) != 1:
                    r.hrmesh = 'illisible'
                else:
                    hr_buffers = AmfMeshBuffers(hr_adf, hr_adf.table_instance_full_values[0],
                                                merged_buffers=types[1] == 0x0E1C0800)
                    buffers.indexBuffers = buffers.indexBuffers + hr_buffers.indexBuffers
                    buffers.vertexBuffers = buffers.vertexBuffers + hr_buffers.vertexBuffers
                    r.hrmesh = 'ok'
        else:
            r.hrmesh = 'aucun'
        etape = 'indices'
        for lg in header.lodGroups:
            for m in lg.meshes:
                if m.indexBufferIndex >= len(buffers.indexBuffers):
                    r.statut, r.detail = 'tampon_absent', 'indices : tampon {} sur {} (hrmesh : {})'.format(
                        m.indexBufferIndex, len(buffers.indexBuffers), r.hrmesh)
                    return r
                if any(i >= len(buffers.vertexBuffers) for i in m.vertexBufferIndices):
                    r.statut, r.detail = 'tampon_absent', 'sommets : tampons {} sur {} (hrmesh : {})'.format(
                        list(m.vertexBufferIndices), len(buffers.vertexBuffers), r.hrmesh)
                    return r
                err = verifier_indices(m, buffers.indexBuffers[m.indexBufferIndex].data)
                if err:
                    r.statut, r.detail = 'indices_faux', err
                    return r
        etape = 'geometrie'
        try:
            import geometrie_maillages as GM
            bb = header.boundingBox
            boite = (bb.min, bb.max) if bb is not None else None
            mesures, total = [], 0
            for lg in header.lodGroups:
                for m in lg.meshes:
                    if total >= GM.SOMMETS_MAX:
                        break
                    mesures.append(GM.mesurer(m, buffers.vertexBuffers, buffers.indexBuffers, boite))
                    total += m.vertexCount
            r.geo = GM.cumuler(mesures)
        except Exception as exc:  # noqa: BLE001 - la mesure est un plus : son echec n'arrete pas l'audit
            r.geo = {}
            r.detail = 'geometrie : {}: {}'.format(type(exc).__name__, str(exc)[:120])
        etape = 'conversion'
        amf_meshc_reformat(header, buffers)
        etape = 'export'
        for lg in header.lodGroups:
            for m in lg.meshes:
                for sa in m.streamAttributes:
                    if sa.format[1] not in FORMATS_EXPORT:
                        r.statut, r.detail = 'format_non_exporte', '{} ({})'.format(_court(sa.format[1]),
                                                                                     _court(sa.usage[1]))
                        return r
                    if sa.usage[1] == b'AmfUsage_Position' and getattr(sa, 'min', None) is not None:
                        if not (np.all(np.isfinite(sa.min)) and np.all(np.isfinite(sa.max))):
                            r.statut, r.detail = 'positions_non_finies', ''
                            return r
    except Exception as exc:  # noqa: BLE001 - un maillage en echec est compte, pas fatal
        r.statut = 'echec_' + etape
        r.detail = '{}: {}'.format(type(exc).__name__, str(exc)[:200])
    return r


# ---------------------------------------------------------------------------------------------------------------
# Travail en parallele (meme organisation qu'audit_noms_internes.py)
# ---------------------------------------------------------------------------------------------------------------

class Journal:
    """Journal de DECA qui garde les messages utiles a l'audit d'un modele."""
    MOTIFS = (('Unhandled RenderBlockId', 'materiau_non_traite'), ('Missing Texture file', 'texture_absente'),
              ('Not Mapped', 'fichier_absent'), ('missing high resolution', 'texture_sans_hmddsc'))

    def __init__(self):
        self.messages: List[Tuple[str, str]] = []

    def log(self, msg='', *a, **k):
        msg = str(msg)
        for motif, cle in self.MOTIFS:
            if motif in msg:
                self.messages.append((cle, msg))
    trace = debug = warning = error = log


class ResultatModele(Resultat):
    __slots__ = ('materiaux', 'blocs', 'textures', 'textures_absentes', 'maillage', 'gltf')

    def __init__(self, chemin: str, uid: int):
        Resultat.__init__(self, chemin, uid)
        self.materiaux = 0
        self.blocs: collections.Counter = collections.Counter()      # RenderBlockId -> materiaux
        self.textures = 0
        self.textures_absentes: List[str] = []
        self.maillage = ''
        self.gltf = ''

    def ligne(self):
        return (self.chemin, self.statut, self.detail, self.maillage, self.materiaux,
                ' '.join('{}x{}'.format(b, n) for b, n in sorted(self.blocs.items())), self.textures,
                len(self.textures_absentes), ' | '.join(self.textures_absentes[:5]), self.gltf)


BLOCS_TRAITES = {'GeneralR2', 'Character', 'CarPaint', 'VegetationFoliage', 'VegetationBark'}


def auditer_modelc(vfs, adf_db, uid: int, chemin: str, textures: bool = False) -> ResultatModele:
    """Export glTF du modele par DECA dans un dossier temporaire (efface ensuite) ; voir le docstring du module."""
    import shutil
    import tempfile
    from deca.ff_adf_amf import AmfModel
    from deca.ff_adf_amf_gltf import DecaGltf, DecaGltfNode, node_of_type, TYPES_TEXTURE_NODE
    r = ResultatModele(chemin, uid)
    journal = Journal()
    logger = vfs.logger
    vfs.logger = journal
    tmp = tempfile.mkdtemp(prefix='cotw_modele_')
    etape = 'lecture'
    try:
        node = vfs.node_where_uid(uid)
        adf = adf_db.read_node(vfs, node)
        model = AmfModel(adf, adf.table_instance_full_values[0])
        r.maillage = _txt(model.mesh)
        for m in model.materials:
            r.materiaux += 1
            r.blocs[_txt(m.renderBlockId)] += 1
            for t in m.textures:
                if t:
                    r.textures += 1
                    if not node_of_type(vfs, t, TYPES_TEXTURE_NODE):
                        r.textures_absentes.append(_txt(t))
        etape = 'export'
        gltf = DecaGltf(vfs, tmp, chemin, texture_format='dds')
        if not textures:
            # textures citees deja comptees ci-dessus ; ne pas les decoder ni les ecrire
            gltf.db.gltf_add_texture = lambda _g, _v: None
        with gltf.scene():
            with DecaGltfNode(gltf, name=chemin.rsplit('/', 1)[-1]):
                gltf.export_modelc(chemin.encode('utf-8'), None)
        gltf.gltf_save()
        g = gltf.gltf
        r.gltf = '{} maillages, {} materiaux, {} accesseurs'.format(len(g.meshes), len(g.materials), len(g.accessors))
        absents = [m for k, m in journal.messages if k == 'fichier_absent']
        if not g.meshes:
            r.statut, r.detail = 'sans_maillage', absents[0][:200] if absents else r.maillage
        elif any(b not in BLOCS_TRAITES for b in r.blocs):
            r.statut = 'materiau_non_traite'
            r.detail = ', '.join(sorted(b for b in r.blocs if b not in BLOCS_TRAITES))
        elif r.textures_absentes:
            r.statut, r.detail = 'texture_absente', r.textures_absentes[0]
    except Exception as exc:  # noqa: BLE001 - un modele en echec est compte, pas fatal
        r.statut = 'echec_' + etape
        r.detail = '{}: {}'.format(type(exc).__name__, str(exc)[:200])
    finally:
        vfs.logger = logger
        shutil.rmtree(tmp, ignore_errors=True)
    return r


_VFS = None
_ADF_DB = None
_MODE = {'modeles': False, 'textures': False}


def _init_travail(projet: str, modeles: bool = False, textures: bool = False):
    global _VFS, _ADF_DB
    from deca.ff_adf import AdfDatabase
    _VFS = _projet.open_project(projet)
    _ADF_DB = AdfDatabase(_VFS)
    _MODE['modeles'], _MODE['textures'] = modeles, textures


def _lot(lot: List[Tuple[int, str]]) -> List[Resultat]:
    if _MODE['modeles']:
        return [auditer_modelc(_VFS, _ADF_DB, uid, chemin, _MODE['textures']) for uid, chemin in lot]
    return [auditer_meshc(_VFS, _ADF_DB, uid, chemin) for uid, chemin in lot]


def herite_just_cause(chemin: str) -> bool:
    """Fichier herite de Just Cause (un dossier commence par 'jc', par ex. models/jc_vehicles/...) : present dans les
    archives de COTW mais pas dans le jeu (2026-10-18)."""
    return any(part.startswith('jc') for part in chemin.split('/')[:-1])


TYPES_ADF = ('adf', 'adf0', 'adf5', 'adfb')


def selectionner(vfs, echantillon: int = 0, graine: int = 1, avec_jc: bool = False, extension: str = '.meshc'
                 ) -> Tuple[List[Tuple[int, str]], int]:
    """(un noeud ADF par fichier nomme de cette extension, le plus petit numero ; nombre de fichiers herites de Just
    Cause ecartes). Le type ADF ecarte l'autre fichier d'une collision de nom (une texture sans type nommee
    eu_hare_internal_left_fore_phalanges.meshc, 14-maillages)."""
    rows = vfs.db_query_all("SELECT MIN(node_id), v_path FROM core_nodes WHERE CAST(v_path AS TEXT) LIKE ? "
                            "AND file_type IN ({}) GROUP BY v_path".format(','.join('?' * len(TYPES_ADF))),
                            ['%' + extension] + list(TYPES_ADF), dbg='cotw_audit_maillages')
    out = sorted((uid, _projet.to_text(p)) for uid, p in rows)
    jc = 0
    if not avec_jc:
        n = len(out)
        out = [(u, c) for u, c in out if not herite_just_cause(c)]
        jc = n - len(out)
    if echantillon and echantillon < len(out):
        import random
        out = sorted(random.Random(graine).sample(out, echantillon))
    return out, jc


# ---------------------------------------------------------------------------------------------------------------
# Rapport
# ---------------------------------------------------------------------------------------------------------------

def vraisemblance(resultats: List[Resultat]) -> List[str]:
    """Section du rapport : scores de geometrie_maillages par fichier (mediane, quartiles, part au-dessus d'un
    seuil)."""
    import numpy as np
    L = ['## Vraisemblance des sommets (geometrie_maillages.py)', '',
         'Normales : moyenne du produit scalaire entre la normale lue et celle des faces (1 = juste, 0 = sans rapport, '
         '-1 = faces dans l\'autre sens). Tangentes : moyenne de |t . n| (0 attendu). Boite : part des sommets dans la '
         'boite englobante de l\'en-tete.', '',
         '| Mesure | Fichiers | Q1 | Mediane | Q3 | Part >= 0,9 (boite : >= 0,99 ; tangentes : <= 0,1) |',
         '|---|---:|---:|---:|---:|---:|']
    for k in CLES_GEO:
        v = np.array([r.geo[k] for r in resultats if r.geo.get(k) is not None])
        if not len(v):
            continue
        if k == 'boite':
            part = np.mean(v >= 0.99)
        elif k.startswith('tangente_orthogonale'):
            part = np.mean(v <= 0.1)
        else:
            part = np.mean(v >= 0.9)
        q1, med, q3 = np.percentile(v, [25, 50, 75])
        L.append('| {} | {} | {:.3f} | {:.3f} | {:.3f} | {:.1f} % |'.format(k, len(v), q1, med, q3, 100 * part))
    L.append('')
    return L


def ecrire(sortie: Path, resultats: List[Resultat], projet: str, duree: float, jc: int = 0):
    sortie.mkdir(parents=True, exist_ok=True)
    with open(sortie / 'maillages.csv', 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f, delimiter=';')
        w.writerow(['chemin', 'statut', 'detail', 'lods', 'maillages', 'sommets', 'hrmesh', 'formats']
                   + [k.replace(':', '_') for k in CLES_GEO])
        w.writerows(r.ligne() for r in resultats)
    statuts = collections.Counter(r.statut for r in resultats)
    n = len(resultats)
    ok = sum(statuts[s] for s in STATUTS_OK)
    L = ['# Audit des maillages (.meshc)', '',
         '- Projet : `{}` ; {} fichiers .meshc ; duree : {:.0f} s'.format(projet, n, duree)
         + (' ; {} herites de Just Cause (dossiers jc*) ecartes'.format(jc) if jc else ''),
         '- **Passent le chemin d\'export de DECA : {} / {} ({:.1f} %)**'.format(ok, n, 100.0 * ok / max(n, 1)), '',
         '| Statut | Fichiers |', '|---|---:|']
    L += ['| {} | {} |'.format(s, c) for s, c in statuts.most_common()]
    hr = collections.Counter(r.hrmesh for r in resultats)
    L += ['', 'Fichier haute resolution (`highLodPath`) : ' + ', '.join('{} {}'.format(k, v) for k, v in hr.most_common()),
          '']
    details = collections.Counter((r.statut, r.detail.split(':')[0] if r.statut.startswith('echec') else r.detail)
                                  for r in resultats if r.statut not in STATUTS_OK)
    if details:
        L += ['## Echecs les plus frequents', '', '| Statut | Detail | Fichiers | Exemple |', '|---|---|---:|---|']
        exemples = {}
        for r in resultats:
            k = (r.statut, r.detail.split(':')[0] if r.statut.startswith('echec') else r.detail)
            exemples.setdefault(k, r)
        for (s, d), c in details.most_common(30):
            e = exemples[(s, d)]
            L.append('| {} | {} | {} | `{}` : {} |'.format(s, d.replace('|', '/'), c, e.chemin,
                                                           e.detail.replace('|', '/')[:160]))
        L.append('')
    formats = collections.Counter()
    fichiers_par_format = collections.Counter()
    for r in resultats:
        formats.update(r.formats)
        for k in r.formats:
            fichiers_par_format[k] += 1
    if formats:
        L += ['## Formats de sommets (avant conversion)', '', '| Usage | Format | Flux | Fichiers |', '|---|---|---:|---:|']
        L += ['| {} | {} | {} | {} |'.format(u, f, c, fichiers_par_format[(u, f)])
              for (u, f), c in formats.most_common()]
        L.append('')
    L += vraisemblance(resultats)
    (sortie / 'maillages.md').write_text('\n'.join(L) + '\n', encoding='utf-8')
    # echantillons : quelques fichiers par statut en echec, avec leur .hrmeshc
    par_statut = collections.defaultdict(list)
    for r in resultats:
        if r.statut not in STATUTS_OK and len(par_statut[r.statut]) < ECHANTILLONS_PAR_STATUT:
            par_statut[r.statut].append(r)
    repo = Path(__file__).resolve().parent.parent
    lignes = ['@echo off',
              'rem Echantillons des .meshc en echec (audit_maillages.py), avec leur .hrmeshc. DECA ferme.',
              'cd /d "{}"'.format(repo),
              'if exist venv\\Scripts\\activate.bat (call venv\\Scripts\\activate.bat) else '
              '(echo venv introuvable dans %CD% & exit /b 1)']
    for s, rs in sorted(par_statut.items()):
        ids = []
        for r in rs:
            ids.append(str(r.uid))
            if r.hr_uid is not None:
                ids.append(str(r.hr_uid))
        lignes.append('python cotw\\extraire.py "{}" --noeuds {} --sortie "{}" --a-plat'.format(
            projet, ' '.join(ids), str(sortie / 'echantillons' / s)))
    (sortie / 'extraire_echantillons.bat').write_text('\r\n'.join(lignes) + '\r\n', encoding='utf-8')
    return statuts


def ecrire_modeles(sortie: Path, resultats: List[ResultatModele], projet: str, duree: float, jc: int = 0):
    sortie.mkdir(parents=True, exist_ok=True)
    with open(sortie / 'modeles.csv', 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f, delimiter=';')
        w.writerow(['chemin', 'statut', 'detail', 'maillage', 'materiaux', 'render_blocks', 'textures',
                    'textures_absentes', 'exemples_absentes', 'gltf'])
        w.writerows(r.ligne() for r in resultats)
    statuts = collections.Counter(r.statut for r in resultats)
    n = len(resultats)
    ok = statuts['ok']
    L = ['# Audit des modeles (.modelc) : export glTF de DECA', '',
         '- Projet : `{}` ; {} fichiers .modelc ; duree : {:.0f} s'.format(projet, n, duree)
         + (' ; {} herites de Just Cause (dossiers jc*) ecartes'.format(jc) if jc else ''),
         '- **Exportes sans defaut : {} / {} ({:.1f} %)**'.format(ok, n, 100.0 * ok / max(n, 1)), '',
         '| Statut | Fichiers |', '|---|---:|']
    L += ['| {} | {} |'.format(s, c) for s, c in statuts.most_common()]
    blocs, fichiers_bloc = collections.Counter(), collections.Counter()
    for r in resultats:
        blocs.update(r.blocs)
        for b in r.blocs:
            fichiers_bloc[b] += 1
    L += ['', '## Materiaux par RenderBlockId', '', '| RenderBlockId | Traite par l\'export | Materiaux | Modeles |',
          '|---|---|---:|---:|']
    L += ['| {} | {} | {} | {} |'.format(b, 'oui' if b in BLOCS_TRAITES else '**non**', c, fichiers_bloc[b])
          for b, c in blocs.most_common()]
    t = sum(r.textures for r in resultats)
    ta = sum(len(r.textures_absentes) for r in resultats)
    L += ['', 'Textures citees : {} ; absentes du projet : {} ({:.1f} %).'.format(t, ta, 100.0 * ta / max(t, 1)), '']
    absentes = collections.Counter(a for r in resultats for a in r.textures_absentes)
    if absentes:
        L += ['Textures absentes les plus citees : ' + ', '.join('`{}` ({})'.format(a, c)
                                                              for a, c in absentes.most_common(15)), '']
    echecs = collections.Counter((r.statut, r.detail.split(':')[0]) for r in resultats
                                 if r.statut.startswith('echec') or r.statut == 'sans_maillage')
    if echecs:
        ex = {}
        for r in resultats:
            ex.setdefault((r.statut, r.detail.split(':')[0]), r)
        L += ['## Echecs', '', '| Statut | Detail | Fichiers | Exemple |', '|---|---|---:|---|']
        L += ['| {} | {} | {} | `{}` : {} |'.format(s, d.replace('|', '/'), c, ex[(s, d)].chemin,
                                                  ex[(s, d)].detail.replace('|', '/')[:160])
              for (s, d), c in echecs.most_common(30)]
    (sortie / 'modeles.md').write_text('\n'.join(L) + '\n', encoding='utf-8')
    return statuts


def main(argv: Optional[List[str]] = None) -> int:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(errors='replace')
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('projet', help='projet DECA (dossier contenant db\\core.db)')
    ap.add_argument('--sortie', required=True, help='dossier des resultats')
    ap.add_argument('--echantillon', type=int, default=0, help='nombre de .meshc tires au hasard (0 = tous)')
    ap.add_argument('--modeles', action='store_true', help='auditer les .modelc (export glTF) au lieu des .meshc')
    ap.add_argument('--textures', action='store_true',
                    help='avec --modeles : decoder et ecrire aussi les textures (plus lent)')
    ap.add_argument('--avec-jc', action='store_true',
                    help='garder les maillages herites de Just Cause (dossiers jc*, ecartes par defaut)')
    ap.add_argument('--processus', type=int, default=max(1, (os.cpu_count() or 2) * 3 // 4),
                    help='processus de lecture (defaut : 3/4 des coeurs)')
    ap.add_argument('--lot', type=int, default=50, help='fichiers par lot envoye a un processus')
    args = ap.parse_args(argv)
    t0 = time.time()
    vfs = _projet.open_project(args.projet)
    try:
        ext = '.modelc' if args.modeles else '.meshc'
        choix, jc = selectionner(vfs, args.echantillon, avec_jc=args.avec_jc, extension=ext)
    finally:
        vfs.db_conn.close()
    print('{} fichiers {}{}'.format(len(choix), ext, ' ({} herites de Just Cause ecartes)'.format(jc) if jc else ''))
    lots = [choix[i:i + args.lot] for i in range(0, len(choix), args.lot)]
    resultats: List[Resultat] = []
    if args.processus <= 1:
        _init_travail(args.projet, args.modeles, args.textures)
        for lot in lots:
            resultats += _lot(lot)
    else:
        with multiprocessing.Pool(args.processus, initializer=_init_travail,
                                  initargs=(args.projet, args.modeles, args.textures)) as pool:
            for i, rs in enumerate(pool.imap_unordered(_lot, lots), 1):
                resultats += rs
                if i % 20 == 0:
                    print('  {} / {}'.format(len(resultats), len(choix)), flush=True)
    resultats.sort(key=lambda r: r.chemin)
    if args.modeles:
        statuts = ecrire_modeles(Path(args.sortie), resultats, args.projet, time.time() - t0, jc)
    else:
        statuts = ecrire(Path(args.sortie), resultats, args.projet, time.time() - t0, jc)
    print(', '.join('{} {}'.format(s, c) for s, c in statuts.most_common()))
    print('Resultats : {}'.format(Path(args.sortie).resolve()))
    return 0


if __name__ == '__main__':
    multiprocessing.freeze_support()
    sys.exit(main())
