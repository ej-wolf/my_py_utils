import fnmatch, pickle, sys
from pathlib import Path
from itertools import combinations, product
# from collections.abc import Iterable
from collections import defaultdict

from my_local_utils import collection, print_color, get_unique_name


CHUNK_SIZE = 1024 * 1024
MAX_CHUNKS = 100000  # safety cap to avoid memory exhaustion
DEFAULT_CUTOFF = 0.05
BAR_W = 40

_CRITERIA_ALIASES = {'dif': 'difference', 'sim': 'similarity', 'sim_max': 'similarity_max'}
_PAIRING_MODES =    {'all', 'same_strict', 'same_loose', 'videos', 'pictures', 'archives'}

_VIDEO_SUFFIXES  =  {'.avi', '.flv', '.m4v', '.mkv', '.mov', '.mp4', '.mpeg', '.mpg', '.webm', '.wmv',} #  '.ts',
_PICTURE_SUFFIXES = {'.bmp', '.gif', '.jpeg', '.jpg', '.png', '.tif', '.tiff', '.webp',}
_ARCHIVE_SUFFIXES = { '.zip', '.7z', '.bz2', '.gz', '.rar', '.tar', '.tar.bz2', '.tar.gz', '.tar.xz', '.tgz', '.xz',}
_ARCHIVE_SUFFIXES_SORTED = sorted(_ARCHIVE_SUFFIXES, key=len, reverse=True)


# region API
def compare_files(f1:str|Path, f2:str|Path, **kwargs) -> dict:
    """ Compare two files and return similarity, coverage, diff size, and chunk info."""

    progress = lambda: (offset/max_size)*100

    def emit_progress(pct: float):
        nonlocal last_progress_pct
        pct = round(float(pct), 2)
        if pct == last_progress_pct:
            return
        last_progress_pct = pct
        if progress_cb:
            progress_cb(pct)

    cutoff = kwargs.pop('cutoff', DEFAULT_CUTOFF)
    progress_cb = kwargs.pop('progress_cb', None)
    compare_bytes = kwargs.pop('compare_bytes', True)
    list_chunks = kwargs.pop('list_chunks', True)

    f1, f2 = Path(f1), Path(f2)
    size1, size2 = f1.stat().st_size, f2.stat().st_size
    max_size = max(size1, size2)

    if max_size == 0:
        info = _empty_info()
        info['similarity'] = 1.0
        return info

    size_diff = abs(size1 - size2)
    threshold = None if cutoff is None else int(max_size * cutoff)

    if threshold is not None and size_diff > threshold:
        info = _empty_info()
        info['note'] = "skipped due to size difference"
        return info

    offset,diff_bytes, searched_bytes = 0, 0, 0
    last_progress_pct = None
    stopped_threshold = False
    chunk_count = 0
    chunks = [] if list_chunks else None
    chunk_start, chunk_end = None, None

    with open(f1, "rb") as a, open(f2, "rb") as b:
        while True:
            ba = a.read(CHUNK_SIZE)
            bb = b.read(CHUNK_SIZE)
            if not ba and not bb:
                break

            max_len = max(len(ba), len(bb))
            if ba == bb:
                offset += max_len
                searched_bytes = offset
                emit_progress(progress())
                continue

            if not compare_bytes:
                diff_bytes += max_len

                if chunk_start is None:
                    chunk_start = offset
                chunk_end = offset + max_len - 1
                searched_bytes = offset + max_len

                if threshold is not None and diff_bytes > threshold:
                    stopped_threshold = True
                    break

                offset += max_len
                emit_progress(progress())
                continue

            for i in range(max_len):
                pos = offset + i
                searched_bytes = pos + 1
                b1 = ba[i] if i < len(ba) else None
                b2 = bb[i] if i < len(bb) else None

                if b1 != b2:
                    diff_bytes += 1
                    if chunk_start is None:
                        chunk_start = pos
                    chunk_end = pos
                    if threshold is not None and diff_bytes > threshold:
                        stopped_threshold = True
                        break
                else:
                    if chunk_start is not None:
                        chunk_count += 1
                        if list_chunks and len(chunks) < MAX_CHUNKS:
                            chunks.append((chunk_start, chunk_end))
                        chunk_start = None

            if stopped_threshold:
                break

            offset += max_len
            emit_progress(progress())

        if chunk_start is not None:
            chunk_count += 1
            if list_chunks and len(chunks) < MAX_CHUNKS:
                chunks.append((chunk_start, chunk_end))

    chunk_list = [] if list_chunks else None
    if list_chunks:
        for i, (start, end) in enumerate(chunks, 1):
            eof_flag = end >= max_size - 1
            chunk_list.append({'chunk': f"{i:04d}",
                               'from': f"{start:07d}",
                               'to': f"{end:07d}" + (" (EOF)" if eof_flag else ""),
                               'total_bytes': end - start + 1,
                               })
    complete = 1.0 if not stopped_threshold else searched_bytes / max_size
    similarity = 1 - (diff_bytes / max_size)

    info = {'similarity': similarity,
            'complete': complete,
            'diff_bytes': diff_bytes,
            'chunks_num': chunk_count,
            'chunks': chunk_list,
            'size': max_size,
            'note': None,
            }
    if progress_cb:
        emit_progress(100*complete)

    return info


def compare_dirs(d1, d2=None, cutoff=DEFAULT_CUTOFF, output_mode='quiet', **kwargs):
    """ Compare files between one/two dir/file inputs or glob collections and return report rows."""

    mask: str | None = kwargs.get("mask", None)
    subdir: bool = kwargs.get("subdir", False)
    file_type = kwargs.get('file_type', 'all')
    _normalize_error_handling(kwargs.get('error_handling', 'auto'))
    pairing_mode, allowed_types = _resolve_file_type_mode(file_type)

    files1 = _collect_dir_files(d1, mask=mask, subdir=subdir, dedupe=True, file_type=allowed_types or 'all')
    files2 = _collect_dir_files(d2, mask=mask, subdir=subdir, file_type=allowed_types or 'all') if d2 else None
    if files1 is None or (d2 and files2 is None):
        return []

    return _run_prepared_compare(files1, files2, cutoff=cutoff,
        output_mode=output_mode, pairing_mode=pairing_mode, **kwargs,)


def compare_lists(files1, files2=None, cutoff=DEFAULT_CUTOFF, output_mode='quiet', **kwargs):
    """ Compare file-path iterables and return the same row format as compare_dirs()."""

    def _as_file_list(items):
        paths = []
        for item in collection(items):
            p = Path(item)
            if not p.is_file():
                print_color(f'Warning: {p} is not a valid file path.', 'y')
                return None
            paths.append(p)
        return paths

    files1 = _as_file_list(files1)
    has_second = files2 is not None
    files2 = _as_file_list(files2) if has_second else None
    _normalize_error_handling(kwargs.get('error_handling', 'auto'))
    if files1 is None or (has_second and files2 is None):
        return []
    files1 = _dedupe_paths(files1)

    mask: str | None = kwargs.get('mask', None)
    pairing_mode, allowed_types = _resolve_file_type_mode(kwargs.get('file_type', 'all'))
    files1 = _apply_file_filters(files1, mask=mask, allowed_types=allowed_types)
    files2 = _apply_file_filters(files2, mask=mask, allowed_types=allowed_types) if files2 else None

    return _run_prepared_compare( files1, files2, cutoff=cutoff, output_mode=output_mode,
                                  pairing_mode=pairing_mode, **kwargs,)


def filter_results(report, cutoff: float|int|None=DEFAULT_CUTOFF, criteria='difference'):
    """ General filtering utility for report rows.
        :param report: ...
        :param numeric cutoff: treated as cutoff for similarity/difference
        :param string cutoff: treated as a filename mask (Pathlib style) file1 or file2
        :param criteria:  'difference': keep rows with similarity >= (1 - cutoff)
                          'similarity': keep exact rows with similarity  >= cutoff
                          'similarity_max': keep rows with similarity   >= cutoff
                          'complete': keep rows with searched coverage >= cutoff
                          'file1', 'file2': Apply mask (Pathlib style) passed by cutoff
                                           to file1 (default) or file2 if criteria='file2'
    """
    criteria = _normalize_criteria(criteria)

    filtered = []
    for r in report:
        if isinstance(cutoff, str):
            target = r.get(criteria if criteria in ('file1', 'file2') else 'file1', '')
            if fnmatch.fnmatch(Path(target).name, cutoff):
                filtered.append(r)
        else:
            if criteria in ('difference', 'similarity', 'similarity_max'):
                value = r.get('similarity', r.get('info', {}).get('similarity'))
                if value is None:
                    continue
                if criteria != 'similarity_max':
                    complete = r.get('complete', r.get('info', {}).get('complete'))
                    if complete != 1.0:
                        continue
                if criteria == 'difference' and value >= (1 - cutoff):
                    filtered.append(r)
                if criteria in ('similarity', 'similarity_max') and value >= cutoff:
                    filtered.append(r)
                continue

            if criteria in r:
                value = r.get(criteria)
            else:
                value = r.get('info', {}).get(criteria)

            if value is None:
                continue
            if value >= cutoff:
                filtered.append(r)
    return filtered


def sort_results(report, criteria='difference', order='increase', **kwargs):
    """ Sort report rows by selected criteria, with optional pre-filter by cutoff.
        Use criteria='complete' to sort by searched coverage before cutoff/EOF.
    """
    criteria = _normalize_criteria(criteria)
    cutoff = kwargs.get('cutoff', None)
    entries = filter_results(report, cutoff=cutoff, criteria=criteria) if cutoff is not None else list(report)

    desc_tokens = {'decrease', 'desc', 'descending', 'reverse'}
    reverse = str(order).lower() in desc_tokens

    if criteria == 'difference':
        key_fn = lambda r: 1 - (r.get('similarity', r.get('info', {}).get('similarity', 0.0)) or 0.0)
    elif criteria in ('similarity', 'similarity_max'):
        key_fn = lambda r: r.get('similarity', r.get('info', {}).get('similarity', 0.0)) or 0.0
    elif criteria in ('file1', 'file2'):
        key_fn = lambda r: Path(r.get(criteria, '')).name
    else:
        key_fn = lambda r: r.get(criteria, r.get('info', {}).get(criteria))

    return sorted(entries, key=key_fn, reverse=reverse)


def print_cmp_info(report, **kwargs):
    """ Print a compact table view of comparison rows."""
    if not report or len(report) == 0:
        print("No comparison results to display.")
        return

    entries = collection(report)

    mxl = kwargs.get('max_len', 30)
    print(f"{'file1':{mxl}} {'file2':{mxl}}{'size':^15} {'similarity':^12} {'complete':>10} {'diff_bytes':>12}")

    printed_rows, total_size, total_similarity = 0, 0, 0.0

    for r in entries:
        print(f"{_short_name(r['file1'], mxl):{mxl}} "
              f"{_short_name(r['file2'], mxl):{mxl}} "
              f"{r['size']:^15,} "
              f"{r['similarity']:^0.5f} "
              f"{r['complete']:>10.3f} "
              f"{r['diff_bytes']:>12,}")
        printed_rows += 1
        total_size += r['size']
        total_similarity += r['similarity']

    if kwargs.get('summary', True):
        print("-" * 100)
        print(f"rows: {printed_rows} | total size: {total_size:,} | avg size: {total_size / printed_rows:,.2f}"
              f" | avg similarity: {total_similarity / printed_rows:.5f}" )


def list_pairs(report, **kwargs):
    """ Print full file paths for pairs whose exact difference is within cutoff."""
    for r in filter_results(report, **kwargs):
        print(f"Similarity: {r['similarity']}; Size: {r['size']}")
        print(r['file1'])
        print(r['file2'])
        print()


def quick_print(report, **kwargs):
    """Filter, sort, and print a compact report in one call."""
    r = sort_results( filter_results(report, kwargs.get('cutoff', DEFAULT_CUTOFF)),
                      criteria=kwargs.get('criteria', 'difference'),)
    print_cmp_info(r, summary=kwargs.get('summary', True))


def save_report(report: list, path: Path | str = None, **kwargs):
    """ Save a report list as pickle with optional chunk stripping and key normalization."""

    def normalize_keys(data: dict):
        if legacy:
            return
        if 'chunks num' in data:
            data['chunks_num'] = data.pop('chunks num')

    def prepare_item(item):
        if not isinstance(item, dict):
            return item

        row = dict(item)
        normalize_keys(row)
        info = row.get('info')
        if isinstance(info, dict):
            row['info'] = dict(info)
            normalize_keys(row['info'])
            if not save_chunks:
                row['info']['chunks'] = None

        if not save_chunks and 'chunks' in row:
            row['chunks'] = None

        return row

    def resolve_target(path_value):
        cwd = Path.cwd()
        if path_value is None:
            return get_unique_name(cwd/'report.pkl')

        p = Path(path_value)
        if p.suffix:
            return p if p.is_absolute() else cwd/p

        p = p if p.is_absolute() else cwd/p
        if p.exists() and p.is_dir():
            return get_unique_name(p/'report.pkl')

        return p.with_suffix('.pkl')

    save_chunks = kwargs.pop('save_chunks', False)
    legacy = kwargs.pop('legacy', False)
    if kwargs:
        unknown = ', '.join(sorted(kwargs))
        raise TypeError(f"save_report() got unexpected keyword argument(s): {unknown}")

    target = resolve_target(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    payload = [prepare_item(item) for item in report]
    with target.open('wb') as f:
        pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)

    return target
# endregion API

# region Helpers
def _short_name(p, max_len=30):
    s = Path(p).name
    return (s[: max_len - 3] + "...") if len(s) > max_len else s


def _empty_info():
    return {'similarity': 0.0, 'complete': 0.0, 'diff_bytes': 0, 'chunks_num': 0, 'size': 0, 'note': ''}


def _normalize_error_handling(mode: str) -> str:
    if mode not in {'auto', 'manual'}:
        raise ValueError("error_handling must be 'auto' or 'manual'")
    return mode


def _normalize_criteria(criteria: str) -> str:
    return _CRITERIA_ALIASES.get(criteria, criteria)


def _archive_suffix(path: Path) -> str | None:
    name = path.name.lower()
    for suffix in _ARCHIVE_SUFFIXES_SORTED:
        if name.endswith(suffix):
            return suffix
    return None


def _path_type_candidates(path: Path) -> set[str]:
    candidates = set()
    suffix = path.suffix.lower()
    if suffix:
        candidates.add(suffix)
    archive_suffix = _archive_suffix(path)
    if archive_suffix:
        candidates.add(archive_suffix)
    return candidates


def _classify_file_family(path: Path) -> str:
    candidates = _path_type_candidates(path)
    if candidates & _VIDEO_SUFFIXES:
        return 'videos'
    if candidates & _PICTURE_SUFFIXES:
        return 'pictures'
    if candidates & _ARCHIVE_SUFFIXES:
        return 'archives'
    return 'other'


def _strict_type_key(path: Path) -> str:
    archive_suffix = _archive_suffix(path)
    if archive_suffix:
        return archive_suffix
    return path.suffix.lower()


def _resolve_file_type_mode(file_type) -> tuple[str, set[str] | None]:
    if isinstance(file_type, str):
        text = file_type.strip().lower()
        if text in _PAIRING_MODES:
            return text, None
    return 'all', _normalize_file_types(file_type)


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


class _CompareRenderer: #170
    _VALID_MODES = ('quiet', 'cli', 'cli-flash', 'prog-bar', 'both')
    _PROGRESS_ALIASES = {'prog', 'progress', 'prog-bar', 'progbar'}
    _CLI_FLASH_ALIASES = {'flash'}

    def __init__(self, mode: str, total_pairs: int):
        self._is_tty = sys.stdout.isatty()
        self.mode = self._normalize_mode(mode)
        self.total_pairs = total_pairs
        self._progress_pct = 0
        self._compared_pairs = 0
        self._printed_lines = 0
        self._last_cli_header = ''
        self._last_cli_status = ''
        self._last_pair_progress = None


    @staticmethod
    def get_modes():
        return list(_CompareRenderer._VALID_MODES)

    def _normalize_mode(self, mode: str) -> str:
        mode = str(mode).strip().lower()
        if mode == 'both':
            if self._is_tty:
                return 'both'
            print_color("both mode requires a TTY; using prog-bar mode", 'y')
            return 'prog-bar'
        if mode in self._PROGRESS_ALIASES:
            return 'prog-bar'
        if mode in self._CLI_FLASH_ALIASES:
            return 'cli-flash'
        if mode not in self._VALID_MODES:
            print_color(f"Warning: invalid output_mode {mode!r}; using quiet mode", 'y')
            return 'quiet'
        return mode

    def _progress_line(self):
        filled = int((BAR_W * self._progress_pct) / 100)
        bar = '#' * filled + '.' * (BAR_W - filled)
        return f'{"Total progress":<18} [{bar}] {self._progress_pct:5.1f}% ({self._compared_pairs}/{self.total_pairs})'

    def _both_status_line(self):
        pct = '0.0%' if self._last_pair_progress is None else f'{self._last_pair_progress:.1f}%'
        return f'{self._last_cli_header}:\t{pct} ...'

    def _clear_live_lines(self):
        if not self._printed_lines:
            return
        if self._printed_lines == 1:
            print('\r\x1b[2K', end='', flush=True)
        else:
            print('\r\x1b[2K\x1b[F\r\x1b[2K', end='', flush=True)
        self._printed_lines = 0

    def _flash_cli_lines(self):
        self._clear_live_lines()
        if self._is_tty:
            line1 = self._last_cli_header
            line2 = self._last_cli_status
            print(f'\r{line1}\n{line2}', end='', flush=True)
            self._printed_lines = 2
            return
        line = self._both_status_line()
        print(f'\r{line}', end='', flush=True)
        self._printed_lines = 1

    def _render_both(self):
        self._clear_live_lines()
        progress_line = self._progress_line()
        status_line = self._both_status_line()
        print(f'\r{progress_line}\n{status_line}', end='', flush=True)
        self._printed_lines = 2

    def _pair_progress_line(self):
        if self._last_pair_progress is None:
            return 'Progress :'
        return f'Progress : {self._last_pair_progress:5.1f}%'

    def _print_cli_block(self):
        print(self._last_cli_header)
        print(self._last_cli_status)

    def _render_cli_state(self):
        if self.mode == 'cli':
            self._print_cli_block()
        elif self.mode == 'both':
            self._render_both()
        elif self.mode == 'cli-flash':
            self._flash_cli_lines()

    def print_cli(self, line: str, *, status: bool = False):
        if self.mode not in {'cli', 'cli-flash', 'both'}:
            return
        if status:
            self._last_cli_status = line
        else:
            self._last_cli_header = line
            self._last_cli_status = self._pair_progress_line()
        self._render_cli_state()

    def update_progress(self, idx: int):
        self._compared_pairs = idx
        self._progress_pct = 100 * idx / self.total_pairs if self.total_pairs > 0 else 100.0

    def print_prog_bar(self):
        if self.mode not in {'prog-bar', 'both'}:
            return
        if self.mode == 'both':
            self._render_both()
            return
        line = self._progress_line()
        self._printed_lines = 1
        print(f'\r{line}', end='', flush=True)

    def finish_prog_bar(self):
        if self.mode not in {'prog-bar', 'both'}:
            return
        pct = 100*self._compared_pairs/self.total_pairs if self.total_pairs > 0 else 100.0
        self._progress_pct = pct
        self._clear_live_lines()
        print(f'Compare finished:\t{pct:.3g}% of {self._compared_pairs}/{self.total_pairs} comparisons complete.')

    def start_pair(self, f1: Path, f2: Path, _idx: int):
        self._last_pair_progress = None
        self.print_cli(f'Comparing:   {_short_name(f1)}   vs.  {_short_name(f2)}')

    def update_pair_progress(self, pct: float):
        if self.mode not in {'cli', 'cli-flash', 'both'}:
            return
        pct = round(float(pct), 2)
        if pct >= 100.0 or pct == self._last_pair_progress:
            return
        self._last_pair_progress = pct
        self._last_cli_status = self._pair_progress_line()
        self._render_cli_state()

    def finish_pair(self, _f1: Path, _f2: Path, info: dict, idx: int):
        self.update_progress(idx)
        self.print_prog_bar()
        if info['complete'] < 1.0:
            pct = 100 * info['complete']
            self.print_cli(f'Skipped:\t{pct:5.1f}% - completed', status=True)
            return
        self.print_cli(f'Similarity:\t{info["similarity"]:.3f}', status=True)

    def skip_pair(self, _f1: Path, _f2: Path, info: dict, idx: int):
        self.update_progress(idx)
        self.print_prog_bar()
        self.print_cli(f'Progress : {info["note"]}', status=True)

    def fail_pair(self, _f1: Path, _f2: Path, idx: int):
        self.update_progress(idx)
        self.print_prog_bar()

    def close(self):
        if self.mode in {'cli-flash', 'both'}:
            self._clear_live_lines()
        self.finish_prog_bar()

def _compare_pairs(pairs, cutoff=DEFAULT_CUTOFF, output_mode='prog', **kwargs):
    """ Compare prepared file pairs and return report rows."""
    def _warn_pair_failure(stage: str, f_1:Path, f_2:Path, err:OSError) -> bool:
        message = '\n'.join(( "Warning: compare skipped for",
                              f"  file1: {f_1}", f"  file2: {f_2}",
                              f"  stage: {stage}", f"  reason: {err}",))
        print_color(message, 'y')
        if error_handling == 'auto':
            return False
        answer = input('Abort comparison? [y/N]: ').strip().lower()
        return answer in {'y', 'yes'}

    sz_dis = kwargs.pop('size_dis', cutoff)
    error_handling = _normalize_error_handling(kwargs.pop('error_handling', 'auto'))
    total_pairs = int(kwargs.pop('total_pairs', 0) or 0)
    renderer = _CompareRenderer(output_mode, total_pairs)
    min_complete = None if cutoff is None else min(1.0, 1.5 * cutoff)

    report = []
    for idx, (f1, f2) in enumerate(pairs, 1):
        renderer.start_pair(f1, f2, idx)
        try:
            size1, size2 = f1.stat().st_size, f2.stat().st_size
            max_size  = max(size1,  size2)
            size_diff = abs(size1 - size2)
        except OSError as   exc:
            if _warn_pair_failure('stat', f1, f2, exc):
                raise RuntimeError(f'Comparison aborted for pair: {f1} vs {f2}') from exc
            renderer.fail_pair(f1, f2, idx)
            continue
        try:
            info = None
            if sz_dis is not None and size_diff > max_size * sz_dis:
                info = _empty_info()
                info['note'] = '\tskipped due to size difference'
                renderer.skip_pair(f1, f2, info, idx)
            else:
                info = compare_files( f1, f2, cutoff=cutoff,
                                      progress_cb=renderer.update_pair_progress,
                                      **kwargs,)
                renderer.finish_pair(f1, f2, info, idx)
        except OSError as exc:
            if _warn_pair_failure('compare', f1, f2, exc):
                raise RuntimeError(f'Comparison aborted for pair: {f1} vs {f2}') from exc
            renderer.fail_pair(f1, f2, idx)
            continue

        row = { 'file1': str(f1),
                'file2': str(f2),
                'size': info['size'],
                'similarity': info['similarity'],
                'diff_bytes': info['diff_bytes'],
                'chunks_num': info['chunks_num'],
                'complete': info['complete'],
                'info': info,
                }
        if min_complete is None or row['complete'] >= min_complete:
            report.append(row)
    renderer.close()

    return report


def _normalize_file_types(file_type) -> set[str] | None:
    if file_type in (None, 'all'):
        return None

    normalized = set()
    for item in collection(file_type):
        text = str(item).strip()
        if not text:
            continue
        if text.lower() == 'all':
            return None
        normalized.add(text.lower() if text.startswith('.') else f'.{text.lower()}')
    return normalized


def _match_file_type(path: Path, allowed_types: set[str] | None) -> bool:
    return allowed_types is None or bool(_path_type_candidates(path) & allowed_types)


def _apply_file_filters(files, *, mask=None, allowed_types=None) -> list[Path]:
    selected = [Path(f) for f in files if Path(f).is_file()]
    if mask:
        selected = [f for f in selected if fnmatch.fnmatch(f.name, mask)]
    if allowed_types is not None:
        selected = [f for f in selected if _match_file_type(f, allowed_types)]
    return selected


def _files_from_dir(d: Path, *, mask=None, subdir=False, allowed_types=None) -> list[Path]:
    if mask and subdir:
        files = [f for f in d.rglob(mask) if f.is_file()]
    elif mask and not subdir:
        files = [f for f in d.glob(mask) if f.is_file()]
    elif not mask and subdir:
        files = [f for f in d.rglob('*') if f.is_file()]
    else:
        files = [f for f in d.iterdir() if f.is_file()]

    if allowed_types is not None:
        files = [f for f in files if _match_file_type(f, allowed_types)]
    return files


def _collect_dir_files(paths, mask=None, subdir=False, dedupe=False, file_type='all'):
    """Collect files from directories, file-globs, dir-globs, or mixed inputs."""
    allowed_types = _normalize_file_types(file_type)
    files = []

    for item in collection(paths):
        item_str = str(item)
        has_wildcard = any(ch in item_str for ch in '*?[')

        if not has_wildcard:
            p = Path(item)
            if p.is_file():
                files.extend(_apply_file_filters([p], mask=mask, allowed_types=allowed_types))
                continue
            if p.is_dir():
                files.extend(_files_from_dir(p, mask=mask, subdir=subdir, allowed_types=allowed_types))
                continue
            print_color(f'Warning: {p} is not a valid path.', 'y')
            return None

        pattern = Path(item)
        if pattern.is_absolute():
            root = Path(pattern.anchor)
            rel_pattern = Path(*pattern.parts[1:]).as_posix()
        else:
            root = Path.cwd()
            rel_pattern = pattern.as_posix()
        matches = list(root.glob(rel_pattern))
        if not matches:
            print_color(f'Warning: no paths match pattern {item!r}.', 'y')
            return None

        matched_files = [p for p in matches if p.is_file()]
        if matched_files:
            files.extend(_apply_file_filters(matched_files, mask=mask, allowed_types=allowed_types))
            continue

        matched_dirs = [p for p in matches if p.is_dir()]
        if matched_dirs:
            for d in matched_dirs:
                files.extend(_files_from_dir(d, mask=mask, subdir=subdir, allowed_types=allowed_types))
            continue

        print_color(f'Warning: no valid files or directories match pattern {item!r}.', 'y')
        return None

    return _dedupe_paths(files) if dedupe else files


def _prepare_pairs(files1, files2=None, pairing_mode='all'):

    def _iter_pairs():
        for l, r in pair_sets:
            if r is None:
                yield from combinations(l, 2)
            else:
                yield from product(l, r)

    if pairing_mode == 'all':
        total_pairs = len(files1) * (len(files1) - 1) // 2 if files2 is None else len(files1) * len(files2)
        pairs = combinations(files1, 2) if files2 is None else product(files1, files2)
        return pairs, total_pairs

    if pairing_mode == 'same_strict':
        key_func = _strict_type_key
    elif pairing_mode == 'same_loose':
        key_func = _classify_file_family
    elif pairing_mode in {'videos', 'pictures', 'archives'}:
        files1 = [f for f in files1 if _classify_file_family(f) == pairing_mode]
        files2 = [f for f in files2 if _classify_file_family(f) == pairing_mode] if files2 is not None else None
        total_pairs = len(files1) * (len(files1) - 1) // 2 if files2 is None else len(files1) * len(files2)
        pairs = combinations(files1, 2) if files2 is None else product(files1, files2)
        return pairs, total_pairs
    else:
        raise ValueError(f'Unsupported file_type: {pairing_mode}')

    grouped1 = defaultdict(list)
    for path in files1:
        grouped1[key_func(path)].append(path)
    grouped2 = None
    if files2 is not None:
        grouped2 = defaultdict(list)
        for path in files2:
            grouped2[key_func(path)].append(path)

    pair_sets = []
    total_pairs = 0
    if grouped2 is None:
        for items in grouped1.values():
            if len(items) < 2:
                continue
            total_pairs += len(items) * (len(items) - 1) // 2
            pair_sets.append((items, None))
    else:
        for key in sorted(set(grouped1) & set(grouped2)):
            left = grouped1[key]
            right = grouped2[key]
            if not left or not right:
                continue
            total_pairs += len(left) * len(right)
            pair_sets.append((left, right))

    return _iter_pairs(), total_pairs


def _run_prepared_compare(files1, files2=None, *, cutoff, output_mode, pairing_mode='all', **kwargs):
    pairs, total_pairs = _prepare_pairs(files1, files2, pairing_mode=pairing_mode)
    return _compare_pairs(pairs, cutoff=cutoff, output_mode=output_mode, total_pairs=total_pairs, **kwargs)
# endregion Helpers

#808(2,6,1) -> 784#(2,4,) -> 775->755
#842(2,5,) cln-up->  830
