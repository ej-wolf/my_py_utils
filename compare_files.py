import time, fnmatch, io, contextlib
from pathlib import Path
from itertools import combinations, product

#* Imports from py_utils project
from my_local_utils import collection

CHUNK_SIZE = 1024*1024
MAX_CHUNKS = 100000  # safety cap to avoid memory exhaustion

def _short_name(p, maxlen=30):
    s = Path(p).name
    return (s[: maxlen - 3] + "...") if len(s) > maxlen else s

def _empty_info():
    return {'similarity':0.0, 'complete':0.0, 'diff_bytes':0, 'chunks num':0, 'chunks':[], 'size':0, 'note':''}

def compare_files(f1:str|Path, f2:str|Path, cutoff:float|None=.05, update_cli=False, compare_bytes=True)-> dict:

    progress = lambda: (offset/max_size)*100

    f1, f2 = Path(f1), Path(f2)

    size1, size2 = f1.stat().st_size, f2.stat().st_size

    max_size = max(size1, size2)
    if max_size == 0:
        info = _empty_info()
        info['similarity'] = 1.0
        return info

    size_diff = abs(size1 - size2)
    threshold = None if cutoff is None else int(max_size*cutoff)

    #*  ---- skip immediately if size difference already exceeds threshold
    if threshold is not None and size_diff > threshold:
        info = _empty_info()
        info['note'] = f"skipped due to size difference"
        return info
        # return { 'similarity': 0.0, 'Different bytes': size_diff, 'chunks num': 0,
        #          'chunks': [], 'size':max_size, 'note': "skipped_size_difference"}

    #* ---- initialize variables for comparison
    offset = 0
    diff_bytes = 0
    searched_bytes = 0
    last_print = time.time()
    stopped_threshold = False
    chunks = []
    current_chunk_start, current_chunk_end = None, None

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

                if current_chunk_start is None:
                    current_chunk_start = offset
                current_chunk_end = offset + max_len - 1
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
                    if current_chunk_start is None:
                        current_chunk_start = pos
                    current_chunk_end = pos
                    if threshold is not None and diff_bytes > threshold:
                        stopped_threshold = True
                        break
                else:
                    # if current_chunk_start is not None:
                    if current_chunk_start is not None and len(chunks) < MAX_CHUNKS:
                        chunks.append((current_chunk_start, current_chunk_end))
                        current_chunk_start = None

            if stopped_threshold:
                break

            offset += max_len

            if update_cli:
                now = time.time()
                if now - last_print >= 5:
                    # percent = 100*(offset/max_size) if max_size else 100
                    print(f"\rProgress: {progress():6.2f}%", end="", flush=True)
                    last_print = now

        if current_chunk_start is not None and len(chunks) < MAX_CHUNKS:
            chunks.append((current_chunk_start, current_chunk_end))

    chunk_list = []

    for i, (start, end) in enumerate(chunks, 1):

        eof_flag = end >= max_size - 1

        chunk_list.append({'chunk': f"{i:04d}",
                           'from': f"{start:07d}",
                           'to': f"{end:07d}" + (" (EOF)" if eof_flag else ""),
                           'total_bytes': end - start + 1
                            })

    complete = 1.0 if not stopped_threshold else searched_bytes / max_size
    similarity = 1 - (diff_bytes / max_size)
    # similarity = round(similarity, 5)

    info = {'similarity': similarity,
            'complete': complete,
            'diff_bytes': diff_bytes,
            'chunks num': len(chunk_list),
            'chunks': chunk_list,
            'size': max_size,
            'note': None,
            }
    if update_cli and not stopped_threshold:
        #* overwrite the progress line with the final result
        print(f"\rSimilarity: {similarity: 0.5f}\n", end='', flush=True)
    elif stopped_threshold:
        # print(f"\r Skipped!\n", end='', flush=True)
        print(f"\rProgress: {100 * complete:6.2f}% -Skipped!\n")
        info['note'] = f"stopped by cutoff at byte {searched_bytes} ({complete:.3%})\n"

    return info


#*****************************************************************************#

def compare_dirs(d1, d2=None, cutoff=0.05, update_cli=True, **kwargs):

    d1 = Path(d1)
    d2 = Path(d2) if d2 else None
    mask = kwargs.get("mask", None)
    subdir = kwargs.get("subdir", False)

    if not d1.is_dir():
        print(f"Error: {d1} is not a valid path.")
        return []
    if d2 and not d2.is_dir():
        print(f"Error: {d2} is not a valid path.")
        return []
    # files1 = [f for f in d1.iterdir() if f.is_file()]
    # files2 = [f for f in d2.iterdir() if f.is_file()] if d2 else None
    # if subdir:
    #     files1 = [f for f in d1.rglob("*") if f.is_file()]
    #     files2 = [f for f in d2.rglob("*") if f.is_file()] if d2 else None
    # else:
    #     files1 = [f for f in d1.iterdir() if f.is_file()]
    #     files2 = [f for f in d2.iterdir() if f.is_file()] if d2 else None
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
    # threshold_similarity = 1 - cutoff

    # decide pair iterator
    if d2 is None:
        pairs = combinations(files1, 2)
    else:
        pairs = product(files1, files2)

    for f1, f2 in pairs:

        size1 = f1.stat().st_size
        size2 = f2.stat().st_size

        max_size = max(size1, size2)
        size_diff = abs(size1 - size2)

        # skipped = False
        info = None

        #* quick size filter
        if sz_dis is not None and size_diff > max_size * sz_dis:
            # skipped = True
            # print(f"{_short_name(f1)} {_short_name(f2)} -> skipped (size)")
            # info = {'similarity': 0.0, 'diff_bytes': size_diff,
            #         'chunks num': 0, 'chunks': [], 'note': "skipped_size_difference"}
            info = _empty_info()
            info['note'] = f"skipped due to size difference"
        else:
            print(f"{_short_name(f1)}   vs.  {_short_name(f2)} -> comparing:")
            info = compare_files(f1, f2, cutoff, update_cli=update_cli)

        # similarity = info['similarity']

        row = { 'file1': str(f1),
                'file2': str(f2),
                'size' : info['size'],
                'similarity': info['similarity'],
                'diff_bytes': info['diff_bytes'],
                'chunks num': info['chunks num'],
                'complete': info['complete'],
                'info': info,
                }

        report.append(row)

    return report

def filter_results(report, cutoff=0.05, criteria='difference'):
    """ General filtering utility for report rows.
        Rules:
        numeric cutoff:
            with criteria='difference' keep rows with similarity >= (1 - cutoff)
            with criteria='similarity' keep rows with similarity >= cutoff
            otherwise apply directly to the selected numeric field
        string cutoff:
            treated as a filename mask (Pathlib style) and applied to
            file1 by default or file2 if criteria='file2'
    """

    filtered = []
    for r in report:
        #* string cutoff → filename mask
        if isinstance(cutoff, str):
            target = r.get(criteria if criteria in ('file1', 'file2') else 'file1', '')
            if fnmatch.fnmatch(Path(target).name, cutoff):
                filtered.append(r)
        else:
            #* numeric threshold
            if criteria in ('difference', 'similarity'):
                # TODO: Revisit partial-result filtering policy after more experiments.
                complete = r.get('complete', r.get('info', {}).get('complete'))
                if complete != 1.0:
                    continue
                value = r.get('similarity', r.get('info', {}).get('similarity'))
                if value is None:
                    continue
                if criteria == 'difference' and value >= (1 - cutoff):
                    filtered.append(r)
                if criteria == 'similarity' and value >= cutoff:
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
    """Sort report rows by selected criteria, with optional pre-filter by cutoff."""

    cutoff = kwargs.get('cutoff', None)
    entries = filter_results(report, cutoff=cutoff, criteria=criteria) if cutoff is not None else list(report)

    desc_tokens = {'decrease', 'desc', 'descending', 'reverse'}
    reverse = str(order).lower() in desc_tokens

    if criteria == 'difference':
        key_fn = lambda r: 1 - (r.get('similarity', r.get('info', {}).get('similarity', 0.0)) or 0.0)
    elif criteria in ('file1', 'file2'):
        key_fn = lambda r: Path(r.get(criteria, '')).name
    else:
        key_fn = lambda r: r.get(criteria, r.get('info', {}).get(criteria))

    return sorted(entries, key=key_fn, reverse=reverse)


def print_cmp_info(report, **kwargs):

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
               f"{r['chunks num']:6}"
               )

        printed_rows += 1
        total_size += r['size']
        total_similarity += r['similarity']

    if kwargs.get('summary',True) and printed_rows > 0:
        print("-" * 100)
        print(f"rows: {printed_rows} | total size: {total_size:,} | avg size: {total_size/printed_rows:,.2f}" 
              f" | avg similarity: {total_similarity/printed_rows:.5f}")

def quick_print(report, **kwargs):
    r = sort_results(filter_results(report, kwargs.get('cutoff',0.05 )), criteria=kwargs.get('criteria','difference'))
    print_cmp_info(r, summary=kwargs.get('summary',True ))


# def test_cf_unit(test_root='test_data', case='all', cutoff=0.05):
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


if __name__ == '__main__':

    test_root =  Path("test_data")
    # cases = {'equal':   [('try_01 vs try_02', test_root/'try_01_cf', test_root/'try_02_cf')],
    #          'similar': [('try_03 vs try_04', test_root/'try_03', test_root/'try_04')],
    #          'all': [
    #         ('try_01 vs try_02', test_root/'try_01_cf', test_root/'try_02_cf'),
    #         ('try_03 vs try_04', test_root/'try_03', test_root/'try_04'),
    #     ],
    # }

    cases = {'try_01 vs try_02': [test_root/'try_01_cf', test_root/'try_02_cf'],
             'try_03 vs try_04': [test_root/'try_03'   , test_root/'try_04'],
             'try_01 vs try_02': [test_root/'try_01_cf', test_root/'try_02_cf'],
             'try_03 vs try_04': [test_root/'try_03'   , test_root/'try_04'],
    }

    cases = (['try_01 vs try_02', test_root/'try_01_cf', test_root/'try_02_cf'],
             ['try_03 vs try_04', test_root/'try_03',    test_root/'try_04'],
             ['try_01 vs try_03', test_root/'try_01_cf', test_root/'try_03'],
             ['try_01 vs try_04', test_root/'try_01_cf', test_root/'try_04'],)

    test_cf_unit(cases=cases[0:2], cli_update=True)

    f1 = Path("/mnt/local-data/Python/Projects/weSmart/data/json_cache-x/jsons_nf/cam3_5_4.json")
    f2 = Path("/mnt/local-data/Python/Projects/weSmart/data/cam3_5_4.json")
    print_cmp_info(compare_files(f1,f2))
