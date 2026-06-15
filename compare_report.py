""" Query wrapper for compare-report rows.
    use cases:
    report = Report.from_dirs('dir_a', 'dir_b', subdir=True, output_mode='quiet')
        Build a queryable report from directory comparison.
    report = Report.from_dirs('data/my_dir/*.zip', 'other_dir/**/*.zip', output_mode='progress')
        Build a report from explicit file-glob inputs.
    report = Report.from_dirs('dir_a', 'dir_b', file_type=['mp4', 'wmv'])
        Compare only selected file extensions on both sides.
    report = Report.from_dirs('dir_a', 'dir_b', file_type='same_loose')
        Compare only same-family files, e.g. videos to videos and archives to archives.
    report = Report.from_dirs('dir_a', 'dir_b', file_type='videos')
        Compare only video-family files.
    report['file1']             :   Return list of file1 paths for all rows.
    report[5:10]['file2']       :   Return list of file2 paths from rows 5 to 10.
    report.where('file1', 'NV*'):   Select rows whose where 'file1' name matches the mask.
    report.where('similarity', '>', 0.9)['file1']:
                                    Select rows subset with similarity above 0.9,
                                    then return list of 'file1'paths of this subset
    report.filter(0.8, criteria='complete')
                                    Keep rows where at least 80% of the file was searched.
    report.sort(criteria='sim_max', order='decrease').print()
                                    Sort rows by max similarity and print the result table.
    report.save('report.pcl')   :   Save the wrapped report as pickle.
"""

import fnmatch
from collections.abc import Iterable
from pathlib import Path
from typing import Iterator
from compare_files import (compare_dirs, filter_results, sort_results, print_cmp_info,
                           list_pairs, save_report,)

_VISIBLE_FIELDS = {'file1', 'file2', 'size', 'similarity', 'diff_bytes', 'complete'}
_COLUMN_ALIASES = {'file_1': 'file1', 'file_2': 'file2'}
_CRITERIA_ALIASES = {'dif': 'difference', 'sim': 'similarity', 'sim_max': 'similarity_max',}
_NUMERIC_FIELDS = {'size', 'similarity', 'diff_bytes', 'complete', 'difference', 'similarity_max'}
_COMPARISON_OPS = {'==', '!=', '>', '>=', '<', '<='}
_DEFULT_CUTOFF = 0.05


class _ReportBase:
    def __init__(self, rows: list[dict]):
        self._rows = rows

    @property
    def rows(self) -> list[dict]:
        return self._rows

    def __len__(self) -> int:
        return len(self._rows)

    def __iter__(self) -> Iterator[dict]:
        return iter(self._rows)

    def __repr__(self) -> str:
        return f'{self.__class__.__name__}(rows={len(self)})'

    def __add__(self, other):
        merged = Report(list(self._rows))
        return merged.append(other)

    def __getitem__(self, key):
        if isinstance(key, str):
            field = self._normalize_column(key)
            return [self._get_field_value(row, field) for row in self._rows]
        if isinstance(key, slice):
            return ReportView(self._rows[key])
        if isinstance(key, int):
            if -len(self._rows) <= key < len(self._rows):
                return self._rows[key]
            print(f'[WARN] index {key} out of range: 0-{len(self._rows)} ')
            return None
        raise TypeError(f'Unsupported index type: {type(key).__name__}')

    def where(self, field: str, op_or_value, value=None):
        field = self._normalize_criteria(field)
        op, expected = self._normalize_where_args(op_or_value, value)
        matched = [row for row in self._rows if self._row_matches(row, field, op, expected)]
        return ReportView(matched)

    def filter(self, cutoff=_DEFULT_CUTOFF, criteria='difference'):
        criteria = self._normalize_criteria(criteria)
        return ReportView(filter_results(self._rows, cutoff=cutoff, criteria=criteria))

    def sort(self, criteria='difference', order='increase', cutoff=None):
        criteria = self._normalize_criteria(criteria)
        kwargs = {'cutoff': cutoff} if cutoff is not None else {}
        return ReportView(sort_results(self._rows, criteria=criteria, order=order, **kwargs))

    def print(self, **kwargs):
        print_cmp_info(self._rows, **kwargs)

    def list_pairs(self, **kwargs):
        list_pairs(self._rows, **kwargs)

    def save(self, path: Path | str | None = None, **kwargs):
        return save_report(self._rows, path=path, **kwargs)

    def append(self, other):
        rows_to_add = self._coerce_rows(other)
        by_pair = {_pair_key(row): row for row in self._rows}

        for row in rows_to_add:
            key = _pair_key(row)
            current = by_pair.get(key)
            if current is None:
                self._rows.append(row)
                by_pair[key] = row
                continue

            if _canonical_row(current) != _canonical_row(row):
                raise ValueError(f'Conflicting data for pair: {key}')

        return self

    def unique(self, field: str) -> set:
        field = self._normalize_column(field)
        return set(self[field])

    def delete(self, index: int):
        return self.drop(index)

    def drop(self, indexes):
        if isinstance(indexes, int):
            indexes = [indexes]
        elif not isinstance(indexes, Iterable):
            raise TypeError('drop() expects an int or iterable of ints')

        valid = set()
        for index in indexes:
            if not isinstance(index, int):
                raise TypeError('drop() indexes must be ints')

            resolved = index if index >= 0 else len(self._rows) + index
            if 0 <= resolved < len(self._rows):
                valid.add(resolved)
            else:
                print(f'[WARN] index {index} out of range: 0-{len(self._rows)} ')

        for index in sorted(valid, reverse=True):
            del self._rows[index]

        return self

    def _normalize_column(self, field: str) -> str:
        field = _COLUMN_ALIASES.get(field, field)
        if field not in _VISIBLE_FIELDS:
            raise KeyError(f'Unsupported column: {field}')
        return field

    def _coerce_rows(self, other) -> list[dict]:
        if isinstance(other, _ReportBase):
            return other.rows
        if isinstance(other, list):
            if all(isinstance(row, dict) for row in other):
                return other
            raise TypeError('List input for append() must contain row dicts')
        raise TypeError('append() expects Report, ReportView, or list[dict]')

    def _normalize_criteria(self, field: str) -> str:
        field = _COLUMN_ALIASES.get(field, field)
        return _CRITERIA_ALIASES.get(field, field)

    def _normalize_where_args(self, op_or_value, value):
        if value is None:
            if isinstance(op_or_value, str) and any(ch in op_or_value for ch in '*?['):
                return 'mask', op_or_value
            return '==', op_or_value

        op = op_or_value
        if op == '=':
            op = '=='
        if op not in _COMPARISON_OPS and op != 'mask':
            raise ValueError(f'Unsupported operator: {op}')
        return op, value

    def _get_similarity(self, row: dict) -> float | None:
        return row.get('similarity', row.get('info', {}).get('similarity'))

    def _get_complete(self, row: dict) -> float | None:
        return row.get('complete', row.get('info', {}).get('complete'))

    def _get_field_value(self, row: dict, field: str):
        if field in row:
            return row[field]
        return row.get('info', {}).get(field)

    def _compare_values(self, actual, op: str, expected) -> bool:
        if op == '==':
            return actual == expected
        if op == '!=':
            return actual != expected
        if op == '>':
            return actual > expected
        if op == '>=':
            return actual >= expected
        if op == '<':
            return actual < expected
        if op == '<=':
            return actual <= expected
        raise ValueError(f'Unsupported operator: {op}')

    def _row_matches(self, row: dict, field: str, op: str, expected) -> bool:
        if field in ('difference', 'similarity', 'similarity_max'):
            actual = self._get_similarity(row)
            if actual is None:
                return False

            if field != 'similarity_max':
                complete = self._get_complete(row)
                if complete != 1.0:
                    return False

            if field == 'difference':
                actual = 1 - actual

            return self._compare_values(actual, op, expected)

        field = self._normalize_column(field)
        actual = self._get_field_value(row, field)
        if actual is None:
            return False

        if op == 'mask':
            if field not in ('file1', 'file2'):
                raise ValueError('mask operator is only supported for file1/file2')
            return fnmatch.fnmatch(Path(actual).name, expected)

        if field in ('file1', 'file2') and op not in ('==', '!='):
            raise ValueError(f'{op} is not supported for {field}')

        if field in _NUMERIC_FIELDS and not isinstance(expected, (int, float)):
            raise TypeError(f'Expected numeric value for {field}')

        return self._compare_values(actual, op, expected)


class Report(_ReportBase):
    @classmethod
    def from_dirs(cls, d1, d2=None, cutoff=_DEFULT_CUTOFF, update_cli=None, output_mode='quiet', **kwargs):
        return cls(compare_dirs(d1, d2=d2, cutoff=cutoff, update_cli=update_cli, output_mode=output_mode, **kwargs))


class ReportView(_ReportBase):
    pass


def as_report(rows: list[dict]) -> Report:
    return Report(rows)

# ---------------------------------------------------------------------------
# * Class helpers
# --------------------------------------------------------------------------
def _pair_key(row: dict) -> tuple[str, str]:
    return row.get('file1'), row.get('file2')


def _canonical_row(row: dict) -> dict:
    data = dict(row)
    if 'chunks num' in data and 'chunks_num' not in data:
        data['chunks_num'] = data.pop('chunks num')

    info = data.get('info')
    if isinstance(info, dict):
        info = dict(info)
        if 'chunks num' in info and 'chunks_num' not in info:
            info['chunks_num'] = info.pop('chunks num')
        data['info'] = info

    return data
