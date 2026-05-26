""" Archive-aware comparison helpers built on top of compare_files.py.

use cases:
    ok, report = search_archive('sample.zip', 'target_dir', threshold=0.95)
        Search all files inside one archive against files found in the target dir/tree.
        Returns a boolean success flag and a detailed member-level report.

    report = compare_with_archives(['left.zip'], ['right_dir'], threshold=0.95)
        Compare normal files and archives in one call.
        Normal files use the usual compare flow; archives produce one summary row per archive.

    report = compare_with_archives(['left.zip'], ['right.zip'], archive_pairing='all')
        Compare archive contents to another archive by searching member files across both archives.

main functions:
    search_archive(arch_file, target_dir, threshold=None, **kwargs)
        Search one archive against target files and return:
            (ok: bool, report: list[dict])
        The returned report contains one row per archived member plus a final archive-summary row.

    compare_with_archives(files1, files2=None, cutoff=DEFAULT_CUTOFF, update_cli=True, **kwargs)
        Run archive-aware comparison on file lists.
        If a file in files1 is an archive, the output contains one compact summary row for it,
        and the member-level matches are stored under row['info']['archive_report'].

useful kwargs:
    threshold=0.95
        Minimum exact similarity required for an archived member to count as matched.
        If omitted, the default is 1 - DEFAULT_CUTOFF.

    search_archives=True
        If True, archive files found in the target side are also opened and searched inside.

    archive_pairing='all'
        Member pairing mode for archive-to-archive comparison.
        Options: 'all', 'inner_path', 'basename'
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import fnmatch
import shutil
import subprocess

from compare_files import DEFAULT_CUTOFF, compare_files, compare_lists
from my_local_utils import collection

DEFAULT_ARCHIVE_THRESHOLD = 1.0 - DEFAULT_CUTOFF
_ARCHIVE_EXTS = {'.zip', '.7z', '.rar', '.tar', '.gz', '.bz2', '.xz', '.tgz', '.tbz', '.tbz2', '.txz',}
_TAR_EXTS = {'.tar', '.tar.gz', '.tar.bz2', '.tar.xz', '.tgz', '.tbz', '.tbz2', '.txz'}
_SEVEN_Z = shutil.which('7z') or shutil.which('7za')
_TAR = shutil.which('tar')


def _is_archive_path(path: str | Path) -> bool:
    p = Path(path)
    suffixes = ''.join(p.suffixes[-2:]) if len(p.suffixes) >= 2 else ''
    return p.suffix.lower() in _ARCHIVE_EXTS or suffixes.lower() in _TAR_EXTS


def _normalize_threshold(threshold: float | None) -> float:
    return DEFAULT_ARCHIVE_THRESHOLD if threshold is None else float(threshold)


def _run_command(cmd: list[str], label: str):
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        msg = res.stderr.strip() or res.stdout.strip() or f'{label} failed'
        raise RuntimeError(msg)


def _extract_archive(archive_path: Path, out_dir: Path):
    archive_path = Path(archive_path)
    out_dir.mkdir(parents=True, exist_ok=True)

    tar_sig = ''.join(archive_path.suffixes[-2:]).lower() if len(archive_path.suffixes) >= 2 else archive_path.suffix.lower()

    errors = []
    if tar_sig in _TAR_EXTS and _TAR:
        try:
            _run_command([_TAR, '-xf', str(archive_path), '-C', str(out_dir)], f'extract {archive_path}')
            return
        except Exception as exc:
            errors.append(str(exc))

    if _SEVEN_Z:
        try:
            _run_command([_SEVEN_Z, 'x', '-y', f'-o{out_dir}', str(archive_path)], f'extract {archive_path}')
            return
        except Exception as exc:
            errors.append(str(exc))

    if _TAR and tar_sig in _TAR_EXTS:
        try:
            _run_command([_TAR, '-xf', str(archive_path), '-C', str(out_dir)], f'extract {archive_path}')
            return
        except Exception as exc:
            errors.append(str(exc))

    raise RuntimeError('; '.join(errors) if errors else f'No backend available for {archive_path}')


def _member_display(archive_path: Path, rel_path: Path) -> str:
    return f'{archive_path}::{rel_path.as_posix()}'


def _collect_archive_members(archive_path: Path, root: Path) -> list[dict]:
    members = []
    for file_path in sorted(p for p in root.rglob('*') if p.is_file()):
        rel = file_path.relative_to(root)
        members.append({
            'path': file_path,
            'display': _member_display(archive_path, rel),
            'archive_path': str(archive_path),
            'member_path': rel.as_posix(),
            'basename': file_path.name,
            'size': file_path.stat().st_size,
        })
    return members


def _expand_input_item(item, mask=None, subdir=False) -> list[Path] | None:
    item_str = str(item)
    if not any(ch in item_str for ch in '*?['):
        p = Path(item)
        if not p.exists():
            print(f'Error: {p} is not a valid path.')
            return None
        if p.is_dir():
            if mask and subdir:
                return [f for f in p.rglob(mask) if f.is_file()]
            if mask and not subdir:
                return [f for f in p.glob(mask) if f.is_file()]
            if not mask and subdir:
                return [f for f in p.rglob('*') if f.is_file()]
            return [f for f in p.iterdir() if f.is_file()]
        if p.is_file():
            if mask and not fnmatch.fnmatch(p.name, mask):
                return []
            return [p]
        print(f'Error: {p} is not a valid path.')
        return None

    pattern = Path(item)
    root = Path(pattern.anchor) if pattern.is_absolute() else Path.cwd()
    rel_pattern = Path(*pattern.parts[1:]).as_posix() if pattern.is_absolute() else pattern.as_posix()
    matches = list(root.glob(rel_pattern))
    if not matches:
        print(f'Error: no paths match pattern {item!r}.')
        return None

    expanded = []
    for match in matches:
        sub_paths = _expand_input_item(match, mask=mask, subdir=subdir)
        if sub_paths is None:
            return None
        expanded.extend(sub_paths)
    return expanded


def _collect_input_files(items, mask=None, subdir=False) -> list[Path] | None:
    files = []
    for item in collection(items):
        expanded = _expand_input_item(item, mask=mask, subdir=subdir)
        if expanded is None:
            return None
        files.extend(expanded)
    return files


def _candidate_from_file(path: Path) -> dict:
    return {'path': path, 'display': str(path), 'archive_path': None, 'member_path': None,
            'basename': path.name, 'size': path.stat().st_size,}

def _build_target_candidates(target, temp_root: Path, search_archives=True, mask=None, subdir=False) -> list[dict] | None:
    files = _collect_input_files(target, mask=mask, subdir=subdir)
    if files is None:
        return None

    candidates = []
    for idx, file_path in enumerate(files):
        if _is_archive_path(file_path):
            if not search_archives:
                continue
            extract_dir = temp_root / f'target_archive_{idx:04d}'
            _extract_archive(file_path, extract_dir)
            candidates.extend(_collect_archive_members(file_path, extract_dir))
            continue
        candidates.append(_candidate_from_file(file_path))
    return candidates


def _filter_candidates(member: dict, candidates: list[dict], archive_pairing='all') -> list[dict]:
    if archive_pairing == 'all':
        return list(candidates)

    member_base = Path(member['member_path']).name
    filtered = []
    for cand in candidates:
        if archive_pairing == 'inner_path':
            if cand['member_path'] is not None and cand['member_path'] == member['member_path']:
                filtered.append(cand)
            elif cand['member_path'] is None and cand['basename'] == member_base:
                filtered.append(cand)
        elif archive_pairing == 'basename':
            if cand['basename'] == member_base:
                filtered.append(cand)
        else:
            raise ValueError("archive_pairing must be 'all', 'inner_path', or 'basename'")
    return filtered


def _best_match_key(item: dict) -> tuple:
    return (item['similarity'], -item['diff_bytes'], item['target'])


def _compare_member(member: dict, candidates: list[dict], threshold: float, archive_pairing='all', compare_bytes=True) -> dict:
    selected = _filter_candidates(member, candidates, archive_pairing=archive_pairing)
    best = None
    matches_above = []

    for cand in selected:
        info = compare_files(member['path'], cand['path'],
                             cutoff=None,
                             update_cli=False,
                             compare_bytes=compare_bytes,
                             list_chunks=False)
        match = {
            'target': cand['display'],
            'similarity': info['similarity'],
            'diff_bytes': info['diff_bytes'],
            'complete': info['complete'],
        }

        if best is None or _best_match_key(match) > _best_match_key(best):
            best = match
        if info['complete'] == 1.0 and info['similarity'] >= threshold:
            matches_above.append(match)

    if best is None:
        return {
            'file1': member['display'],
            'file2': '',
            'size': member['size'],
            'similarity': 0.0,
            'diff_bytes': member['size'],
            'chunks_num': 0,
            'complete': 0.0,
            'info': {
                'archive_path': member['archive_path'],
                'member_path': member['member_path'],
                'best_match': None,
                'matches_above_threshold': [],
                'matches_count': 0,
                'extra_matches_count': 0,
                'threshold': threshold,
                'note': 'no candidates',
            },
        }

    note = None
    if len(matches_above) > 1:
        note = f'{len(matches_above)} matches above threshold'
    elif best['similarity'] < threshold:
        note = 'best match below threshold'

    return {'file1': member['display'],
            'file2': best['target'],
            'size': member['size'],
            'similarity': best['similarity'],
            'diff_bytes': best['diff_bytes'],
            'chunks_num': 0,
            'complete': best['complete'],
            'info': {'archive_path': member['archive_path'],
                     'member_path': member['member_path'],
                     'best_match': best,
                     'matches_above_threshold': matches_above,
                     'matches_count': len(matches_above),
                     'extra_matches_count': max(0, len(matches_above) - 1),
                     'threshold': threshold,
                     'note': note,},
            }


def _archive_summary(archive_path: Path, member_rows: list[dict], threshold: float, archive_pairing='all') -> tuple[bool, dict]:
    total_size = sum(row['size'] for row in member_rows)
    if total_size > 0:
        weighted_similarity = sum(row['size'] * row['similarity'] for row in member_rows) / total_size
        weighted_complete = sum(row['size'] * row['complete'] for row in member_rows) / total_size
    else:
        weighted_similarity = 1.0
        weighted_complete = 1.0

    matched_rows = [row for row in member_rows if row['complete'] == 1.0 and row['similarity'] >= threshold]
    above_targets = []
    for row in member_rows:
        above_targets.extend(match['target'] for match in row['info']['matches_above_threshold'])

    matched_paths = sorted(set(above_targets))
    ok = len(matched_rows) == len(member_rows)
    summary = {
        'file1': str(archive_path),
        'file2': '; '.join(matched_paths) if matched_paths else '<no matches>',
        'size': total_size,
        'similarity': weighted_similarity,
        'diff_bytes': sum(row['diff_bytes'] for row in member_rows),
        'chunks_num': 0,
        'complete': weighted_complete,
        'info': {
            'is_archive_summary': True,
            'archive_path': str(archive_path),
            'archive_members_total': len(member_rows),
            'archive_members_matched': len(matched_rows),
            'archive_threshold': threshold,
            'archive_search_ok': ok,
            'archive_pairing': archive_pairing,
            'matched_paths': matched_paths,
            'extra_matches_total': sum(row['info']['extra_matches_count'] for row in member_rows),
            'archive_report': member_rows,
        },
    }
    return ok, summary


def search_archive(arch_file, target_dir, threshold=None, **kwargs) -> tuple[bool, list[dict]]:
    """Search one archive against files and archive-members collected from target paths."""

    archive_path = Path(arch_file)
    if not archive_path.is_file() or not _is_archive_path(archive_path):
        raise ValueError(f'{arch_file} is not a supported archive file')

    threshold = _normalize_threshold(threshold)
    search_archives = kwargs.get('search_archives', True)
    archive_pairing = kwargs.get('archive_pairing', 'all')
    compare_bytes = kwargs.get('compare_bytes', True)
    mask = kwargs.get('mask', None)
    subdir = kwargs.get('subdir', False)

    with TemporaryDirectory() as td:
        temp_root = Path(td)
        member_root = temp_root / 'source_archive'
        _extract_archive(archive_path, member_root)
        members = _collect_archive_members(archive_path, member_root)

        candidates = _build_target_candidates(target_dir, temp_root / 'targets',
                                              search_archives=search_archives,
                                              mask=mask,
                                              subdir=subdir)
        if candidates is None:
            return False, []

        member_rows = [
            _compare_member(member, candidates,
                            threshold=threshold,
                            archive_pairing=archive_pairing,
                            compare_bytes=compare_bytes)
            for member in members
        ]

    ok, summary = _archive_summary(archive_path, member_rows, threshold, archive_pairing=archive_pairing)
    return ok, member_rows + [summary]


def compare_with_archives(files1, files2=None, cutoff=DEFAULT_CUTOFF, update_cli=True, **kwargs) -> list[dict]:
    """Compare path collections, expanding archive files on the left into archive summary rows."""

    threshold = _normalize_threshold(kwargs.pop('threshold', None))
    search_archives = kwargs.get('search_archives', True)
    archive_pairing = kwargs.get('archive_pairing', 'all')
    mask = kwargs.get('mask', None)
    subdir = kwargs.get('subdir', False)

    left_files = _collect_input_files(files1, mask=mask, subdir=subdir)
    if left_files is None:
        return []

    same_source = files2 is None
    right_files = left_files if same_source else _collect_input_files(files2, mask=mask, subdir=subdir)
    if right_files is None:
        return []

    report = []
    for idx, left in enumerate(left_files):
        targets = right_files[idx + 1:] if same_source else right_files
        if not targets:
            continue

        if _is_archive_path(left):
            ok, archive_report = search_archive(left, targets,
                                                threshold=threshold,
                                                search_archives=search_archives,
                                                archive_pairing=archive_pairing,
                                                compare_bytes=kwargs.get('compare_bytes', True))
            if not archive_report:
                continue

            summary_row = dict(archive_report[-1])
            summary_info = dict(summary_row['info'])
            summary_info['archive_search_ok'] = ok
            summary_info['archive_report'] = archive_report[:-1]
            summary_row['info'] = summary_info
            report.append(summary_row)
            continue

        normal_targets = [target for target in targets if not _is_archive_path(target)]
        if not normal_targets:
            continue
        report.extend(compare_lists([left], normal_targets, cutoff=cutoff, update_cli=update_cli, **kwargs))

    return report


def test_ah_unit():
    """Run a small smoke test for archive_handling.py."""

    def _check(name: str, cond: bool, detail=''):
        checks.append({'check': name, 'ok': bool(cond), 'detail': detail})

    def _make_file(path: Path, text: str):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return path

    import zipfile

    checks = []
    with TemporaryDirectory() as td:
        root = Path(td)
        target = root / 'target'
        alpha = _make_file(target / 'copy_alpha.txt', 'alpha')
        beta = _make_file(target / 'b.txt', 'beta')

        source_zip = root / 'source.zip'
        with zipfile.ZipFile(source_zip, 'w') as zf:
            zf.writestr('a.txt', 'alpha')
            zf.writestr('nested/b.txt', 'beta')

        ok, report = search_archive(source_zip, target, threshold=1.0, search_archives=False)
        member_rows, summary = report[:-1], report[-1]
        _check('search archive ok', ok)
        _check('search archive rows', len(member_rows) == 2 and summary['info']['is_archive_summary'])
        _check('member row best match', any(row['file2'] == str(beta) for row in member_rows))
        _check('summary similarity', summary['similarity'] == 1.0)

        target_zip = root / 'target.zip'
        with zipfile.ZipFile(target_zip, 'w') as zf:
            zf.writestr('a.txt', 'alpha')
            zf.writestr('nested/b.txt', 'beta')

        cmp_report = compare_with_archives([source_zip], [target_zip], threshold=1.0, update_cli=False)
        _check('archive compare summary row', len(cmp_report) == 1 and cmp_report[0]['similarity'] == 1.0)

        plain_left = _make_file(root / 'plain_left.txt', 'alpha')
        cmp_mix = compare_with_archives([source_zip, plain_left], [alpha, beta], threshold=1.0, update_cli=False)
        _check('mixed compare rows', len(cmp_mix) == 2)

    passed = sum(item['ok'] for item in checks)
    print(f"{'check':28} {'result':8} detail")
    for item in checks:
        print(f"{item['check'][:28]:28} {('PASS' if item['ok'] else 'FAIL'):8} {item['detail']}")
    print('-' * 72)
    print(f'passed: {passed}/{len(checks)}')
    return {'passed': passed, 'total': len(checks), 'checks': checks}

#444(,1,3)
if __name__ == '__main__':
    test_ah_unit()
