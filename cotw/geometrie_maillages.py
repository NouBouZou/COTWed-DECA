"""
geometrie_maillages.py -- les sommets d'un maillage AMF sont-ils decodes juste ? (controles de vraisemblance)

Pourquoi (2026-10-18) : audit_maillages.py montre que les 21 488 .meshc du jeu passent la conversion de DECA, mais
pas que le resultat est juste. Deux doutes dans deca/ff_adf_amf.py :
  - [corrige le 22/10 : mesure 14-maillages, 2 v - 1 avec z corrige, mediane 0,982] convert_R32_UNIT_VEC_AS_FLOAT
    (normales et tangentes de 18 231 maillages) calculait la troisieme composante avec
    (1.0 / 256*256), soit 1.0 : z vaut x. Le codage habituel de trois octets dans un flottant est
    f = c0 / 256 + c1 + 256 c2 : x = frac(f), y = frac(f / 256), z = frac(f / 65536), dans [0, 1[ ; reste a savoir si
    le jeu les ramene a [-1, 1] (2 v - 1), ce que DECA ne fait pas ;
  - convert_R8G8B8A8_TANGENT_SPACE (3 375 maillages) vient d'un article (Persson) sans verification sur COTW.
Plutot que deviner, on mesure : les normales lues doivent suivre celles des faces (produit vectoriel des aretes,
moyenne ponderee par l'aire autour de chaque sommet). Pour chaque decodage candidat, score = moyenne du produit
scalaire entre normale lue et normale des faces (1 : parfait ; ~0 : sans rapport ; -1 : sens des faces inverse).
Les positions doivent tomber dans la boite englobante de l'en-tete.
"""
from typing import Dict, List, Optional, Tuple

import numpy as np

DECODAGES = ('deca', 'z_corrige', 'z_corrige_2v-1')
# 'deca_avant_22_10' : l'ancien decodage, pour memoire (score median 0,22 sur COTW)
TOLERANCE_BOITE = 0.01      # part de la diagonale de la boite
SOMMETS_MAX = 300_000       # par fichier : les LOD suivants ne sont pas mesures au-dela (duree)


def _flux(mesh, vertex_buffers, sa, dtype: str) -> Optional[np.ndarray]:
    """Valeurs brutes d'un attribut de sommet (une ligne par sommet), ou None si le tampon est trop court."""
    s = sa.streamIndex
    buf = vertex_buffers[mesh.vertexBufferIndices[s]].data
    debut = mesh.vertexStreamOffsets[s] + sa.streamOffset
    stride = mesh.vertexStreamStrides[s]
    dt = np.dtype(dtype)
    n = mesh.vertexCount
    if n == 0 or debut + (n - 1) * stride + dt.itemsize > len(buf):
        return None
    vue = np.ndarray(shape=(n,), dtype=dt, buffer=buf, offset=debut, strides=(stride,))
    return np.array(vue)


def _attribut(mesh, usage: bytes, fmt: Optional[bytes] = None):
    for sa in mesh.streamAttributes:
        if sa.usage[1] == usage and (fmt is None or sa.format[1] == fmt):
            return sa
    return None


def positions(mesh, vertex_buffers) -> Optional[np.ndarray]:
    """(N, 3) flottants : R16G16B16_SNORM (x packingData[0] s'il n'est pas nul, comme preconvert_scale) ou R32G32B32."""
    sa = _attribut(mesh, b'AmfUsage_Position')
    if sa is None:
        return None
    if sa.format[1] == b'AmfFormat_R16G16B16_SNORM':
        v = _flux(mesh, vertex_buffers, sa, '3i2')
        if v is None:
            return None
        p = v.astype(np.float64) / 0x7fff
        if sa.packingData and sa.packingData[0] != 0.0:
            p *= sa.packingData[0]
        return p
    if sa.format[1] == b'AmfFormat_R32G32B32_FLOAT':
        v = _flux(mesh, vertex_buffers, sa, '3f4')
        return None if v is None else v.astype(np.float64)
    return None


def decoder_unit_vec(f: np.ndarray, mode: str) -> np.ndarray:
    """Vecteur code dans un flottant (R32_UNIT_VEC_AS_FLOAT) selon un decodage candidat (voir le docstring)."""
    f = np.asarray(f, dtype=np.float64)
    if mode == 'deca':
        # le code de DECA tel qu'il est (corrige le 22/10 d'apres la mesure 14-maillages)
        from deca.ff_adf_amf import convert_R32_UNIT_VEC_AS_FLOAT
        v = np.zeros((len(f), 3), dtype=np.float64)
        convert_R32_UNIT_VEC_AS_FLOAT(v, f, None, False)
        return v
    if mode == 'deca_avant_22_10':
        v = np.stack([f, f / 256.0, f], axis=-1)            # (1.0 / 256*256) == 1.0 dans l'ancien ff_adf_amf
    else:
        v = np.stack([f, f / 256.0, f / 65536.0], axis=-1)
    v = v - np.floor(v)
    if mode == 'z_corrige_2v-1':
        v = v * 2.0 - 1.0
    return v


def triangles(mesh, index_data: bytes) -> np.ndarray:
    """(T, 3) indices de tous les sous-maillages (indices de 16 ou 32 bits)."""
    dtype = {2: np.uint16, 4: np.uint32}.get(mesh.indexBufferStride)
    if dtype is None:
        return np.zeros((0, 3), dtype=np.int64)
    out = []
    for sm in mesh.subMeshes:
        debut = mesh.indexBufferOffset + sm.indexStreamOffset
        idx = np.frombuffer(index_data, dtype=dtype, count=sm.indexCount, offset=debut).astype(np.int64)
        out.append(idx[:len(idx) // 3 * 3].reshape(-1, 3))
    return np.concatenate(out) if out else np.zeros((0, 3), dtype=np.int64)


def normales_des_faces(pos: np.ndarray, tri: np.ndarray) -> np.ndarray:
    """(N, 3) normales unitaires des sommets : somme des normales des faces voisines ponderees par l'aire."""
    n = np.zeros_like(pos)
    if len(tri):
        a, b, c = pos[tri[:, 0]], pos[tri[:, 1]], pos[tri[:, 2]]
        fn = np.cross(b - a, c - a)
        for k in range(3):
            for comp in range(3):
                n[:, comp] += np.bincount(tri[:, k], weights=fn[:, comp], minlength=len(pos))
    norme = np.linalg.norm(n, axis=1)
    ok = norme > 1e-12
    n[ok] /= norme[ok, None]
    n[~ok] = 0.0
    return n


def score(lues: np.ndarray, reference: np.ndarray) -> Optional[float]:
    """Moyenne du produit scalaire entre les normales lues (normalisees) et celles des faces, sur les sommets qui
    ont les deux."""
    norme = np.linalg.norm(lues, axis=1)
    ok = (norme > 1e-9) & (np.linalg.norm(reference, axis=1) > 0.5)
    if not np.any(ok):
        return None
    return float(np.mean(np.sum(lues[ok] / norme[ok, None] * reference[ok], axis=1)))


def dans_boite(pos: np.ndarray, bmin, bmax, tolerance: float = TOLERANCE_BOITE) -> float:
    bmin, bmax = np.asarray(bmin, dtype=np.float64)[:3], np.asarray(bmax, dtype=np.float64)[:3]
    marge = tolerance * max(float(np.linalg.norm(bmax - bmin)), 1e-6)
    dedans = np.all((pos >= bmin - marge) & (pos <= bmax + marge), axis=1)
    return float(np.mean(dedans)) if len(pos) else 1.0


def mesurer(mesh, vertex_buffers, index_buffers, boite=None) -> Dict[str, float]:
    """Scores d'un AmfMesh (avant la conversion de DECA). Cles : boite, normale:<decodage>, tangent_space,
    tangente_orthogonale:<decodage> (moyenne de |t . n|, 0 attendu), os_hors_table, poids_ecart (mesurer_os),
    sommets."""
    out: Dict[str, float] = {'sommets': float(mesh.vertexCount)}
    pos = positions(mesh, vertex_buffers)
    if pos is None:
        return out
    if boite is not None and boite[0] is not None:
        out['boite'] = dans_boite(pos, boite[0], boite[1])
    if mesh.indexBufferIndex >= len(index_buffers):
        return out
    tri = triangles(mesh, index_buffers[mesh.indexBufferIndex].data)
    if not len(tri) or int(tri.max()) >= len(pos):
        return out
    ref = normales_des_faces(pos, tri)
    sa_n = _attribut(mesh, b'AmfUsage_Normal', b'AmfFormat_R32_UNIT_VEC_AS_FLOAT')
    sa_t = _attribut(mesh, b'AmfUsage_Tangent', b'AmfFormat_R32_UNIT_VEC_AS_FLOAT')
    if sa_n is not None:
        f = _flux(mesh, vertex_buffers, sa_n, 'f4')
        ft = _flux(mesh, vertex_buffers, sa_t, 'f4') if sa_t is not None else None
        if f is not None:
            for mode in DECODAGES:
                n = decoder_unit_vec(f, mode)
                s = score(n, ref)
                if s is not None:
                    out['normale:' + mode] = s
                if ft is not None:
                    t = decoder_unit_vec(ft, mode)
                    nn, tn = np.linalg.norm(n, axis=1), np.linalg.norm(t, axis=1)
                    ok = (nn > 1e-9) & (tn > 1e-9)
                    if np.any(ok):
                        out['tangente_orthogonale:' + mode] = float(np.mean(np.abs(np.sum(
                            n[ok] / nn[ok, None] * t[ok] / tn[ok, None], axis=1))))
    mesurer_os(mesh, vertex_buffers, out)
    sa_ts = _attribut(mesh, b'AmfUsage_TangentSpace', b'AmfFormat_R8G8B8A8_TANGENT_SPACE')
    if sa_ts is not None:
        from deca.ff_adf_amf import convert_R8G8B8A8_TANGENT_SPACE
        q = _flux(mesh, vertex_buffers, sa_ts, '4u1')
        if q is not None:
            nt = np.zeros((len(q), 6), dtype=np.float64)
            convert_R8G8B8A8_TANGENT_SPACE(nt, q.astype(np.float64), sa_ts, False)
            s = score(nt[:, 0:3], ref)
            if s is not None:
                out['tangent_space'] = s
    return out


def mesurer_os(mesh, vertex_buffers, out: Dict[str, float]):
    """Indices et poids d'os (R8G8B8A8_UINT / R8G8B8A8_UNORM) : os_hors_table = part des sommets qui citent, avec un
    poids non nul, un os au-dela de la table BoneIndexLookup du maillage (0 attendu) ; poids_ecart = moyenne de
    |somme des poids - 1| (0 attendu ; DECA remplace un jeu de poids nuls par (1, 0, 0, 0) pour le glTF)."""
    sa_i = _attribut(mesh, b'AmfUsage_BoneIndex', b'AmfFormat_R8G8B8A8_UINT')
    sa_w = _attribut(mesh, b'AmfUsage_BoneWeight', b'AmfFormat_R8G8B8A8_UNORM')
    if sa_i is None or sa_w is None:
        return
    idx = _flux(mesh, vertex_buffers, sa_i, '4u1')
    w = _flux(mesh, vertex_buffers, sa_w, '4u1')
    if idx is None or w is None:
        return
    w = w.astype(np.float64) / 255.0
    table = len(mesh.boneIndexLookup or [])
    if table:
        hors = np.any((idx >= table) & (w > 0), axis=1)
        out['os_hors_table'] = float(np.mean(hors))
    somme = w.sum(axis=1)
    nonnul = somme > 0
    if np.any(nonnul):
        out['poids_ecart'] = float(np.mean(np.abs(somme[nonnul] - 1.0)))


def cumuler(mesures: List[Dict[str, float]]) -> Dict[str, float]:
    """Moyenne ponderee par le nombre de sommets de chaque cle, sur les maillages d'un fichier."""
    poids: Dict[str, float] = {}
    somme: Dict[str, float] = {}
    for m in mesures:
        w = m.get('sommets', 1.0)
        for k, v in m.items():
            if k == 'sommets':
                continue
            somme[k] = somme.get(k, 0.0) + w * v
            poids[k] = poids.get(k, 0.0) + w
    return {k: somme[k] / poids[k] for k in somme if poids[k] > 0}
