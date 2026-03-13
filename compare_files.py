import time, fnmatch
from pathlib import Path
from itertools import combinations, product

CHUNK_SIZE = 1024 * 1024
MAX_CHUNKS = 100000  # safety cap to avoid memory exhaustion

def _short_name(p, maxlen=30):
    s = Path(p).name
    return (s[: maxlen - 3] + "...") if len(s) > maxlen else s

def _empty_info():
    return {'similarity': 0.0, 'diff_bytes': 0, 'chunks num': 0, 'chunks': [], 'size': 0, 'note': ""}

def compare_files(f1:str|Path, f2:str|Path, dis:float=.05, update_cli=False, compare_bytes=True)-> dict:

    progress = lambda: (offset/max_size)*100

    f1, f2 = Path(f1), Path(f2)

    size1, size2 = f1.stat().st_size, f2.stat().st_size

    max_size = max(size1, size2)
    size_diff = abs(size1 - size2)
    threshold = int(size1 * dis)

    #*  ---- skip immediately if size difference already exceeds threshold
    if size_diff > threshold:
        info = _empty_info()
        info['note'] = f"skipped due to size difference"
        return info
        # return { 'similarity': 0.0, 'Different bytes': size_diff, 'chunks num': 0,
        #          'chunks': [], 'size':max_size, 'note': "skipped_size_difference"}

    #* ---- initialize variables for comparison
    offset = 0
    diff_bytes = 0
    last_print = time.time()
    stopped_threshold = False
    chunks = []
    current_chunk_start, current_chunk_end = None, None

    #* start loop over chunks of both files
    with open(f1, "rb") as a, open(f2, "rb") as b:

        buf_a = bytearray(CHUNK_SIZE)
        buf_b = bytearray(CHUNK_SIZE)

        while True:
            n1 = a.readinto(buf_a)
            n2 = b.readinto(buf_b)
            if n1 == 0 and n2 == 0:
                break

            ba = memoryview(buf_a)[:n1] # a.read(CHUNK_SIZE)
            bb = memoryview(buf_b)[:n2] # b.read(CHUNK_SIZE)
            if not ba and not bb:
                break

            max_len = max(len(ba), len(bb))

            #* FAST BLOCK COMPARISON - if enabled and chunks are identical, skip byte-by-byte comparison
            if compare_bytes and ba == bb:
                offset += max_len
                continue

            #* if compare_bytes=False treat entire block as different
            if not compare_bytes:
                diff_bytes += max_len

                if current_chunk_start is None:
                    current_chunk_start = offset
                current_chunk_end = offset + max_len - 1

                if diff_bytes > threshold:
                    stopped_threshold = True
                    break

                offset += max_len
                continue


            #* BYTE-BY-BYTE COMPARISON - if chunks differ, compare byte by byte
            for i in range(max_len):
                pos = offset + i
                b1 = ba[i] if i < len(ba) else None
                b2 = bb[i] if i < len(bb) else None

                if b1 != b2:
                    diff_bytes += 1
                    if current_chunk_start is None:
                        current_chunk_start = pos
                    current_chunk_end = pos
                    if diff_bytes > threshold:
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

    # similarity = 1 - (diff_bytes/size1 if size1 else 0)
    similarity = 1 - (diff_bytes/size1 if not stopped_threshold else 1)
    # similarity = round(similarity, 5)

    info = {'similarity': similarity,
            'diff_bytes': diff_bytes,
            'chunks num': len(chunk_list),
            'chunks': chunk_list,
            'size': max_size,
            'note': None,
            }
    if update_cli and not stopped_threshold:
        #* overwrite the progress line with the final result
        print(f"\rSimilarity: {similarity: 0.5f}\n", end='', flush=True)
    else:
        # print(f"\r Skipped!\n", end='', flush=True)
        print(f"\rProgress: {progress():6.2f}% -Skipped!\n")
        info['note'] = f"stopped by threshold at byte {offset} ({progress():.3%})\n"

    return info



def compare_dirs(d1, d2=None, dis=0.05, update_cli=True, **kwargs):

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

    sz_dis = kwargs.get('size_dis', dis)
    report = []
    # threshold_similarity = 1 - dis

    # decide pair iterator
    if d2 is None:
        pairs = combinations(files1, 2)
    else:
        pairs = product(files1, files2)

    for f1, f2 in pairs:

        size1 = f1.stat().st_size
        size2 = f2.stat().st_size

        size_diff = abs(size1 - size2)

        # skipped = False
        info = None

        #* quick size filter
        if size_diff > size1*sz_dis:
            # skipped = True
            # print(f"{_short_name(f1)} {_short_name(f2)} -> skipped (size)")
            # info = {'similarity': 0.0, 'diff_bytes': size_diff,
            #         'chunks num': 0, 'chunks': [], 'note': "skipped_size_difference"}
            info = _empty_info()
            info['note'] = f"skipped due to size difference"
        else:
            print(f"{_short_name(f1)}   vs.  {_short_name(f2)} -> comparing:")
            info = compare_files(f1, f2, dis, update_cli=update_cli)

        # similarity = info['similarity']

        row = { 'file1': str(f1),
                'file2': str(f2),
                'size' : info['size'],
                'similarity': info['similarity'],
                'diff_bytes': info['diff_bytes'],
                'chunks num': info['chunks num'],
                'info': info,
                }

        report.append(row)

    return report



def print_cmp_info(report, threshold=0.05, sort_by=None, **kwargs):

    if not report or len(report) == 0:
        print("No comparison results to display.")
        return

    entries = report
    # if kwargs.get('sort', True):
    #     entries = sorted(entries, key=lambda x: x['similarity'], reverse=True)
    if sort_by is not None:
        entries = sorted(entries, key=lambda x: x.get(sort_by, x.get("info", {}).get(sort_by)),
                         reverse=kwargs.get('sort_desc', True))

    mxl = kwargs.get('max_len', 30)
    # header = ["file1", "file2", "similarity", "Different bytes", "chunks num"]
    header = list(report[0].keys())
    print(f"{header[0]:{mxl}} {header[1]:{mxl}}{header[2]:^15} {header[3]:^12} {header[4]:>10}")

    printed_rows, total_size, total_similarity = 0, 0 , 0.0

    for r in entries:

        if r['similarity'] >= threshold:
            print(  f"{_short_name(r['file1'], mxl):{mxl}} "
                    f"{_short_name(r['file2'], mxl):{mxl}} "                    
                    f"{r['size']:^15,} "
                    f"{r['similarity']:^0.5f} "                    
                    f"{r['diff_bytes']:>12,} "
                    f"{r['chunks num']:6}"
                )

            printed_rows += 1
            total_size += r['size']
            total_similarity += r['similarity']

    if kwargs.get('summary') and printed_rows > 0:
        print("-" * 100)
        print(f"rows: {printed_rows} | total size: {total_size:,} | avg size: {total_size/printed_rows:,.2f}" 
              f" | avg similarity: {total_similarity/printed_rows:.5f}")

def filter_results(report, threshold, filter_by='similarity'):
    """ General filtering utility for report rows.
        Rules:
        numeric threshold:
            applied to the given field (default: similarity)
        string threshold:
            treated as a filename mask (Pathlib style) and applied to
            file1 by default or file2 if filter_by='file2'
    """

    filtered = []
    for r in report:
        #* string threshold → filename mask
        if isinstance(threshold, str):
            target = r.get(filter_by if filter_by in ('file1','file2') else 'file1', '')
            if fnmatch.fnmatch(Path(target).name, threshold):
                filtered.append(r)
        else:
            #* numeric threshold
            if filter_by in r:
                value = r.get(filter_by)
            else:
                value = r.get('info', {}).get(filter_by)

            if value is None:
                continue
            if filter_by == 'similarity':
                if value >= (1 - threshold):
                    filtered.append(r)
            else:
                if value >= threshold:
                    filtered.append(r)
    return filtered


if __name__ == '__main__':

    d1 = Path("i:/new-rec/Lost79985")
    d2 = Path("i:/sort/R/G")
    report = compare_dirs(d1,d2, subdir=True, update_cli=True)
    print_cmp_info(report, threshold=0.10)
