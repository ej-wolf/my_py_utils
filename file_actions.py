"""File actions for compare reports and path lists.

use cases:
    move_files(report, 'dst', move_tree=True)
        Move report['file1'] into dst while preserving the shared tree.
    move_files(paths, 'dst', overwrite_policy='rename')
        Move plain path list into dst and rename on collisions.
    delete_files(report, policy='ask')
        Delete report['file1'] after one batch confirmation.
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterable
import builtins
import os
import shutil

from my_local_utils import _absolute_path, get_unique_name, relative_paths


def _path_exists(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _is_file_like(path: Path) -> bool:
    return path.is_file() or path.is_symlink()


def _normalize_paths(file_ls) -> list[Path]:
    if isinstance(file_ls, (str, Path)):
        return [Path(file_ls)]

    if hasattr(file_ls, 'rows') and hasattr(file_ls, '__getitem__'):
        try:
            return [Path(p) for p in file_ls['file1']]
        except Exception:
            pass

    if isinstance(file_ls, Iterable):
        return [Path(p) for p in file_ls]

    raise TypeError('file_ls must be a path iterable or report-like object')


def _normalize_overwrite_policy(policy: str) -> str:
    aliases = {'overwite': 'overwrite'}
    policy = aliases.get(policy, policy)
    if policy not in {'overwrite', 'skip', 'rename'}:
        raise ValueError("overwrite_policy must be 'overwrite', 'skip', or 'rename'")
    return policy


def _normalize_delete_policy(policy: str) -> str:
    aliases = {'prun': 'prune', 'pron': 'prune', 'trash': 'trash_bin'}
    policy = aliases.get(policy, policy)
    if policy not in {'trash_bin', 'prune', 'ask'}:
        raise ValueError("policy must be 'trash_bin', 'prune', or 'ask'")
    return policy


def _common_parent(paths: list[Path]) -> Path | None:
    valid = [_absolute_path(p) for p in paths if _path_exists(p) and _is_file_like(p)]
    if not valid:
        return None
    if len(valid) == 1:
        return valid[0].parent

    try:
        return Path(os.path.commonpath([str(p) for p in valid]))
    except ValueError:
        return None


def _anchor_label(path: Path) -> str:
    if path.drive:
        return path.drive.rstrip(':')
    return 'root'


def _relative_target(src: Path, dest_root: Path, common_parent: Path | None, move_tree: bool) -> Path:
    if not move_tree:
        return dest_root / src.name

    src_abs = _absolute_path(src)
    if common_parent is not None:
        return dest_root / src_abs.relative_to(common_parent)

    rel_tail = src_abs.relative_to(src_abs.anchor)
    return dest_root / _anchor_label(src_abs) / rel_tail


def _move_result(source: Path, target: Path | None, status: str, reason: str | None = None) -> dict:
    return {'source': source, 'target': target, 'status': status, 'reason': reason}


def _delete_result(source: Path, status: str, reason: str | None = None) -> dict:
    return {'source': source, 'target': None, 'status': status, 'reason': reason}


def _load_send2trash():
    try:
        from send2trash import send2trash  # type: ignore
    except ImportError:
        return None
    return send2trash


def move_files(file_ls, dest_path, overwrite_policy='skip', move_tree=False):
    """ Move files into a destination directory.

    Parameters
    ----------
    file_ls : iterable[str|Path] | Report | ReportView
        Source files to move. If a report object is given, the function uses
        its 'file1' column.
    dest_path : str | Path
        Destination root directory. It is created if missing.
    overwrite_policy : {'overwrite', 'skip', 'rename'}, default 'skip'
        Collision handling when the target file already exists.
    move_tree : bool, default False
        If False, move files directly into dest_path.
        If True, preserve each file path relative to the earliest common
        parent of the source files.

    Returns
    -------
    list[dict]
        Per-file action rows with: 'source', 'target', 'status', 'reason'.
    """

    sources = _normalize_paths(file_ls)
    overwrite_policy = _normalize_overwrite_policy(overwrite_policy)
    dest_root = Path(dest_path)

    if _path_exists(dest_root) and not dest_root.is_dir():
        raise ValueError('dest_path must be a directory path')

    dest_root.mkdir(parents=True, exist_ok=True)
    common_parent = _common_parent(sources) if move_tree else None
    results = []

    for src in sources:
        src = Path(src)
        if not _path_exists(src):
            results.append(_move_result(src, None, 'failed', 'missing source'))
            continue
        if not _is_file_like(src):
            results.append(_move_result(src, None, 'failed', 'source is not a file'))
            continue

        target = _relative_target(src, dest_root, common_parent, move_tree)
        target.parent.mkdir(parents=True, exist_ok=True)
        src_abs = _absolute_path(src)
        target_abs = _absolute_path(target)

        if overwrite_policy != 'rename' and src_abs == target_abs:
            results.append(_move_result(src, target, 'skipped', 'source and target are the same'))
            continue

        if _path_exists(target):
            if target.is_dir():
                results.append(_move_result(src, target, 'failed', 'target path is an existing directory'))
                continue

            if overwrite_policy == 'skip':
                results.append(_move_result(src, target, 'skipped', 'target already exists'))
                continue

            if overwrite_policy == 'rename':
                target = get_unique_name(target)
            else:
                target.unlink()

        try:
            shutil.move(str(src), str(target))
        except Exception as exc:
            results.append(_move_result(src, target, 'failed', str(exc)))
            continue

        results.append(_move_result(src, target, 'moved'))

    return results


def delete_files(file_ls, policy='ask'):
    """ Delete files from a path list or report object.

    Parameters
    ----------
    file_ls : iterable[str|Path] | Report | ReportView
        Source files to delete. If a report object is given, the function uses
        its 'file1' column.
    policy : {'ask', 'prune', 'trash_bin'}, default 'ask'
        Delete mode:
        'ask' asks once for the whole batch before hard delete,
        'prune' hard deletes immediately,
        'trash_bin' sends files to trash when the optional backend is available.

    Returns
    -------
    list[dict]
        Per-file action rows with: 'source', 'target', 'status', 'reason'.
    """

    sources = _normalize_paths(file_ls)
    policy = _normalize_delete_policy(policy)

    if policy == 'ask':
        existing = sum(1 for p in sources if _path_exists(p))
        prompt = f'Delete {existing}/{len(sources)} files permanently? [y/N]: '
        answer = builtins.input(prompt).strip().lower()
        if answer not in {'y', 'yes'}:
            return [_delete_result(Path(src), 'cancelled', 'batch delete cancelled') for src in sources]

    send2trash = _load_send2trash() if policy == 'trash_bin' else None
    if policy == 'trash_bin' and send2trash is None:
        raise ValueError("policy='trash_bin' requires the optional 'send2trash' package")

    results = []
    for src in sources:
        src = Path(src)
        if not _path_exists(src):
            results.append(_delete_result(src, 'failed', 'missing source'))
            continue
        if not _is_file_like(src):
            results.append(_delete_result(src, 'failed', 'source is not a file'))
            continue

        try:
            if policy == 'trash_bin':
                send2trash(str(src))
            else:
                src.unlink()
        except Exception as exc:
            results.append(_delete_result(src, 'failed', str(exc)))
            continue

        results.append(_delete_result(src, 'deleted'))

    return results


def test_ca_unit():
    """Run a small smoke test for file_actions.py."""

    def _check(name: str, cond: bool, detail=''):
        checks.append({'check': name, 'ok': bool(cond), 'detail': detail})

    def _make_file(path: Path, text: str = 'x'):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return path

    def _patch_input(answer: str):
        original = builtins.input
        builtins.input = lambda _prompt='': answer
        return original

    from compare_report import as_report

    checks = []

    with TemporaryDirectory() as td:
        root = Path(td)

        src_flat = root / 'src_flat'
        dst_flat = root / 'dst_flat'
        flat_a = _make_file(src_flat / 'a.txt', 'a')
        flat_b = _make_file(src_flat / 'b.txt', 'b')
        moved = move_files([flat_a, flat_b], dst_flat)
        _check('move flat list', all(r['status'] == 'moved' for r in moved))
        _check('move flat targets', (dst_flat / 'a.txt').is_file() and (dst_flat / 'b.txt').is_file())

        tree_root = root / 'tree_src'
        tree_a = _make_file(tree_root / 'left' / 'x.txt', 'x')
        tree_b = _make_file(tree_root / 'right' / 'y.txt', 'y')
        tree_dst = root / 'tree_dst'
        tree_move = move_files([tree_a, tree_b], tree_dst, move_tree=True)
        _check('move tree', all(r['status'] == 'moved' for r in tree_move))
        _check('move tree keeps rel path', (tree_dst / 'left' / 'x.txt').is_file() and (tree_dst / 'right' / 'y.txt').is_file())

        rename_src = root / 'rename_src'
        rename_dst = root / 'rename_dst'
        rename_file = _make_file(rename_src / 'dup.txt', 'new')
        _make_file(rename_dst / 'dup.txt', 'old')
        renamed = move_files([rename_file], rename_dst, overwrite_policy='rename')
        _check('rename collision', renamed[0]['status'] == 'moved')
        _check('rename creates unique target', renamed[0]['target'] != rename_dst / 'dup.txt')

        skip_src = root / 'skip_src'
        skip_dst = root / 'skip_dst'
        skip_file = _make_file(skip_src / 'same.txt', 'new')
        _make_file(skip_dst / 'same.txt', 'old')
        skipped = move_files([skip_file], skip_dst, overwrite_policy='skip')
        _check('skip collision', skipped[0]['status'] == 'skipped')
        _check('skip leaves source', skip_file.is_file())

        overwrite_src = root / 'overwrite_src'
        overwrite_dst = root / 'overwrite_dst'
        overwrite_file = _make_file(overwrite_src / 'same.txt', 'new')
        old_target = _make_file(overwrite_dst / 'same.txt', 'old')
        overwritten = move_files([overwrite_file], overwrite_dst, overwrite_policy='overwrite')
        _check('overwrite collision', overwritten[0]['status'] == 'moved')
        _check('overwrite replaces target', old_target.read_text(encoding='utf-8') == 'new')

        report_src = root / 'report_src'
        report_dst = root / 'report_dst'
        report_file = _make_file(report_src / 'report.txt', 'r')
        report = as_report([{
            'file1': report_file,
            'file2': report_file,
            'size': report_file.stat().st_size,
            'similarity': 1.0,
            'diff_bytes': 0,
            'complete': 1.0,
        }])
        report_move = move_files(report, report_dst)
        _check('report input uses file1', report_move[0]['status'] == 'moved' and (report_dst / 'report.txt').is_file())

        prune_root = root / 'delete_prune'
        prune_file = _make_file(prune_root / 'gone.txt', 'gone')
        pruned = delete_files([prune_file], policy='prune')
        _check('delete prune', pruned[0]['status'] == 'deleted' and not prune_file.exists())

        ask_root = root / 'delete_ask'
        ask_file = _make_file(ask_root / 'ask.txt', 'ask')
        original_input = _patch_input('n')
        try:
            declined = delete_files([ask_file], policy='ask')
        finally:
            builtins.input = original_input
        _check('delete ask decline', declined[0]['status'] == 'cancelled' and ask_file.exists())

        original_input = _patch_input('y')
        try:
            accepted = delete_files([ask_file], policy='ask')
        finally:
            builtins.input = original_input
        _check('delete ask accept', accepted[0]['status'] == 'deleted' and not ask_file.exists())

        trash_file = _make_file(root / 'trash.txt', 'trash')
        try:
            trash_res = delete_files([trash_file], policy='trash_bin')
            trash_ok = all(r['status'] == 'deleted' for r in trash_res)
        except ValueError:
            trash_ok = not _load_send2trash()
            if trash_file.exists():
                trash_file.unlink()
        _check('trash backend handling', trash_ok)

    passed = sum(item['ok'] for item in checks)
    print(f"{'check':28} {'result':8} detail")
    for item in checks:
        print(f"{item['check'][:28]:28} {('PASS' if item['ok'] else 'FAIL'):8} {item['detail']}")
    print('-' * 72)
    print(f'passed: {passed}/{len(checks)}')
    return {'passed': passed, 'total': len(checks), 'checks': checks}


if __name__ == '__main__':
    test_ca_unit()
