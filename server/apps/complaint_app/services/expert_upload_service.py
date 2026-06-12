from __future__ import annotations

import json
import os
from datetime import datetime

EXPECTED_HEADERS = {
    '一级分类': {'aliases': ['一级分类']},
    '一级分类说明': {'aliases': ['一级分类说明']},
    '名词辨析': {'aliases': ['名词辨析']},
    '二级分类': {'aliases': ['二级分类']},
    '二级分类说明': {'aliases': ['二级分类说明']},
    '核实事项': {'aliases': ['核实事项']},
    '是否属于支付部门核实范围': {'aliases': ['是否属于支付部门核实范围']},
    '处理建议': {'aliases': ['处理建议']},
    '法律法规': {'aliases': ['法律法规']},
    '核实要点': {'aliases': ['核实要点']},
    '调阅清单': {'aliases': ['调阅清单']},
    '注意事项': {'aliases': ['注意事项']},
}

CANONICAL_KEYS = {
    '一级分类': 'category',
    '一级分类说明': 'first_category_description',
    '名词辨析': 'glossary',
    '二级分类': 'overview',
    '二级分类说明': 'second_category_description',
    '核实事项': 'decomposition',
    '是否属于支付部门核实范围': 'in_scope',
    '处理建议': 'suggestions',
    '法律法规': 'law_list',
    '核实要点': 'verification_points',
    '调阅清单': 'checklist',
    '注意事项': 'notes',
}


def _normalize_text(value) -> str:
    if value is None:
        return ''
    s = str(value)
    s = s.replace('\r\n', '\n').replace('\r', '\n')
    s = s.replace('\u00A0', ' ').replace('\u3000', ' ')
    s = s.strip()
    return s


def _normalize_header(name: str) -> str:
    return _normalize_text(name).replace(' ', '').replace('　', '')


def _match_headers_row(sheet) -> tuple:
    """返回 (header_row_index, header_map)，header_map: canonical_name -> column_index"""
    max_rows = min(sheet.max_row, 10)
    for r in range(1, max_rows + 1):
        values = []
        for c in range(1, sheet.max_column + 1):
            values.append(_normalize_header(sheet.cell(row=r, column=c).value))
        header_map = {}
        for canonical, conf in EXPECTED_HEADERS.items():
            aliases = [_normalize_header(a) for a in conf['aliases']]
            for i, v in enumerate(values, start=1):
                if v and v in aliases:
                    header_map[canonical] = i
                    break
        if '二级分类' in header_map and '核实事项' in header_map:
            return r, header_map
    r = 1
    values = [_normalize_header(sheet.cell(row=r, column=c).value) for c in range(1, sheet.max_column + 1)]
    header_map = {}
    for canonical, conf in EXPECTED_HEADERS.items():
        aliases = [_normalize_header(a) for a in conf['aliases']]
        for i, v in enumerate(values, start=1):
            if v and v in aliases:
                header_map[canonical] = i
                break
    return r, header_map


def _get_overview_for_row(sheet, row_idx: int, overview_col: int) -> str:
    cell = sheet.cell(row=row_idx, column=overview_col)
    val = _normalize_text(cell.value)
    if val:
        return val
    try:
        for merged in getattr(sheet, 'merged_cells').ranges:
            if merged.min_col <= overview_col <= merged.max_col and merged.min_row <= row_idx <= merged.max_row:
                top_left = sheet.cell(row=merged.min_row, column=merged.min_col)
                return _normalize_text(top_left.value)
    except Exception:
        pass
    r = row_idx - 1
    while r >= 1:
        v = _normalize_text(sheet.cell(row=r, column=overview_col).value)
        if v:
            return v
        r -= 1
    return ''


def _parse_excel(file_path: str) -> dict:
    from openpyxl import load_workbook

    wb = load_workbook(file_path, data_only=True)
    sheet = wb.active

    header_row, header_map = _match_headers_row(sheet)
    rows = []
    overview_col = header_map.get('二级分类')
    decomp_col = header_map.get('核实事项')
    category_col = header_map.get('一级分类')

    current_category = None

    for r in range(header_row + 1, sheet.max_row + 1):
        is_empty = True
        for c in range(1, sheet.max_column + 1):
            if _normalize_text(sheet.cell(row=r, column=c).value):
                is_empty = False
                break
        if is_empty:
            continue

        overview = _get_overview_for_row(sheet, r, overview_col) if overview_col else ''
        decomp = _normalize_text(sheet.cell(row=r, column=decomp_col).value) if decomp_col else ''
        if not decomp:
            fc = header_map.get('核实要点')
            if fc:
                decomp = _normalize_text(sheet.cell(row=r, column=fc).value)
        if not decomp:
            fc = header_map.get('处理建议')
            if fc:
                val = _normalize_text(sheet.cell(row=r, column=fc).value)
                if val:
                    decomp = val
        if not decomp:
            continue

        item = {
            'row_no': r,
            'overview': overview,
            'decomposition': decomp,
        }
        if category_col:
            category_value = _normalize_text(sheet.cell(row=r, column=category_col).value)
            if category_value:
                current_category = category_value
            item['category'] = current_category or '未分类'
        else:
            item['category'] = '未分类'

        for canonical in [
            '一级分类说明',
            '名词辨析',
            '二级分类说明',
            '是否属于支付部门核实范围',
            '处理建议',
            '法律法规',
            '核实要点',
            '调阅清单',
            '注意事项',
        ]:
            col = header_map.get(canonical)
            val = _normalize_text(sheet.cell(row=r, column=col).value) if col else ''
            key = CANONICAL_KEYS[canonical]
            item[key] = val
        rows.append(item)

    groups_map = {}
    for row in rows:
        cat = row.get('category') or '未分类'
        groups_map.setdefault(cat, []).append(row)
        groups = [
            {
                'category': cat,
                'items_count': len(items),
                'items': [
                    {
                        'overview': it.get('overview'),
                        'decomposition': it.get('decomposition'),
                        'first_category_description': it.get('first_category_description'),
                        'glossary': it.get('glossary'),
                        'second_category_description': it.get('second_category_description'),
                        'in_scope': it.get('in_scope'),
                        'suggestions': it.get('suggestions'),
                        'verification_points': it.get('verification_points'),
                        'checklist': it.get('checklist'),
                        'law_list': it.get('law_list'),
                        'notes': it.get('notes'),
                        'row_no': it.get('row_no'),
                    }
                    for it in items
                ]
            }
            for cat, items in groups_map.items()
        ]

    desired_order = [
        '一级分类', '一级分类说明', '名词辨析', '二级分类', '二级分类说明', '核实事项', '是否属于支付部门核实范围',
        '处理建议', '法律法规', '核实要点', '调阅清单', '注意事项'
    ]
    columns_detected = [c for c in desired_order if c in header_map]

    parsed = {
        'rows': rows,
        'groups': groups,
        'columns_detected': columns_detected,
        'sheets': [sheet.title],
        'rows_count': len(rows),
        'overview_count': len(groups),
    }
    return parsed


def _safe_id() -> str:
    return datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + os.urandom(3).hex()


def parse_and_store_excel(upload_file, base_dir: str) -> tuple[dict, int]:
    if not upload_file:
        return {'error': '缺少文件字段 file'}, 400
    if not upload_file.filename:
        return {'error': '文件名为空'}, 400

    filename = upload_file.filename
    if '.' not in filename or filename.rsplit('.', 1)[1].lower() not in {'xlsx', 'xls'}:
        return {'error': '仅支持 xlsx/xls 文件'}, 400

    upload_id = _safe_id()
    folder = os.path.join(base_dir, upload_id)
    os.makedirs(folder, exist_ok=True)

    ext = filename.rsplit('.', 1)[1].lower()
    original_name = 'original.' + ext
    original_path = os.path.join(folder, original_name)
    upload_file.save(original_path)

    try:
        parsed = _parse_excel(original_path)
    except Exception as exc:
        return {'error': f'解析失败: {exc}'}, 500

    meta = {
        'id': upload_id,
        'created_at': datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
        'file_name': filename,
        'sheet': parsed.get('sheets', [''])[0],
        'columns_detected': parsed.get('columns_detected', []),
        'rows_count': parsed.get('rows_count', 0),
        'overview_count': parsed.get('overview_count', 0),
    }

    parsed_for_store = {
        (g.get('category') or ''): g.get('items', [])
        for g in parsed.get('groups', [])
    }
    with open(os.path.join(folder, 'parsed.json'), 'w', encoding='utf-8') as fp:
        json.dump(parsed_for_store, fp, ensure_ascii=False, indent=2)
    with open(os.path.join(folder, 'meta.json'), 'w', encoding='utf-8') as fp:
        json.dump(meta, fp, ensure_ascii=False, indent=2)

    try:
        from apps.complaint_app.experience_loader import get_expert_experience_cached

        get_expert_experience_cached(ttl_seconds=60, force=True)
    except Exception:
        pass

    return {
        'id': upload_id,
        'created_at': meta['created_at'],
        'file_name': meta['file_name'],
        'rows_count': meta['rows_count'],
        'overview_count': meta['overview_count'],
    }, 200


def list_uploads(base_dir: str) -> list[dict]:
    if not os.path.isdir(base_dir):
        return []
    items = []
    for name in os.listdir(base_dir):
        folder = os.path.join(base_dir, name)
        if not os.path.isdir(folder):
            continue
        meta_path = os.path.join(folder, 'meta.json')
        if not os.path.isfile(meta_path):
            continue
        try:
            with open(meta_path, 'r', encoding='utf-8') as fp:
                meta = json.load(fp)
            items.append({
                'id': meta.get('id', name),
                'created_at': meta.get('created_at'),
                'file_name': meta.get('file_name'),
                'rows_count': meta.get('rows_count', 0),
                'overview_count': meta.get('overview_count', 0),
            })
        except Exception:
            continue
    items.sort(key=lambda x: (x.get('created_at') or ''), reverse=True)
    return items


def get_upload(base_dir: str, upload_id: str) -> tuple[dict, int]:
    folder = os.path.join(base_dir, upload_id)
    if not os.path.isdir(folder):
        return {'error': 'Not found'}, 404
    meta_path = os.path.join(folder, 'meta.json')
    parsed_path = os.path.join(folder, 'parsed.json')
    try:
        with open(meta_path, 'r', encoding='utf-8') as fp:
            meta = json.load(fp)
        with open(parsed_path, 'r', encoding='utf-8') as fp:
            parsed = json.load(fp)
    except Exception:
        return {'error': '读取数据失败'}, 500

    if isinstance(parsed, dict):
        parsed_list = [{k: v} for k, v in parsed.items()]
    else:
        parsed_list = parsed
    return {'meta': meta, 'parsed': parsed_list}, 200


def resolve_download_path(base_dir: str, upload_id: str) -> tuple[str | None, int]:
    folder = os.path.join(base_dir, upload_id)
    if not os.path.isdir(folder):
        return None, 404
    path_xlsx = os.path.join(folder, 'original.xlsx')
    path_xls = os.path.join(folder, 'original.xls')
    if os.path.isfile(path_xlsx):
        return path_xlsx, 200
    if os.path.isfile(path_xls):
        return path_xls, 200
    return None, 404
