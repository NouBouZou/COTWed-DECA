"""
Feuilles XLSBook (ADF, type 0x0b73315d) : lecture en lignes, et ajout d'une ligne copiee d'une autre.

Le jeu range ses tableaux de reglages issus d'Excel (equipment_stats_ui.bin...) sous cette forme, lue comme
deca.export_import_adf.adf_export_xlsx_0x0b73315d :
  Sheet[]      : Name (chaine), Cols, Rows, CellIndex[] (Rows x Cols indices dans Cell, ligne par ligne) ;
  Cell[]       : Type (0 booleen, 1 chaine, 2 nombre), DataIndex (index dans BoolData, StringData ou ValueData),
                 AttributeIndex (format) ;
  StringData[], ValueData[], BoolData[], Attribute[], ColorData[].
Une meme cellule (Cell) peut servir a plusieurs cases : CellIndex ne fait que la designer.

Ajouter une ligne copiee d'un modele, sous un nouveau nom :
  StringData += nouveau nom ; Cell += copie de la cellule du nom du modele, DataIndex = nouveau nom ;
  CellIndex de la feuille += les indices de la ligne modele, la case du nom remplacee ; Rows += 1.
Les autres cases (stats) designent les memes cellules que le modele. La ligne est ajoutee en fin de feuille.
Constat en jeu (test8) : le jeu retrouve une ligne par le texte du nom ; [hypothese] l'ordre des lignes est
indifferent.
"""
import copy
from dataclasses import dataclass
from typing import List, Optional, Tuple

import adf_ecriture as AE

XLSBOOK_TYPE = 0x0b73315d
CELL_BOOL, CELL_STRING, CELL_VALUE = 0, 1, 2


class EXls(Exception):
    pass


def require_book(values):
    for key in ('Sheet', 'Cell', 'StringData'):
        if not isinstance(values, dict) or key not in values:
            raise EXls('pas une feuille XLSBook (membre "{}" absent)'.format(key))


def cell_text(values, cell) -> Optional[bytes]:
    if int(cell['Type']) != CELL_STRING:
        return None
    i = int(cell['DataIndex'])
    return bytes(values['StringData'][i]) if i < len(values['StringData']) else None


def cell_value(values, cell):
    """Valeur affichee d'une cellule (chaine, nombre ou booleen), comme l'export xlsx de DECA."""
    t, i = int(cell['Type']), int(cell['DataIndex'])
    data = {CELL_BOOL: 'BoolData', CELL_STRING: 'StringData', CELL_VALUE: 'ValueData'}.get(t)
    if data is None or data not in values or i >= len(values[data]):
        return None
    v = values[data][i]
    return bytes(v).decode('utf-8', 'replace') if t == CELL_STRING else (bool(v) if t == CELL_BOOL else float(v))


def sheet_rows(values, sheet_index: int) -> List[List[object]]:
    s = values['Sheet'][sheet_index]
    cols, rows, ci = int(s['Cols']), int(s['Rows']), s['CellIndex']
    return [[cell_value(values, values['Cell'][int(ci[c + cols * r])]) for c in range(cols)] for r in range(rows)]


@dataclass
class RowMatch:
    sheet: int
    sheet_name: str
    row: int
    columns: List[int]      # colonnes dont la cellule est le texte cherche


def find_rows(values, text: str, sheet_name: Optional[str] = None) -> List[RowMatch]:
    """Lignes dont une case est exactement le texte (cellule de type chaine)."""
    require_book(values)
    target = text.encode('utf-8')
    out = []
    for si, s in enumerate(values['Sheet']):
        name = bytes(s['Name']).decode('utf-8', 'replace')
        if sheet_name is not None and name != sheet_name:
            continue
        cols, rows, ci = int(s['Cols']), int(s['Rows']), s['CellIndex']
        for r in range(rows):
            hit = [c for c in range(cols) if cell_text(values, values['Cell'][int(ci[c + cols * r])]) == target]
            if hit:
                out.append(RowMatch(si, name, r, hit))
    return out


@dataclass
class AddRowPlan:
    match: RowMatch
    new_name: str
    string_index: int
    cell_index: int
    template_cell: int
    new_indices: List[int]
    second_rule: str
    new_row: int            # index de la ligne ajoutee (derniere ligne de la feuille)


def add_row(adf, data: bytes, template: str, new_name: str, sheet_name: Optional[str] = None,
            instance: int = 0) -> Tuple[bytes, AddRowPlan]:
    """Ajoute a la feuille une ligne copiee de celle du modele, sous new_name. Renvoie (fichier, plan)."""
    if adf.table_instance[instance].type_hash != XLSBOOK_TYPE:
        raise EXls('instance {} : type 0x{:08x}, pas XLSBook (0x{:08x})'.format(
            instance, adf.table_instance[instance].type_hash, XLSBOOK_TYPE))
    values = AE.plain_values(adf, instance)
    require_book(values)
    if not new_name or new_name == template:
        raise EXls('nouveau nom vide ou identique au modele')
    if find_rows(values, new_name):
        raise EXls('"{}" a deja une ligne dans le fichier'.format(new_name))
    matches = find_rows(values, template, sheet_name)
    if not matches:
        raise EXls('aucune ligne pour "{}"{}'.format(template, ' dans la feuille "{}"'.format(sheet_name) if sheet_name else ''))
    if len(matches) > 1:
        raise EXls('"{}" a {} lignes ({}) : preciser la feuille'.format(
            template, len(matches), ', '.join('{} ligne {}'.format(m.sheet_name, m.row) for m in matches)))
    m = matches[0]

    full = adf.table_instance_full_values[instance].value
    ed = AE.InstanceEditor(adf, data, instance)
    sheet = values['Sheet'][m.sheet]
    cols = int(sheet['Cols'])
    row_indices = [int(x) for x in sheet['CellIndex'][m.row * cols:(m.row + 1) * cols]]
    template_cell = row_indices[m.columns[0]]
    if any(row_indices[c] != template_cell for c in m.columns):
        raise EXls('le nom du modele occupe plusieurs cellules differentes dans sa ligne')

    # 1. le nouveau nom : texte en fin d'instance, en-tete ajoute a StringData
    rule, rule_label = ed.string_rule(full['StringData'])
    second = rule(len(new_name.encode('utf-8')))
    string_index = ed.grow(full['StringData'], [ed.new_string(new_name.encode('utf-8'), second)])

    # 2. la cellule du nom : copie de celle du modele, DataIndex = nouveau nom
    _pos, cell_bytes = ed.element(full['Cell'], template_cell)
    cell_type = full['Cell'].value[template_cell].type_id
    member = ed.member(cell_type, 'DataIndex')
    cell = bytearray(cell_bytes)
    cell[member.offset:member.offset + member.size] = string_index.to_bytes(member.size, 'little')
    cell_index = ed.grow(full['Cell'], [bytes(cell)])

    # 3. la ligne : indices du modele, case du nom remplacee ; puis Rows += 1
    new_indices = [cell_index if c in m.columns else x for c, x in enumerate(row_indices)]
    sheet_value = full['Sheet'].value[m.sheet]
    ci_value = sheet_value.value['CellIndex']
    _h, ci_type, ci_size, _a = ed.array(ci_value)
    ed.grow(ci_value, [x.to_bytes(ci_size, 'little') for x in new_indices])
    rows_pos, rows_member = ed.member_pos(sheet_value, 'Rows')
    ed.write_uint(rows_pos, rows_member.size, ed.read_uint(rows_pos, rows_member.size) + 1)

    plan = AddRowPlan(m, new_name, string_index, cell_index, template_cell, new_indices,
                      '{} ({})'.format(rule_label, second), int(sheet['Rows']))
    return ed.result(), plan


def expected_values(old_values, plan: AddRowPlan):
    """Ce que DECA doit relire apres add_row : l'original, plus exactement les ajouts du plan."""
    exp = copy.deepcopy(old_values)
    exp['StringData'].append(plan.new_name.encode('utf-8'))
    cell = copy.deepcopy(exp['Cell'][plan.template_cell])
    cell['DataIndex'] = type(cell['DataIndex'])(plan.string_index)
    exp['Cell'].append(cell)
    s = exp['Sheet'][plan.match.sheet]
    old_ci = list(s['CellIndex'])                       # DECA rend une liste (scalaires numpy ou entiers)
    conv = type(old_ci[0]) if old_ci else int
    s['CellIndex'] = old_ci + [conv(x) for x in plan.new_indices]
    s['Rows'] = type(s['Rows'])(int(s['Rows']) + 1)
    return exp


def verify(decode, old_data: bytes, new_data: bytes, plan: AddRowPlan, instance: int = 0):
    """Relit les deux fichiers avec DECA (decode : octets -> Adf) : la nouvelle lecture doit etre l'ancienne plus
    les ajouts du plan, rien d'autre ; la ligne ajoutee doit montrer les memes valeurs que le modele, au nom pres ;
    les autres instances doivent etre identiques."""
    old, new = decode(old_data), decode(new_data)
    if old is None or new is None:
        raise EXls('verification : fichier illisible par DECA')
    ov, nv = AE.plain_values(old, instance), AE.plain_values(new, instance)
    if AE.normalize(expected_values(ov, plan)) != AE.normalize(nv):
        raise EXls('verification : la relecture differe de l\'original plus la ligne ajoutee')
    rows = sheet_rows(nv, plan.match.sheet)
    template_row, added = rows[plan.match.row], rows[-1]
    for c, (a, b) in enumerate(zip(template_row, added)):
        if c in plan.match.columns:
            if b != plan.new_name:
                raise EXls('verification : nom de la ligne ajoutee incorrect ({!r})'.format(b))
        elif AE.normalize(a) != AE.normalize(b):
            raise EXls('verification : colonne {} de la ligne ajoutee differente du modele'.format(c))
    for j in range(len(old.table_instance)):
        if j != instance and AE.normalize(AE.plain_values(old, j)) != AE.normalize(AE.plain_values(new, j)):
            raise EXls('verification : instance {} modifiee'.format(j))
    return rows[-1]
