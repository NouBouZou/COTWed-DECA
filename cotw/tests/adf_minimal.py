"""
Fabrique un petit fichier ADF valide pour les tests (DECA ne sait pas ecrire d'ADF).

Disposition lue par deca.ff_adf.Adf.deserialize :
  0x00  ' FDA', u32 version 4, puis u32 : instance_count, instance_offset, typedef_count, typedef_offset,
        stringhash_count, stringhash_offset, nametable_count, nametable_offset, total_size, 5 x u32 inconnus
  0x40  commentaire (chaine terminee par 0)
  ...   table des noms : nametable_count octets de longueur, puis chaque nom suivi d'un octet nul
  ...   types : metatype, size, alignment, type_hash, u64 index du nom, flags, element_type_hash,
        element_length, puis (structure) u32 nombre de membres et membres, (StringHash, tableau) u32 0
  ...   instances : name_hash, type_hash, offset, size, u64 index du nom
  ...   donnees de l'instance. Les offsets internes (tableaux, chaines) sont relatifs au debut de l'instance.

Contenu : une instance "sheet" de type Sheet { Rows : A[Row], Ids : A[u32] },
Row { Name : String, Key : StringHash<4>, Value : u32 }.
"""
import struct
from typing import List, Tuple

from deca.ff_adf import typedef_u32
from deca.hashes import hash32_func

STRING = 0x8955583e
T_SHEET, T_ROW, T_ROWS, T_IDS, T_HASH = 0x5EE70001, 0x5EE70002, 0x5EE70003, 0x5EE70004, 0x5EE70005


def build(rows: List[Tuple[str, int, int]], ids: List[int]) -> bytes:
    """rows : (Name, Key en hash32, Value) ; ids : tableau u32."""
    names = ['Sheet', 'Row', 'A[Row]', 'A[u32]', 'StringHash', 'Rows', 'Ids', 'Name', 'Key', 'Value', 'sheet']
    ni = {n: i for i, n in enumerate(names)}

    # --- donnees de l'instance ---
    rows_off = 32
    row_size = 16
    strings_off = rows_off + row_size * len(rows)
    strings = b''
    string_offsets = []
    for name, _k, _v in rows:
        string_offsets.append(strings_off + len(strings))
        strings += name.encode() + b'\0'
    ids_off = strings_off + len(strings)
    ids_off += (4 - ids_off % 4) % 4
    inst = bytearray()
    inst += struct.pack('<III', rows_off, 0, len(rows)) + b'\0' * 4
    inst += struct.pack('<III', ids_off, 0, len(ids)) + b'\0' * 4
    for (name, key, value), so in zip(rows, string_offsets):
        inst += struct.pack('<II', so, len(name)) + struct.pack('<II', key, value)
    inst += strings
    inst += b'\0' * (ids_off - len(inst))
    inst += b''.join(struct.pack('<I', i) for i in ids)

    # --- types ---
    def typedef(metatype, size, align, type_hash, name, element=0, members=None):
        b = struct.pack('<IIII', metatype, size, align, type_hash) + struct.pack('<Q', ni[name])
        b += struct.pack('<III', 0, element, 0)
        if members is None:
            return b + struct.pack('<I', 0)
        b += struct.pack('<I', len(members))
        for mname, mtype, msize, moff in members:
            b += struct.pack('<Q', ni[mname]) + struct.pack('<IIII', mtype, msize, moff, 0) + struct.pack('<Q', 0)
        return b
    types = [
        typedef(1, 32, 4, T_SHEET, 'Sheet', members=[('Rows', T_ROWS, 16, 0), ('Ids', T_IDS, 16, 16)]),
        typedef(1, 16, 4, T_ROW, 'Row', members=[('Name', STRING, 8, 0), ('Key', T_HASH, 4, 8), ('Value', typedef_u32, 4, 12)]),
        typedef(3, 16, 4, T_ROWS, 'A[Row]', element=T_ROW),
        typedef(3, 16, 4, T_IDS, 'A[u32]', element=typedef_u32),
        typedef(9, 4, 4, T_HASH, 'StringHash'),
    ]
    typedef_blob = b''.join(types)

    # --- assemblage ---
    header_size = 0x40
    comment = b'\0'
    name_table = bytes(len(n) for n in names) + b''.join(n.encode() + b'\0' for n in names)
    pos = header_size + len(comment)
    nametable_offset = pos
    pos += len(name_table)
    pos += (4 - pos % 4) % 4
    typedef_offset = pos
    pos += len(typedef_blob)
    instance_offset = pos
    pos += 24
    pos += (16 - pos % 16) % 16
    data_offset = pos
    total = data_offset + len(inst)
    instance_entry = struct.pack('<IIII', int(hash32_func(b'sheet')), T_SHEET, data_offset, len(inst)) + struct.pack('<Q', ni['sheet'])
    header = b' FDA' + struct.pack('<I', 4) + struct.pack('<IIIIIIIII', 1, instance_offset, len(types), typedef_offset,
                                                          0, total, len(names), nametable_offset, total) + b'\0' * 20
    out = bytearray(header + comment + name_table)
    out += b'\0' * (typedef_offset - len(out))
    out += typedef_blob
    out += instance_entry
    out += b'\0' * (data_offset - len(out))
    out += inst
    return bytes(out)
