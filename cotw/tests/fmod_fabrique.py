"""
Fabrique de banques FMOD Studio pour les tests (aucun fichier du jeu n'est versionne).

Meme disposition que sound/fmod_banks/master_strings.fmod_bankc (journal de recherche, 2026-10-09) : RIFF 'FEV ',
chunk 'FMT ', LIST 'PROJ' ; la banque de chaines porte le chunk STDT (arbre de prefixes des noms FMOD, GUID tries,
textes des noeuds, index GUID -> noeud et noeud -> parent).
"""
import struct
import uuid
from typing import Iterable, Optional, Tuple

from deca.ff_fmod_bank import string_table_bytes


def chunk(cid: bytes, body: bytes) -> bytes:
    return cid + struct.pack('<I', len(body)) + body + (b'\0' if len(body) & 1 else b'')


def container(cid: bytes, form: bytes, children: bytes) -> bytes:
    return cid + struct.pack('<I', 4 + len(children)) + form + children


def bank(names: Optional[Iterable[Tuple[uuid.UUID, str]]] = None, sound: bytes = b'') -> bytes:
    """Banque FMOD : FMT 125/119, PROJ (BNKI, listes vides, STDT si names), puis SND si sound."""
    proj = chunk(b'BNKI', bytes(range(32))) + container(b'LIST', b'EVTS', b'') + container(b'LIST', b'BEFX', b'')
    if names is not None:
        proj += chunk(b'SNDH', b'') + chunk(b'STDT', string_table_bytes(names)) + chunk(b'HASH', b'\x01\x00')
    body = chunk(b'FMT ', struct.pack('<II', 125, 119)) + container(b'LIST', b'PROJ', proj)
    if sound:
        body += chunk(b'SND ', sound)
    return container(b'RIFF', b'FEV ', body)


GUID_GRAVEL = uuid.UUID('e4be7f4e-866d-4c89-974c-b1ab00c66efe')
GUID_SHELLS = uuid.UUID('288a32f5-0f2f-43b9-a1ef-bc9add92723e')
NAMES = [
    (GUID_GRAVEL, 'event:/weapons/bullet_impact/plr_bullet_impact_gravel'),
    (GUID_SHELLS, 'event:/weapons/thp_weapon_generic/plr_weapon_shells_beretta'),
    (uuid.UUID('11111111-2222-3333-4444-555555555555'), 'event:/weapons/bullet_impact/plr_bullet_impact_grass'),
    (uuid.UUID('0b0bd674-676b-42b0-bb6f-d0f1490fd686'), 'bus:/'),
    (uuid.UUID('962ee1bf-0c72-4ce1-8451-d60f0d478547'), 'bus:/SFX'),
    (uuid.UUID('303fca22-565e-4689-9e26-edca63dc7f1d'), 'bus:/SFX/GUI'),
    (uuid.UUID('ef0f5bfc-35cc-47f5-b3be-602a184c7e0e'), 'bank:/master/master'),
    (uuid.UUID('b0079de6-6220-4e07-a7d1-2570be0371d9'), 'bank:/animals/specific/animal_test'),
    (uuid.UUID('7678dfbc-0000-4000-8000-000000000001'), 'vca:/SFX'),
    (uuid.UUID('527b91ca-0000-4000-8000-000000000002'), 'parameter:/ground_material_group'),
]


def strings_bank(names=NAMES) -> bytes:
    return bank(names)


def sound_bank(seed: int = 0) -> bytes:
    """Banque de sons sans table de chaines (le chunk SND porte un FSB5, ici factice)."""
    return bank(None, b'FSB5' + bytes([seed]) * 60)
