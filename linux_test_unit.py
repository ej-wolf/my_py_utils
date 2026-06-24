import time
import io
import fnmatch
import contextlib
from pathlib import Path
from tempfile import TemporaryDirectory

from compare_files import compare_files, compare_dirs, DEFAULT_CUTOFF, filter_results
from compare_report import Report, ReportView
from duplicates_report import quick_report, full_report

#region Compare Files Test
def test_cf_unit(cases, cutoff=DEFAULT_CUTOFF, **kwargs):
    """Run selected bundled directory comparisons in both compare_bytes modes."""

    def _files_by_name(path):
        return {p.name: p for p in path.iterdir() if p.is_file()}

    def _normalize_case(case):
        if len(case) == 3:
            label, left, right = case
            return label, left, right, {}
        if len(case) == 4:
            label, left, right, opts = case
            if opts is None:
                opts = {}
            if not isinstance(opts, dict):
                raise TypeError('4th field in test case must be a dict')
            return label, left, right, dict(opts)
        raise ValueError('Each test case must have 3 or 4 items')

    def _capture_if_quiet(mode, func, *args, **kwargs):
        if mode == 'quiet':
            with contextlib.redirect_stdout(io.StringIO()):
                return func(*args, **kwargs)
        return func(*args, **kwargs)

    rows = []
    default_output_mode = kwargs.get('output_mode', 'progress')
    for case in cases:
        label, left_dir, right_dir, opts = _normalize_case(case)
        use_compare_dirs = bool(opts.get('subdir')) or ('mask' in opts) or ('file_type' in opts)
        case_cutoff = opts.get('cutoff', cutoff)
        auto_output_mode = 'progress' if use_compare_dirs else default_output_mode
        case_output_mode = opts.get('output_mode', auto_output_mode)

        for cmp_b in (True, False):
            start = time.perf_counter()
            similarities = []
            diff_bytes = 0
            identical = 0
            files_compared = 0
            left_only = right_only = 'n/a'

            if use_compare_dirs:
                compare_kwargs = dict(opts)
                compare_kwargs.pop('cutoff', None)
                compare_kwargs.pop('compare_bytes_modes', None)
                compare_kwargs.setdefault('output_mode', case_output_mode)
                dir_rows = _capture_if_quiet(
                    compare_kwargs.get('output_mode', case_output_mode),
                    compare_dirs,
                    left_dir, right_dir,
                    cutoff=case_cutoff,
                    compare_bytes=cmp_b,
                    **compare_kwargs,
                )

                files_compared = len(dir_rows)
                for row in dir_rows:
                    similarities.append(row['similarity'])
                    diff_bytes += row['diff_bytes']
                    if row['diff_bytes'] == 0:
                        identical += 1
            else:
                left_files = _files_by_name(left_dir)
                right_files = _files_by_name(right_dir)

                common_names = sorted(set(left_files) & set(right_files))
                left_only = len(sorted(set(left_files) - set(right_files)))
                right_only = len(sorted(set(right_files) - set(left_files)))
                files_compared = len(common_names)

                for name in common_names:
                    info = _capture_if_quiet(
                        case_output_mode,
                        compare_files,
                        left_files[name], right_files[name],
                        cutoff=case_cutoff,
                        output_mode=case_output_mode,
                        compare_bytes=cmp_b,
                    )

                    similarities.append(info['similarity'])
                    diff_bytes += info['diff_bytes']
                    if info['diff_bytes'] == 0:
                        identical += 1

            elapsed = time.perf_counter() - start
            avg_similarity = sum(similarities) / len(similarities) if similarities else 0.0
            min_similarity = min(similarities) if similarities else 0.0

            rows.append({ 'case': label,
                          'compare_bytes': cmp_b,
                          'files': files_compared,
                          'identical': identical,
                          'avg_similarity': avg_similarity,
                          'min_similarity': min_similarity,
                          'diff_bytes': diff_bytes,
                          'elapsed_sec': elapsed,
                          'left_only': left_only,
                          'right_only': right_only,
                          })

    print(f"{'case':28} {'byte mode':10} {'files':>5} {'ident':>5} "
          f"{'avg_sim':>10} {'min_sim':>10} {'diff_bytes':>12} {'sec':>8}")

    for row in rows:
        print(f"{row['case'][:28]:28} "
              f"{str('on' if row['compare_bytes'] else 'off'):^10} "
              f"{row['files']:^5d} "
              f"{row['identical']:5d} "
              f"{row['avg_similarity']:10.5f} "
              f"{row['min_similarity']:10.5f} "
              f"{row['diff_bytes']:12,d} "
              f"{row['elapsed_sec']:8.3f}")

        if row['left_only'] != 'n/a' and (row['left_only'] or row['right_only']):
            print(f"  unmatched files: left_only={row['left_only']} right_only={row['right_only']}")
        elif row['left_only'] == 'n/a':
            print("  unmatched files: n/a (directory discovery case)")

    return rows
#endregion


#region Compare Report Test
def test_cr_unit(test_root: Path | str = 'test_data', tst_msk='*.*', **kwargs): #122
    """ Run a small smoke test for the report wrapper on a given directory tree."""

    def _check(name: str, cond: bool, detail=''):
        """ Collect pass/fail checks for the report test summary."""
        # print(name)
        checks.append({'check': name, 'ok': bool(cond), 'detail': detail})

    def _capture_output(action):
        """ capture printed output for print/list smoke tests """
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            action()
        return buf.getvalue()

    def _match_mask(path_str: str) -> bool:
        """ apply the filename mask used in where() checks."""
        return fnmatch.fnmatch(Path(path_str).name, tst_msk)

    def _kind(path_str: str) -> str:
        """ Group files into broad families for loose pairing checks."""
        suffix = Path(path_str).suffix.lower()
        if suffix in {'.avi', '.flv', '.m4v', '.mkv', '.mov', '.mp4', '.mpeg', '.mpg', '.ts', '.webm', '.wmv'}:
            return 'videos'
        if suffix in {'.bmp', '.gif', '.jpeg', '.jpg', '.png', '.tif', '.tiff', '.webp'}:
            return 'pictures'
        if suffix in {'.7z', '.bz2', '.gz', '.rar', '.tar', '.tgz', '.xz', '.zip'} or Path(path_str).name.lower().endswith(('.tar.gz', '.tar.bz2', '.tar.xz')):
            return 'archives'
        return 'other'

    # test_root = Path(test_root)
    report = Report.from_dirs(test_root, subdir=True, output_mode=kwargs.get('output_mode', 'progress'))

    sample_view= report[0:min(5, len(report))]
    sample_row = report[0] if len(report) > 0 else None
    mask_view  = report.where('file1', tst_msk)
    exact_view = report.filter(cutoff=0.0, criteria='dif')
    pairs_demo = exact_view[0:min(4, len(exact_view))]

    checks = []
    _check('column access', len(report['file1']) == len(report))
    _check('slice returns view', isinstance(sample_view, ReportView))
    _check('row access returns dict', sample_row is None or isinstance(sample_row, dict))
    _check('mask where on file1', all(_match_mask(p) for p in mask_view['file1']), f'matches={len(mask_view)}')
    _check('similarity exact-only', len(report.where('similarity', '>', 0.94)) < len(report.where('sim_max', '>', 0.94)))
    _check('filter delegates', len(report.filter(cutoff=0.05, criteria='dif')) == len(filter_results(report.rows, cutoff=0.05, criteria='dif')))

    sorted_view = report.sort(criteria='sim_max', order='decrease')
    similarities = sorted_view['similarity']
    _check('sort delegates', similarities == sorted(similarities, reverse=True))

    table_out = _capture_output(lambda: report.print(summary=False))
    _check('print output', bool(table_out.strip()))

    pairs_out = _capture_output(lambda: pairs_demo.list_pairs(cutoff=0.0, criteria='dif'))
    _check('list_pairs output', bool(pairs_out.strip()))

    with TemporaryDirectory() as td:
        target = report.save(Path(td))
        _check('save report', target.is_file(), str(target))

    row_a = {'file1': 'a', 'file2': 'b', 'size': 1, 'similarity': 1.0, 'diff_bytes': 0, 'complete': 1.0}
    row_b = {'file1': 'c', 'file2': 'd', 'size': 2, 'similarity': 0.9, 'diff_bytes': 1, 'complete': 1.0}
    merged = Report([dict(row_a)]).append(Report([dict(row_a), dict(row_b)]))
    _check('append dedup merge', len(merged) == 2)

    plus_merged = Report([dict(row_a)]) + Report([dict(row_a), dict(row_b)])
    _check('plus matches append', len(plus_merged) == 2 and plus_merged['file1'] == merged['file1'])

    delete_report = Report([dict(row_a), dict(row_b)])
    _check('delete row', delete_report.delete(0) is delete_report and len(delete_report) == 1 and delete_report[0]['file1'] == 'c')

    drop_report = Report([dict(row_a), dict(row_b), {'file1': 'e', 'file2': 'f', 'size': 3, 'similarity': 0.8, 'diff_bytes': 2, 'complete': 1.0}])
    _check('drop rows', drop_report.drop([0, -1]) is drop_report and len(drop_report) == 1 and drop_report[0]['file1'] == 'c')

    conflict_ok = False
    try:
        Report([dict(row_a)]).append([dict(row_a), {**row_a, 'similarity': 0.5}])
    except ValueError:
        conflict_ok = True
    _check('append conflict guard', conflict_ok)

    # mix_dir = test_root/'mix'
    # if mix_dir.is_dir():
    #     strict_rows = compare_dirs(mix_dir, mix_dir, cutoff=None, output_mode='quiet', file_type='same_strict')
    #     loose_rows = compare_dirs(mix_dir, mix_dir, cutoff=None, output_mode='quiet', file_type='same_loose')
    #     video_rows = compare_dirs(mix_dir, mix_dir, cutoff=None, output_mode='quiet', file_type='videos')
    #     strict_ok = all(Path(row['file1']).suffix.lower() == Path(row['file2']).suffix.lower() for row in strict_rows)
    #     loose_ok  = all(_kind(row['file1']) == _kind(row['file2']) for row in loose_rows)
    #     videos_ok = all(_kind(row['file1']) == _kind(row['file2']) == 'videos' for row in video_rows)
    #     _check('same_strict pairing', strict_ok, f'rows={len(strict_rows)}')
    #     _check('same_loose pairing', loose_ok, f'rows={len(loose_rows)}')
    #     _check('videos pairing', videos_ok, f'rows={len(video_rows)}')
    #     loose_report = Report.from_dirs(mix_dir, mix_dir, cutoff=None, output_mode='quiet', file_type='same_loose')
    #     _check('Report.from_dirs same_loose', len(loose_report) == len(loose_rows), f'rows={len(loose_report)}')

    passed = sum(item['ok'] for item in checks)

    print('\n *** Visual demo ***')
    print(f"slice returns view: {sample_view!r}")
    print("slice file1 sample:")
    for item in sample_view['file1'][:3]:
        print(f'  {item}')
    # print('row access sample:')
    # print(sample_row)
    print(f"\nmask where sample: report.where('file1', {tst_msk!r})['file2'][:3]")
    if len(mask_view) == 0:
        print('  <no matches>')
    else:
        for item in mask_view['file2'][:3]:
            print(f'  {item}')
    print("\nlist_pairs sample:")
    print(pairs_out.strip() if pairs_out.strip() else '  <no exact pairs>')
    print('-' * 72)

    print(f"{'check':28} {'result':8} detail")
    for item in checks:
        print(f"{item['check'][:28]:28} {('PASS' if item['ok'] else 'FAIL'):8} {item['detail']}")

    print('-' * 72)
    print(f'passed: {passed}/{len(checks)}')
#endregion


#region Duplicates Report Test
def test_dup_unit(src_dir, trg_dir, **kwargs):

    checks = []
    def _check(name: str, cond: bool):
        checks.append({'check': name, 'ok': bool(cond)})

    label = kwargs.get('label')
    if label is None:
        left_name = Path(src_dir).name if isinstance(src_dir, (str, Path)) else 'source list'
        right_name = Path(trg_dir).name if isinstance(trg_dir, (str, Path)) else 'target list'
        label = f'{left_name} vs {right_name}'

    size_unit = 'kB'
    print_unique = kwargs.get('print_unique', False)
    output_mode = kwargs.get('output_mode', 'progress')
    cli_print = kwargs.get('cli_print', True)

    print(f'\n=== {label} ===')
    print(f'building compare rows: {label}')
    rows = compare_dirs(src_dir, trg_dir, cutoff=None, subdir=True, output_mode=output_mode)
    print(f'compare rows ready: {len(rows)}')

    report = Report(rows)
    quick_data = quick_report(src_dir, report, size_unit=size_unit, subdir=True, cli_print=cli_print)
    full_data = full_report(src_dir, report, size_unit=size_unit, subdir=True, cli_print=cli_print)

    if print_unique:
        print('\nUnique Files:')
        if quick_data['unique_files']:
            for path_str in quick_data['unique_files']:
                print(path_str)
        else:
            print('<none>')

    _check(f'{label} quick totals', quick_data['all_file1']['files'] > 0)
    _check(f'{label} report rows', isinstance(report.rows, list))
    _check(f'{label} full data shape', 'groups' in full_data and 'source_files' in full_data)

    passed = sum(item['ok'] for item in checks)
    print(f'\nPassed: {passed}/{len(checks)}')
    for item in checks:
        print(f"{'OK' if item['ok'] else 'FAIL'} - {item['check']}")
#endregion

#* local runners

def get_op_mod():
    from compare_files import _CompareRenderer
    vmodes = dict(enumerate(_CompareRenderer.get_modes(), 1))
    m = input('\n'.join([f"{k})\t{v}" for k,v in vmodes.items()])+'\n')
    return vmodes[int(m)]

if __name__ == '__main__':
    import time
    test_root = Path('test_data')

    ws_vids = "/mnt/local-data/Projects/Wesmart/Video-datasets/wesmart"
    mix_dir = test_root/'mix'
    cache_dir = test_root/'cache'
    other_dirs = sorted(p for p in test_root.iterdir() if p.is_dir() and p.name!='mix')
    tst_cases = (['try_01 vs try_02', test_root/'try_01_cf', test_root/'try_02_cf'],
                 ['try_03 vs try_04', test_root/'try_03',    test_root/'try_04'],
                 ['try_01 vs try_03', test_root/'try_01_cf', test_root/'try_03'],
                 ['try_01 vs try_04', test_root/'try_01_cf', test_root/'try_04'],
                 ['events vs mix'   , test_root/'events',    test_root/'mix',{'subdir': True}],
                 #['ws videos'   , ws_vids,  ws_vids,  {'subdir': True, 'output_mode':'progress'}],
                 )
    ws_tst = ('ws videos'   , ws_vids,  ws_vids,  {'subdir': True, 'output_mode': 'progress'})
    t0 = time.time()
    # test_cf_unit(cases=tst_cases)
    print('\n' + '=' * 80 + '\n')
    opmod = 'cli-flash' # 'prog' if input(" 1) progress (1/p)\n 2) both (2/b)\n:") in ('1', 'p', 'P') else 'both'
    opmod = get_op_mod()
    test_cr_unit(test_root, output_mode=opmod);
    # print('\n' + '=2' * 80 + '\n')
    test_dup_unit(cache_dir, mix_dir, label='cache vs mix', output_mode=opmod);   print('\n' + '=' * 80 + '\n')
    # test_dup_unit(other_dirs, mix_dir, label='all dirs except mix vs mix', print_unique=True)
    print(f"Testing duration = {time.time()-t0:.3f}")
#261(,4,)-> 251(,2,1)

