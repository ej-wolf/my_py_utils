import time, fnmatch, io, contextlib
from pathlib import Path
from itertools import combinations, product

#* Imports from py_utils project
from my_local_utils import collection, print_color

CHUNK_SIZE = 1024*1024
MAX_CHUNKS = 100000  # safety cap to avoid memory exhaustion

def _short_name(p, maxlen=30):
    s = Path(p).name
    return (s[: maxlen - 3] + "...") if len(s) > maxlen else s

def _empty_info(list_chunks=True):
    return {'similarity':0.0, 'complete':0.0, 'diff_bytes':0, 'chunks_num':0, 'size':0, 'note':''}
# def _empty_info(list_chunks=True):
#     info = {'similarity':0.0, 'complete':0.0, 'diff_bytes':0, 'chunks num':0, 'size':0, 'note':''}
#     if list_chunks:
#         info['chunks'] = []
#     else:
#         info['chunks'] = None
#     return info

def compare_files(f1:str|Path, f2:str|Path, **kwargs)-> dict: # 151
    """ Compare two files and return similarity, coverage, diff size, and chunk info."""
    progress = lambda: (offset/max_size)*100

    #* Normalize arguments
    cutoff = kwargs.pop('cutoff', 0.05)
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

def compare_dirs(d1, d2=None, cutoff=0.05, update_cli=True, **kwargs):
    """ Compare files between one or two directories and return a row-based report."""

    d1 = Path(d1)
    d2 = Path(d2) if d2 else None
    mask:str|None = kwargs.get("mask", None)
    subdir:bool = kwargs.get("subdir", False)

    if not d1.is_dir():
        print(f"Error: {d1} is not a valid path.")
        return []
    if d2 and not d2.is_dir():
        print(f"Error: {d2} is not a valid path.")
        return []

    if   mask and subdir:
        files1 = [f for f in d1.rglob(mask) if f.is_file()]
        files2 = [f for f in d2.rglob(mask) if f.is_file()] if d2 else None
    elif mask and not subdir:
        files1 = [f for f in d1.glob(mask) if f.is_file()]
        files2 = [f for f in d2.glob(mask) if f.is_file()] if d2 else None
    elif not mask and subdir:
        files1 = [f for f in d1.rglob("*") if f.is_file()]
        files2 = [f for f in d2.rglob("*") if f.is_file()] if d2 else None
    elif not mask and not subdir:
        files1 = [f for f in d1.iterdir() if f.is_file()]
        files2 = [f for f in d2.iterdir() if f.is_file()] if d2 else None



    sz_dis = kwargs.get('size_dis', cutoff)
    report = []
    #* Pairs iterator
    if d2 is None:
        pairs = combinations(files1, 2)
    else:
        pairs = product(files1, files2)

    for f1, f2 in pairs:
        size1 = f1.stat().st_size
        size2 = f2.stat().st_size

        max_size = max(size1, size2)
        size_diff = abs(size1 - size2)


        #* quick size filter
        info = None
        if sz_dis is not None and size_diff > max_size*sz_dis:
            info = _empty_info()
            info['note'] = f"skipped due to size difference"
        else:
            print(f"{_short_name(f1)}   vs.  {_short_name(f2)} -> comparing:")
            info = compare_files(f1, f2, cutoff=cutoff, update_cli=update_cli)

        row = { 'file1': str(f1),
                'file2': str(f2),
                'size' : info['size'],
                'similarity': info['similarity'],
                'diff_bytes': info['diff_bytes'],
                'chunks_num': info['chunks_num'],
                'complete': info['complete'],
                'info': info,
                }

        report.append(row)

    return report

def filter_results(report, cutoff:float|int=0.05, criteria='difference'):
    """ General filtering utility for report rows.
        :param numeric cutoff: treated as cutoff for similarity/difference
        :param string cutoff: treated as a filename mask (Pathlib style) file1 or file2
        :param criteria:  'difference': keep rows with similarity >= (1 - cutoff)
                          'similarity': keep exact rows with similarity >= cutoff
                          'similarity_max': keep rows with similarity >= cutoff
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
    """ Sort report rows by selected criteria, with optional pre-filter by cutoff """

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
    # header = ["file1", "file2", "similarity", "Different bytes", "chunks num"]
    header = list(entries[0].keys())
    print(f"{header[0]:{mxl}} {header[1]:{mxl}}{header[2]:^15} {header[3]:^12} {header[4]:>10}")

    printed_rows, total_size, total_similarity = 0, 0 , 0.0

    for r in entries:
        print( f"{_short_name(r['file1'], mxl):{mxl}} "
               f"{_short_name(r['file2'], mxl):{mxl}} "
               f"{r['size']:^15,} "
               f"{r['similarity']:^0.5f} "
               f"{r['diff_bytes']:>12,} "
               f"{r['chunks_num']:6}")

        printed_rows += 1
        total_size += r['size']
        total_similarity += r['similarity']

    if kwargs.get('summary',True): #  and printed_rows > 0:
        print("-" * 100)
        print(f"rows: {printed_rows} | total size: {total_size:,} | avg size: {total_size/printed_rows:,.2f}" 
              f" | avg similarity: {total_similarity/printed_rows:.5f}")


def list_pairs(report, **kwargs):
    """ Print full file paths for pairs whose exact difference is within cutoff."""
    # entries = filter_results(report, cutoff=cutoff, criteria='difference')
    # if not entries:
    #     print("No matching pairs found.")
    #     return
    # for r in filter_results(report, cutoff=cutoff, criteria='difference'):
    #Todo; resolve the default cutoff issue
    for r in filter_results(report, **kwargs):
        print(f"Similarity: {r['similarity']}; Size: {r['size']}")
        print(r['file1'])
        print(r['file2'])
        print()


def quick_print(report, **kwargs):
    """Filter, sort, and print a compact report in one call."""

    r = sort_results(filter_results(report, kwargs.get('cutoff',0.05 )), criteria=kwargs.get('criteria','difference'))
    print_cmp_info(r, summary=kwargs.get('summary',True ))


def test_cf_unit( cases, cutoff=0.05, **kwargs):
    """Run selected bundled directory comparisons in both compare_bytes modes."""

    # test_root = Path(test_root)
    # cases = {
    #     'equal': [('try_01 vs try_02', test_root/'try_01_cf', test_root/'try_02_cf')],
    #     'similar': [('try_03 vs try_04', test_root/'try_03', test_root/'try_04')],
    #     'all': [
    #         ('try_01 vs try_02', test_root/'try_01_cf', test_root/'try_02_cf'),
    #         ('try_03 vs try_04', test_root/'try_03', test_root/'try_04'),
    #     ],
    # }

    # if case not in cases:
    #     raise ValueError(f"Unknown case {case!r}. Expected one of: {', '.join(cases)}")

    def _files_by_name(path):
        return {p.name: p for p in path.iterdir() if p.is_file()}

    rows = []
    for label, left_dir, right_dir in cases:
        left_files = _files_by_name(left_dir)
        right_files = _files_by_name(right_dir)

        common_names = sorted(set(left_files) & set(right_files))
        left_only  = sorted(set(left_files) - set(right_files))
        right_only = sorted(set(right_files) - set(left_files))

        for cmp_b in (True, False):
            start = time.perf_counter()
            similarities = []
            diff_bytes = 0
            identical = 0

            for name in common_names:
                with contextlib.redirect_stdout(io.StringIO()):
                    info = compare_files(left_files[name], right_files[name],
                                        cutoff=cutoff,
                                         update_cli= kwargs.get('cli_update',False),
                                         compare_bytes=cmp_b,)

                similarities.append(info['similarity'])
                diff_bytes += info['diff_bytes']
                if info['diff_bytes'] == 0:
                    identical += 1

            elapsed = time.perf_counter() - start
            avg_similarity = sum(similarities)/len(similarities) if similarities else 0.0
            min_similarity = min(similarities) if similarities else 0.0

            rows.append({'case': label,
                        'compare_bytes': cmp_b,
                        'files': len(common_names),
                        'identical': identical,
                        'avg_similarity': avg_similarity,
                        'min_similarity': min_similarity,
                        'diff_bytes': diff_bytes,
                        'elapsed_sec': elapsed,
                        'left_only': len(left_only),
                        'right_only': len(right_only),}
                        )

    print(f"{'case':28} {'byte mode':10} {'files':>5} {'ident':>5} "
          f"{'avg_sim':>10} {'min_sim':>10} {'diff_bytes':>12} {'sec':>8}")

    for row in rows:
        print(f"{row['case'][:28]:28} "
              f"{str('on' if  row['compare_bytes'] else 'off'):^10} "
              f"{row['files']:^5d} "
              f"{row['identical']:5d} "
              f"{row['avg_similarity']:10.5f} "
              f"{row['min_similarity']:10.5f} "
              f"{row['diff_bytes']:12,d} "
              f"{row['elapsed_sec']:8.3f}"
              )

        if row['left_only'] or row['right_only']:
            print(f"  unmatched files: left_only={row['left_only']} right_only={row['right_only']}")

    return rows

# 444(14,14,1) -> 416(4,2,1)

if __name__ == '__main__':

    test_root =  Path("test_data")
    tst_cases = (['try_01 vs try_02', test_root/'try_01_cf', test_root/'try_02_cf'],
                 ['try_03 vs try_04', test_root/'try_03',    test_root/'try_04'],
                 ['try_01 vs try_03', test_root/'try_01_cf', test_root/'try_03'],
                 ['try_01 vs try_04', test_root/'try_01_cf', test_root/'try_04'],)

    test_cf_unit(cases=tst_cases[0:2], cli_update=True)

    jf1 = Path("/mnt/local-data/Python/Projects/weSmart/data/json_cache-x/jsons_nf/cam3_5_4.json")
    jf2 = Path("/mnt/local-data/Python/Projects/weSmart/data/cam3_5_4.json")
    print_cmp_info(compare_files(jf1, jf2))
