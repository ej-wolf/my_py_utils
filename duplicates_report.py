""" Advanced duplicate reports built on top of compare-report rows.

    use cases: from compare_report import Report import duplicates_report as dr

        report = Report.from_dirs('src_dir', 'dup_dir', cutoff=None, output_mode='quiet')
        dr.quick_report(src_dir, report) : Print a compact duplicate summary and return the summary data.
        report = Report.from_dirs('src_dir', 'dup_dir', cutoff=None, file_type='same_loose')
        dr.full_report(src_dir, report)  : Reuse a same-family-only report without extra duplicate logic changes.
        dr.full_report(['src_dir', 'extra_dir'], report, threshold=0.98, size_unit='MB'):
                        Print one row per duplicate match and return grouped duplicate data.
"""

from __future__ import annotations
from pathlib import Path
#* local imports
from my_local_utils import collection, print_color
from compare_files import DEFAULT_CUTOFF
from compare_report import Report, ReportView


_SIZE_FACTORS = {'B': 1, 'kB': 1000, 'MB': 1000**2, 'GB': 1000**3}
BAR_W = 40 #36

def _normalize_size_unit(size_unit: str) -> str:
    aliases = {'KB': 'kB', 'kb': 'kB', 'mb': 'MB', 'gb': 'GB', 'b': 'B'}
    size_unit = aliases.get(size_unit, size_unit)
    if size_unit not in _SIZE_FACTORS:
        raise ValueError("size_unit must be one of: 'B', 'kB', 'MB', 'GB'")
    return size_unit


def _format_size(size_bytes: int, size_unit: str) -> str:
    factor = _SIZE_FACTORS[size_unit]
    value = size_bytes / factor
    if size_unit == 'B':
        return f'{int(round(value)):,} {size_unit}'
    return f'{value:,.3g} {size_unit}'


def _short_path(path_str: str, max_len: int) -> str:
    text = str(path_str)
    if len(text) <= max_len:
        return text
    return text[-max_len:]


def _format_path_cell(path_str: str, width: int) -> str:
    text = str(path_str)
    if len(text) <= width:
        return f'{text:<{width}}'
    return f'{text[-width:]:>{width}}'


def _print_progress(label: str, current: int, total: int, last_pct: int = -1) -> int:
    if total <= 0:
        return last_pct

    pct = int((100 * current) / total)
    if pct == last_pct and current < total:
        return last_pct

    filled = int((BAR_W * current) / total)
    bar = '#' * filled + '.' * (BAR_W - filled)
    line = f'{label:<18} [{bar}] {pct:3d}% ({current}/{total})'
    print(f'\r{line}', end='', flush=True)
    if current >= total:
        print(f'\r{" " * len(line)}\r', end='', flush=True)
    return pct


def _coerce_report_rows(report) -> list[dict]:
    if isinstance(report, (Report, ReportView)):
        return report.rows
    if isinstance(report, list) and all(isinstance(row, dict) for row in report):
        return report
    raise TypeError('report must be Report, ReportView, or list[dict]')


def _dedupe_paths(paths) -> list[Path]:
    unique = []
    seen = set()
    for item in paths:
        path = Path(item)
        key = path.resolve(strict=False)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def _normalize_source_files(file1, subdir=True) -> list[Path]:
    source_files = []
    for item in collection(file1):
        path = Path(item)
        if path.is_file():
            source_files.append(path)
            continue
        if path.is_dir():
            if subdir:
                source_files.extend(f for f in path.rglob('*') if f.is_file())
            else:
                source_files.extend(f for f in path.iterdir() if f.is_file())
            continue
        print_color(f'Warning: {path} is not a valid file or directory.', 'y')
    return _dedupe_paths(source_files)


def _build_source_entries(file1, subdir=True) -> list[dict]:
    entries = []
    for path in _normalize_source_files(file1, subdir=subdir):
        try:
            size = path.stat().st_size
        except OSError as exc:
            print_color(f'Warning: failed to stat source file {path}: {exc}', 'y')
            continue
        entries.append({
            'path': path,
            'resolved': path.resolve(strict=False),
            'file1': str(path),
            'size_bytes': size,
        })
    return entries


def _build_dup_data(file1, report, **kwargs) -> dict:
    threshold = float(kwargs.get('threshold', 1 - DEFAULT_CUTOFF))
    subdir = kwargs.get('subdir', True)
    size_unit = _normalize_size_unit(kwargs.get('size_unit', 'kB'))
    cli_print = kwargs.get('cli_print', True)

    source_entries = _build_source_entries(file1, subdir=subdir)
    rows = _coerce_report_rows(report)

    by_file1 = {entry['resolved']: {'path' : entry['path'],
                                    'file1': entry['file1'],
                                    'size_bytes': entry['size_bytes'],
                                    'complete_matches': [],
                                    'partial_matches': [],
                                    } for entry in source_entries
                }
    last_pct = -1
    for idx, row in enumerate(rows, 1):
        file1_path = row.get('file1')
        file2_path = row.get('file2')
        if not file1_path or not file2_path:
            if cli_print:
                last_pct = _print_progress('analyzing rows', idx, len(rows), last_pct)
            continue

        key = Path(file1_path).resolve(strict=False)
        entry = by_file1.get(key)
        if entry is None:
            if cli_print:
                last_pct = _print_progress('analyzing rows', idx, len(rows), last_pct)
            continue

        similarity = float(row.get('similarity', 0.0) or 0.0)
        complete = float(row.get('complete', 0.0) or 0.0)
        match = {'file2': str(file2_path),
                 'similarity': similarity,
                 'complete': complete,
                 'size_bytes': int(row.get('size', 0) or 0),
                 'row': row,
                 }
        if similarity >= 1.0 and complete >= 1.0:
            entry['complete_matches'].append(match)
        elif complete >= 1.0 > similarity >= threshold:
            entry['partial_matches'].append(match)
        if cli_print:
            last_pct = _print_progress('analyzing rows', idx, len(rows), last_pct)

    groups = []
    last_pct = -1
    for idx, entry in enumerate(source_entries, 1):
        group = by_file1[entry['resolved']]
        if group['complete_matches'] or group['partial_matches']:
            group['complete_matches'].sort(key=lambda m: (-m['similarity'], m['file2']))
            group['partial_matches'].sort(key=lambda m: (-m['similarity'], m['file2']))
            groups.append(group)
        if cli_print:
            last_pct = _print_progress('grouping files', idx, len(source_entries), last_pct)

    dup_keys = {Path(group['file1']).resolve(strict=False) for group in groups}
    unique_files = [entry['file1'] for entry in source_entries if entry['resolved'] not in dup_keys]

    return {'threshold': threshold,
            'size_unit': size_unit,
            'source_files': source_entries,
            'groups': groups,
            'unique_files': unique_files,
            }


def _build_quick_summary(data: dict) -> dict:
    source_files = data['source_files']
    groups = data['groups']

    total_files = len(source_files)
    total_size = sum(item['size_bytes'] for item in source_files)

    complete_groups = [g for g in groups if g['complete_matches']]
    partial_groups = [g for g in groups if g['partial_matches']]

    summary = {
        'threshold': data['threshold'],
        'size_unit': data['size_unit'],
        'unique_files': list(data['unique_files']),
        'all_file1': {
            'files': total_files,
            'size_bytes': total_size,
        },
        'have_complete_dups': {
            'files': len(complete_groups),
            'size_bytes': sum(g['size_bytes'] for g in complete_groups),
        },
        'total_complete_dups': {
            'files': sum(len(g['complete_matches']) for g in complete_groups),
            'size_bytes': sum(sum(m['size_bytes'] for m in g['complete_matches']) for g in complete_groups),
        },
        'have_partial_dups': {
            'files': len(partial_groups),
            'size_bytes': sum(g['size_bytes'] for g in partial_groups),
        },
        'total_partial_dups': {
            'files': sum(len(g['partial_matches']) for g in partial_groups),
            'size_bytes': sum(sum(m['size_bytes'] for m in g['partial_matches']) for g in partial_groups),
        },
    }
    return summary


def quick_report(file1, report, **kwargs) -> dict:
    data = _build_dup_data(file1, report, **kwargs)
    summary = _build_quick_summary(data)
    size_unit = summary['size_unit']

    rows = [
        ('all file1', summary['all_file1']),
        ('have complete dups', summary['have_complete_dups']),
        ('total complete dups', summary['total_complete_dups']),
        ('have partial dups', summary['have_partial_dups']),
        ('total partial dups', summary['total_partial_dups']),
    ]

    cat_w = max(len('category'), *(len(label) for label, _ in rows))
    files_w = max(len('files'), *(len(str(item['files'])) for _, item in rows))
    size_values = [_format_size(item['size_bytes'], size_unit) for _, item in rows]
    size_w = max(len('size'), *(len(value) for value in size_values))

    print('\nDuplicates quick report')
    print(f"partial threshold: {summary['threshold']:.5f} | size unit: {size_unit}")
    print(f"{'category':{cat_w}} {'files':>{files_w}} {'size':>{size_w}}")
    print('-' * (cat_w + files_w + size_w + 2))

    for (label, item), size_text in zip(rows, size_values):
        print(f"{label:{cat_w}} {item['files']:>{files_w}} {size_text:>{size_w}}")

    print()

    return summary

    
def full_report(file1, report, **kwargs) -> dict:
    data = _build_dup_data(file1, report, **kwargs)
    size_unit = data['size_unit']
    original_w = int(kwargs.get('original_w', 48))
    duplicates_w = int(kwargs.get('duplicates_w', 60))

    print('\nDuplicates full report')
    print(f"partial threshold: {data['threshold']:.5f} | size unit: {size_unit}")
    print('-' * 60)

    if not data['groups']:
        print('No duplicate matches to display.\n')
        return data

    size_header = f'Size ({size_unit})'
    columns = { 'file1': max(original_w, len('Original File')),
                'size': max(len(size_header), max(len(_format_size(group['size_bytes'], size_unit)) for group in data['groups'])),
                'type': max(len('Dup Type'), len('Complete')),
                'similarity': max(len('Similarity'), len('1.000')),
                'file2': max(duplicates_w, len('Duplicates')),
                }
    header = (f"{'Original File':{columns['file1']}} | "
              f"{size_header:^{columns['size']}} | "
              f"{'Dup Type':^{columns['type']}} | "
              f"{'Similarity':^{columns['similarity']}} | "
              f"\t{'Duplicates':{columns['file2']}}")

    print(header)
    print('-' * len(header))

    sep_line = '-' * len(header)
    for group_idx, group in enumerate(data['groups'], 1):
        matches = [('complete', match) for match in group['complete_matches']]
        matches.extend(('partial', match) for match in group['partial_matches'])

        first = True
        for dup_type, match in matches:
            file1_text = _format_path_cell(group['file1'], columns['file1']) if first else ' ' * columns['file1']
            size_text = _format_size(group['size_bytes'], size_unit) if first else ''
            file2_text = _format_path_cell(match['file2'], columns['file2'])

            print(f"{file1_text} | "
                  f"{size_text:>{columns['size']}} | "
                  f"{dup_type.capitalize():<{columns['type']}} | "
                  f"{match['similarity']:>{columns['similarity']}.3f} | "
                  f"{file2_text}")
            first = False

        if group_idx < len(data['groups']):
            print(sep_line)

    print()
    return data
#324(,1,12)-
