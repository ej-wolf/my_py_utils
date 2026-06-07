import time, fnmatch, pickle
from pathlib import Path
from itertools import combinations, product
from collections.abc import Iterable

#* Imports from py_utils project
from my_local_utils import collection, print_color, get_unique_name

CHUNK_SIZE = 1024*1024
MAX_CHUNKS = 100000  # safety cap to avoid memory exhaustion
DEFAULT_CUTOFF = 0.05

BAR_W = 40 # 36

def _short_name(p, maxlen=30):
    s = Path(p).name
    return (s[: maxlen - 3] + "...") if len(s) > maxlen else s

def _empty_info(list_chunks=True):
    return {'similarity':0.0, 'complete':0.0, 'diff_bytes':0, 'chunks_num':0, 'size':0, 'note':''}


def _normalize_error_handling(mode: str) -> str:
    if mode not in {'auto', 'manual'}:
        raise ValueError("error_handling must be 'auto' or 'manual'")
    return mode


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


def _print_progress(label: str, current: int, total : int, last_pct: int = -1) -> int:
    # print(last_pct)
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

def compare_files(f1:str|Path, f2:str|Path, **kwargs)-> dict: # 151
    """ Compare two files and return similarity, coverage, diff size, and chunk info."""
    progress = lambda: (offset/max_size)*100

    #* Normalize arguments
    cutoff = kwargs.pop('cutoff', DEFAULT_CUTOFF)
    update_cli = kwargs.pop('update_cli', False)
    compare_bytes = kwargs.pop('compare_bytes', True)
    list_chunks = kwargs.pop('list_chunks', True)

    f1, f2 = Path(f1), Path(f2)

    size1, size2 = f1.stat().st_size, f2.stat().st_size

    max_size = max(size1, size2)
    if max_size == 0:
        info = _empty_info() #list_chunks=list_chunks)
        info['similarity'] = 1.0
        return info

    size_diff = abs(size1 - size2)
    threshold = None if cutoff is None else int(max_size*cutoff)

    #*  ---- skip immediately if size difference already exceeds threshold
    if threshold is not None and size_diff > threshold:
        info = _empty_info() # list_chunks=list_chunks)
        info['note'] = f"skipped due to size difference"
        return info

    #* ---- initialize variables for comparison
    offset = 0
    diff_bytes = 0
    searched_bytes = 0
    last_print = time.time()
    stopped_threshold = False
    chunk_count = 0
    chunks = [] if list_chunks else None
    chunk_start, chunk_end = None, None

    #* start loop over chunks of both files
    with open(f1, "rb") as a, open(f2, "rb") as b:
        while True:
            ba = a.read(CHUNK_SIZE)
            bb = b.read(CHUNK_SIZE)
            if not ba and not bb:
                break

            max_len = max(len(ba), len(bb))
            #* identical chunks are equal in both modes
            if ba == bb:
                offset += max_len
                searched_bytes = offset
                continue

            #* in block mode, any differing chunk is counted as fully different
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
                continue

            #* BYTE-BY-BYTE COMPARISON - if chunks differ, compare byte by byte
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
                    #* if current_chunk_start is not None:
                    if chunk_start is not None:
                        chunk_count += 1
                        if list_chunks and len(chunks) < MAX_CHUNKS:
                            chunks.append((chunk_start, chunk_end))
                        chunk_start = None

            if stopped_threshold:
                break

            offset += max_len

            if update_cli:
                now = time.time()
                if now - last_print >= 5:
                    print(f"\rProgress: {progress():6.2f}%", end="", flush=True)
                    last_print = now

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
                               'to'  : f"{end:07d}" + (" (EOF)" if eof_flag else ""),
                               'total_bytes': end - start + 1
                               })

    complete = 1.0 if not stopped_threshold else searched_bytes / max_size
    similarity = 1 - (diff_bytes / max_size)

    info = {'similarity': similarity,
            'complete': complete,
            'diff_bytes': diff_bytes,
            'chunks_num': chunk_count,
            'chunks':chunk_list,
            'size': max_size,
            'note': None,}
    if update_cli and not stopped_threshold:
        #* overwrite the progress line with the final result
        print(f"\rSimilarity: {similarity: 0.5f}\n", end='', flush=True)
    elif stopped_threshold:
        print(f"\rProgress: {100 * complete:6.2f}% -Skipped!\n")
        info['note'] = f"stopped by cutoff at byte {searched_bytes} ({complete:.3%})\n"

    return info

#*****************************************************************************#

def _compare_pairs(pairs, cutoff=DEFAULT_CUTOFF, update_cli=False, **kwargs):
    """ Compare prepared file pairs and return report rows."""
    print(kwargs)
    def _warn_pair_failure(stage: str, f1: Path, f2: Path, exc: OSError) -> bool:
        message = '\n'.join((
            'Warning: compare skipped for',
            f'  file1: {f1}',
            f'  file2: {f2}',
            f'  stage: {stage}',
            f'  reason: {exc}',
        ))
        print_color(message, 'y')
        if error_handling == 'auto':
            return False

        answer = input('Abort comparison? [y/N]: ').strip().lower()
        return answer in {'y', 'yes'}

    sz_dis = kwargs.pop('size_dis', cutoff)
    error_handling = _normalize_error_handling(kwargs.pop('error_handling', 'auto'))
    total_pairs = int(kwargs.pop('total_pairs', 0) or 0)
    # progress_bar = bool(kwargs.pop('progress_bar', False))
    # show_pair_bar = progress_bar and not update_cli
    show_pair_bar = kwargs.pop('progress_bar', False) and not update_cli
    min_complete = None if cutoff is None else min(1.0, 1.5 * cutoff)
    report = []
    last_pct = -1

    for idx, (f1, f2) in enumerate(pairs, 1):
        try:
            size1 = f1.stat().st_size
            size2 = f2.stat().st_size
            max_size = max(size1, size2)
            size_diff = abs(size1 - size2)
        except OSError as exc:
            if _warn_pair_failure('stat', f1, f2, exc):
                raise RuntimeError(f'Comparison aborted for pair: {f1} vs {f2}') from exc
            continue

        try:
            info = None
            if sz_dis is not None and size_diff > max_size*sz_dis:
                info = _empty_info()
                info['note'] = 'skipped due to size difference'
            else:
                if not show_pair_bar:
                    print(f'{_short_name(f1)}   vs.  {_short_name(f2)} -> comparing:')
                info = compare_files(f1, f2, cutoff=cutoff, update_cli=update_cli, **kwargs)
        except OSError as exc:
            if _warn_pair_failure('compare', f1, f2, exc):
                raise RuntimeError(f'Comparison aborted for pair: {f1} vs {f2}') from exc
            if show_pair_bar:
                last_pct = _print_progress('comparing pairs', idx, total_pairs, last_pct)
            continue

        row = {'file1': str(f1), 'file2': str(f2),
               'size': info['size'],
               'similarity': info['similarity'],
               'diff_bytes': info['diff_bytes'],
               'chunks_num': info['chunks_num'],
               'complete': info['complete'],
               'info': info,}
        # Drop rows that diverged almost immediately relative to the active cutoff.
        if min_complete is None or row['complete'] >= min_complete:
            report.append(row)
        if show_pair_bar:
            last_pct = _print_progress('comparing pairs', idx, total_pairs, last_pct)

    return report


def _collect_dir_files(paths, mask=None, subdir=False, dedupe=False):
    """Collect files from one directory, dir collection, or dir mask."""

    def _expand_dir_item(item) -> list[Path] | None:
        item_str = str(item)
        has_wildcard = any(ch in item_str for ch in '*?[')
        if not has_wildcard:
            p = Path(item)
            if not p.is_dir():
                print_color(f'Warning: {p} is not a valid path.', 'y')
                return None
            return [p]

        pattern = Path(item)
        if pattern.is_absolute():
            root = Path(pattern.anchor)
            rel_pattern = Path(*pattern.parts[1:]).as_posix()
        else:
            root = Path.cwd()
            rel_pattern = pattern.as_posix()

        matches = [p for p in root.glob(rel_pattern) if p.is_dir()]
        if not matches:
            print_color(f'Warning: no directories match pattern {item!r}.', 'y')
            return None
        return matches

    dirs = []
    for item in collection(paths):
        expanded = _expand_dir_item(item)
        if expanded is None:
            return None
        dirs.extend(expanded)
    if dedupe:
        dirs = _dedupe_paths(dirs)

    files = []
    for d in dirs:
        if mask and subdir:
            files.extend(f for f in d.rglob(mask) if f.is_file())
        elif mask and not subdir:
            files.extend(f for f in d.glob(mask) if f.is_file())
        elif not mask and subdir:
            files.extend(f for f in d.rglob('*') if f.is_file())
        else:
            files.extend(f for f in d.iterdir() if f.is_file())
    return _dedupe_paths(files) if dedupe else files


def compare_dirs(d1, d2=None, cutoff=DEFAULT_CUTOFF, update_cli=True, **kwargs):
    """ Compare files between one/two dirs or dir collections and return report rows."""
    # print("cmp-dir", kwargs)
    mask:str|None = kwargs.get("mask", None)
    subdir:bool = kwargs.get("subdir", False)
    _normalize_error_handling(kwargs.get('error_handling', 'auto'))

    files1 = _collect_dir_files(d1, mask=mask, subdir=subdir, dedupe=True)
    files2 = _collect_dir_files(d2, mask=mask, subdir=subdir) if d2 else None
    if files1 is None or (d2 and files2 is None):
        return []

    total_pairs = len(files1) * (len(files1) - 1) // 2 if d2 is None else len(files1) * len(files2)
    pairs = combinations(files1, 2) if d2 is None else product(files1, files2)
    return _compare_pairs(pairs, cutoff=cutoff, update_cli=update_cli, total_pairs=total_pairs, **kwargs)


def compare_lists(files1, files2=None, cutoff=DEFAULT_CUTOFF, update_cli=True, **kwargs):
    """ Compare file-path iterables and return the same row format as compare_dirs()."""
    print("cmp-ls", kwargs)
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

    mask:str|None = kwargs.get('mask', None)

    if mask:
        files1 = [f for f in files1 if fnmatch.fnmatch(f.name, mask)]
        files2 = [f for f in files2 if fnmatch.fnmatch(f.name, mask)] if files2 else None

    total_pairs = len(files1) * (len(files1) - 1) // 2 if files2 is None else len(files1) * len(files2)
    pairs = combinations(files1, 2) if files2 is None else product(files1, files2)
    return _compare_pairs(pairs, cutoff=cutoff, update_cli=update_cli, total_pairs=total_pairs, **kwargs)

def filter_results(report, cutoff:float|int=DEFAULT_CUTOFF, criteria='difference'):
    """ General filtering utility for report rows.
        :param numeric cutoff: treated as cutoff for similarity/difference
        :param string cutoff: treated as a filename mask (Pathlib style) file1 or file2
        :param criteria:  'difference': keep rows with similarity >= (1 - cutoff)
                          'similarity': keep exact rows with similarity >= cutoff
                          'similarity_max': keep rows with similarity >= cutoff
                          'complete': keep rows with searched coverage >= cutoff
                          'file1', 'file2': Apply mask (Pathlib style) passed by cutoff
                                           to file1 (default) or file2 if criteria='file2'
    """
    criteria_map = {'dif': 'difference', 'sim': 'similarity', 'sim_max': 'similarity_max'}
    criteria = criteria_map.get(criteria, criteria)

    filtered = []
    for r in report:
        #* string cutoff → filename mask
        if isinstance(cutoff, str):
            target = r.get(criteria if criteria in ('file1', 'file2') else 'file1', '')
            if fnmatch.fnmatch(Path(target).name, cutoff):
                filtered.append(r)
        else:
            #* numeric threshold
            if criteria in ('difference', 'similarity', 'similarity_max'):
                value = r.get('similarity', r.get('info', {}).get('similarity'))
                if value is None:
                    continue
                if criteria != 'similarity_max':
                    # TODO: Revisit partial-result filtering policy after more experiments.
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

    cutoff = kwargs.get('cutoff', None)
    entries = filter_results(report, cutoff=cutoff, criteria=criteria) if cutoff is not None else list(report)

    desc_tokens = {'decrease', 'desc', 'descending', 'reverse'}
    reverse = str(order).lower() in desc_tokens

    if criteria in ('difference', 'dif'):
        key_fn = lambda r: 1 - (r.get('similarity', r.get('info', {}).get('similarity', 0.0)) or 0.0)
    elif criteria in ('similarity', 'sim', 'similarity_max', 'sim_max'):
        key_fn = lambda r: r.get('similarity', r.get('info', {}).get('similarity', 0.0)) or 0.0
    elif criteria in ('file1', 'file2'):
        key_fn = lambda r: Path(r.get(criteria, '')).name
    else:
        key_fn = lambda r: r.get(criteria, r.get('info', {}).get(criteria))

    return sorted(entries, key=key_fn, reverse=reverse)


#*****************************************************************************#
#* Report printing
#*****************************************************************************#

def print_cmp_info(report, **kwargs):
    """ Print a compact table view of comparison rows."""
    #Todo: make better solution for empty report
    if not report or len(report) == 0:
        print("No comparison results to display.")
        return

    entries = collection(report)

    mxl = kwargs.get('max_len', 30)
    print(f"{'file1':{mxl}} {'file2':{mxl}}{'size':^15} {'similarity':^12} {'complete':>10} {'diff_bytes':>12}")

    printed_rows, total_size, total_similarity = 0, 0 , 0.0

    for r in entries:
        print( f"{_short_name(r['file1'], mxl):{mxl}} "
               f"{_short_name(r['file2'], mxl):{mxl}} "
               f"{r['size']:^15,} "
               f"{r['similarity']:^0.5f} "
               f"{r['complete']:>10.3f} "
               f"{r['diff_bytes']:>12,}")

        printed_rows += 1
        total_size += r['size']
        total_similarity += r['similarity']

    if kwargs.get('summary',True): #  and printed_rows > 0:
        print("-" * 100)
        print(f"rows: {printed_rows} | total size: {total_size:,} | avg size: {total_size/printed_rows:,.2f}" 
              f" | avg similarity: {total_similarity/printed_rows:.5f}")


def list_pairs(report, **kwargs):
    """ Print full file paths for pairs whose exact difference is within cutoff."""
    #Todo; resolve the default cutoff issue
    for r in filter_results(report, **kwargs):
        print(f"Similarity: {r['similarity']}; Size: {r['size']}")
        print(r['file1'])
        print(r['file2'])
        print()


def quick_print(report, **kwargs):
    """Filter, sort, and print a compact report in one call."""
    r = sort_results(filter_results(report, kwargs.get('cutoff',DEFAULT_CUTOFF )), criteria=kwargs.get('criteria','difference'))
    print_cmp_info(r, summary=kwargs.get('summary',True ))

def save_report(report:list, path:Path|str=None, **kwargs):
    """Save a report list as pickle with optional chunk stripping and key normalization."""

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
            return get_unique_name(cwd / 'report.pkl')

        p = Path(path_value)
        if p.suffix:
            return p if p.is_absolute() else cwd / p

        p = p if p.is_absolute() else cwd / p
        if p.exists() and p.is_dir():
            return get_unique_name(p / 'report.pkl')

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
