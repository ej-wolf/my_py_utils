""" Query wrapper for compare-report rows.
    use cases:
    report = Report.from_dirs('dir_a', 'dir_b', subdir=True, update_cli=False)
        Build a queryable report from directory comparison.
    report['file1']             :   Return list of file1 paths for all rows.
    report[5:10]['file2']       :   Return list of file2 paths from rows 5 to 10.
    report.where('file1', 'NV*'):   Select rows whose where 'file1' name matches the mask.
    report.where('similarity', '>', 0.9)['file1']:
                                    Select rows subset with similarity above 0.9,
                                    then return list of 'file1'paths of this subset
    report.sort(criteria='sim_max', order='decrease').print()
                                    Sort rows by max similarity and print the result table.
    report.save('report.pcl')   :   Save the wrapped report as pickle.
"""

from pathlib import Path
from typing import Iterator
import fnmatch
import io, contextlib
from tempfile import TemporaryDirectory
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

    def __getitem__(self, key):
        if isinstance(key, str):
            field = self._normalize_column(key)
            return [self._get_field_value(row, field) for row in self._rows]
        if isinstance(key, slice):
            return ReportView(self._rows[key])
        if isinstance(key, int):
            return self._rows[key]
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

    def _normalize_column(self, field: str) -> str:
        field = _COLUMN_ALIASES.get(field, field)
        if field not in _VISIBLE_FIELDS:
            raise KeyError(f'Unsupported column: {field}')
        return field

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
    def from_dirs(cls, d1, d2=None, cutoff=_DEFULT_CUTOFF, update_cli=True, **kwargs):
        return cls(compare_dirs(d1, d2=d2, cutoff=cutoff, update_cli=update_cli, **kwargs))


class ReportView(_ReportBase):
    pass


def as_report(rows: list[dict]) -> Report:
    return Report(rows)


def test_cr_unit(test_root: Path | str = 'test_data', tst_msk='*.*'):
    """Run a small smoke test for the report wrapper on a given directory tree."""

    def _check(name: str, cond: bool, detail=''):
        checks.append({'check': name, 'ok': bool(cond), 'detail': detail})

    def _capture_output(func, *args, **kwargs):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            func(*args, **kwargs)
        return buf.getvalue()

    def _match_mask(path_str: str) -> bool:
        return fnmatch.fnmatch(Path(path_str).name, tst_msk)

    test_root = Path(test_root)
    with contextlib.redirect_stdout(io.StringIO()):
        report = Report.from_dirs(test_root, update_cli=False, subdir=True)

    checks = []

    sample_view = report[0:min(5, len(report))]
    sample_row = report[0] if len(report) > 0 else None
    mask_view = report.where('file1', tst_msk)
    exact_view = report.filter(cutoff=0.0, criteria='dif')
    pairs_demo = exact_view[0:min(4, len(exact_view))]

    _check('column access', len(report['file1']) == len(report))
    _check('slice returns view', isinstance(sample_view, ReportView))
    _check('row access returns dict', sample_row is None or isinstance(sample_row, dict))
    _check('mask where on file1', all(_match_mask(p) for p in mask_view['file1']), f'matches={len(mask_view)}')
    _check('similarity exact-only', len(report.where('similarity', '>', 0.94)) < len(report.where('sim_max', '>', 0.94)))
    _check('filter delegates', len(report.filter(cutoff=0.05, criteria='dif')) == len(filter_results(report.rows, cutoff=0.05, criteria='dif')))

    sorted_view = report.sort(criteria='sim_max', order='decrease')
    similarities = sorted_view['similarity']
    _check('sort delegates', similarities == sorted(similarities, reverse=True))

    table_out = _capture_output(report.print, summary=False)
    _check('print output', bool(table_out.strip()))

    pairs_out = _capture_output(pairs_demo.list_pairs, cutoff=0.0, criteria='dif')
    _check('list_pairs output', bool(pairs_out.strip()))

    with TemporaryDirectory() as td:
        target = report.save(Path(td))
        _check('save report', target.is_file(), str(target))

    passed = sum(item['ok'] for item in checks)

    print('Visual demo')
    print(f"slice returns view: {sample_view!r}")
    print('slice file1 sample:')
    for item in sample_view['file1'][:3]:
        print(f'  {item}')
    print('row access sample:')
    print(sample_row)
    print(f"mask where sample: report.where('file1', {tst_msk!r})['file2'][:3]")
    if len(mask_view) == 0:
        print('  <no matches>')
    else:
        for item in mask_view['file2'][:3]:
            print(f'  {item}')
    print('list_pairs sample:')
    print(pairs_out.strip() if pairs_out.strip() else '  <no exact pairs>')
    print('-' * 72)

    print(f"{'check':28} {'result':8} detail")
    for item in checks:
        print(f"{item['check'][:28]:28} {('PASS' if item['ok'] else 'FAIL'):8} {item['detail']}")

    print('-' * 72)
    print(f"passed: {passed}/{len(checks)}")

    return {'passed': passed, 'total': len(checks), 'checks': checks}

if __name__ == '__main__':
    pass
    test_root = Path("test_data")
    test_cr_unit(test_root,'NV*')
