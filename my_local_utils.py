import shutil, re, zipfile, fnmatch, os
from pathlib import Path
import json
import random




from colorama import Fore, Style
# B, U, R = '\033[1m', '\033[4m', '\033[0m'
# RED, GREEN, BLUE = Fore.RED, Fore.GREEN, Fore.BLUE
# RESET = Style.RESET_ALL
def print_color(msg:str, clr=Fore.RED):
    if   clr in ['RED',' Red', 'red', 'r']      :  clr = Fore.RED
    elif clr in ['BLUE', 'Blue', 'blue', 'b']   :  clr = Fore.BLUE
    elif clr in ['YELLOW', 'yellow', 'y']       :  clr = Fore.YELLOW
    elif clr in ['BLUE', 'Blue', 'blue', 'r']   :  clr = Fore.BLUE
    elif clr in ['GREEN', 'Green', 'green', 'g']:  clr = Fore.GREEN
    print( f"{clr}{msg}{Style.RESET_ALL}")

#* region  Files and Paths Utils ----------------------------------------------


def _make_unique_dir(root, base_name, **kwargs):
    """  Create a unique subdir under root for base_name, adding (2), (3), ... if needed.
    :param root : str|Path,  Parent directory.
    :param base_name : str, Desired subdirectory name.
    no_space : bool, optional (via kwargs)  If True, use base_name-(2) style; otherwise base_name (2).
    """
    no_space = kwargs.get("no_space", False)
    sep = "" if no_space else " "

    root = Path(root)
    clip_name = base_name
    i = 2
    clip_dir = root / clip_name

    while clip_dir.exists():
        clip_name = f"{base_name}{sep}({i})"
        clip_dir = root / clip_name
        i += 1

    clip_dir.mkdir(parents=True, exist_ok=True)
    return clip_dir, clip_name


def clear_dir(path, missing_ok:bool = False) -> None:
    """  Delete all files and subdirectories inside `path`, but keep `path` itself.
    Any error (nonexistent path, not a directory, permissions, etc.)
    is printed and swallowed, so it won't stop the program.
    ----------
    :param path : str|Path  Directory whose contents will be removed.
    :param missing_ok : bool, default False
            If False, raise FileNotFoundError when path doesn't exist.
            If True, silently do nothing when path doesn't exist.
    """
    try:
        p = Path(path)
    except Exception as e:
        print(f"clear_dir: invalid path {path!r}: {e}")
        return

    try:
        if not p.exists():
            print(f"clear_dir: path does not exist: {p}")
            return

        if not p.is_dir():
            print(f"clear_dir: not a directory: {p}")
            return
    except Exception as e:
        print(f"clear_dir: failed to inspect path {p!r}: {e}")
        return

    for child in p.iterdir():
        try:
            # Don't follow symlinks, just remove the link itself
            if child.is_symlink() or child.is_file():
                child.unlink()
            elif child.is_dir():
                shutil.rmtree(child)
            else:
                # Weird/unknown type – try to unlink anyway
                child.unlink(missing_ok=True)
        except Exception as e:
            print(f"clear_dir: failed to remove {child!r}: {e}")


def correct_path(path:str|Path, project_root: str|Path|None=None):
    """  Try to resolve a path defined with relative prefixes (e.g. ../../a/b/c)
    by matching only its *tail* (a/b/c) somewhere under the project root.
    Rules:
    - Ignores leading ../ components
    - Searches recursively under project_root
    - If exactly one match is found → return corrected Path
    - If zero or multiple matches → print one-line warning and return None
    """
    if path is None:
        return None

    p = Path(path)
    tail = Path(*[x for x in p.parts if x not in ('..', '.')])

    root = Path(project_root) if project_root is not None else Path.cwd()
    matches = list(root.rglob(tail.as_posix()))

    if len(matches) == 1:
        # return matches[0]
        return str(matches[0]) if isinstance(path,str) else matches[0]
    elif len(matches) == 0:
        print(f"[correct_path] NOT FOUND: {tail}")
    else:
        print(f"[correct_path] AMBIGUOUS ({len(matches)} matches): {tail}")
    return None


def get_unique_name(file_name:str|Path, n:int=3) -> Path:
    """ Return a unique file name.
    Rules:  If file does not exist → return as is.
    If exists:  my_file.txt      -> my_file_001.txt  (padding = n)
                my_file_01.txt   -> my_file_02.txt   (padding preserved = 2)
                my_file_45.txt   -> my_file_46.txt   (padding preserved = 2)
    """

    file_path = Path(file_name)
    if not file_path.exists():
        return file_path

    parent, stem, suffix = file_path.parent, file_path.stem, file_path.suffix

    match = re.search(r'_(\d+)$', stem)

    if match:
        base = stem[:match.start()]
        num_str = match.group(1)
        counter = int(num_str) + 1
        padding = len(num_str)  # preserve existing padding
    else:
        base = stem
        counter = 1
        padding = n  # use default padding

    while True:
        new_name = f"{base}_{counter:0{padding}d}{suffix}"
        new_path = parent / new_name
        if not new_path.exists():
            return new_path
        counter += 1


def _absolute_path(path: Path) -> Path:
    return path if path.is_absolute() else Path.cwd()/path


def relative_paths(paths, ref_path=None) -> list[Path]:
    """ Return input paths as paths relative to ref_path or the current working directory."""
    root = Path.cwd() if ref_path is None else Path(ref_path)
    root_abs = _absolute_path(root)
    return [ Path(os.path.relpath(_absolute_path(Path(path)), root_abs))
             for path in collection(paths) ]

#endregion

#* region *** Compressing Utils  *********************************************#

def zip_dir(target_dir:Path|str, method='file', protocol='zip', rm_policy='ask', mask=None):
    """ Compress a directory either child-by-child or as one archive.
    method:     'file'  -> zip each direct child of `dir` separately
                'dir'   -> zip the whole directory into one archive
    protocol:   'zip' implemented now,  ('7z' / 'rar' reserved for later)
    rm_policy:  'keep'  -> keep originals
                'remove'-> remove originals after successful compression
                'ask'   -> ask once at the end whether to remove originals
    """
    def _matches_mask(path_obj):
        if mask in [None, '']:
            return True
        rel_name = str(path_obj.relative_to(target_dir)) if path_obj != target_dir else path_obj.name
        return fnmatch.fnmatch(path_obj.name, mask) or fnmatch.fnmatch(rel_name, mask)

    target_dir = Path(target_dir)
    if not target_dir.is_dir():
        raise NotADirectoryError(target_dir)
    if method not in {'file', 'dir'}:
        raise ValueError(f"Unknown zip_dir method: {method}")
    archived, originals = [], []
    if method == 'file':
        for child in sorted(target_dir.iterdir()):
            if not _matches_mask(child):
                continue
            archive_path = _zip_one_path(child, protocol=protocol)
            archived.append(archive_path)
            originals.append(child)
    else:
        members = [child for child in sorted(target_dir.rglob('*')) if child.is_file() and _matches_mask(child)]
        archive_path = _zip_one_path(target_dir, protocol=protocol, members=members)
        archived.append(archive_path)
        originals.append(target_dir)

    if _archive_rm_decision(rm_policy, len(originals), "Remove original sources after archiving"):
        for src in originals:
            if src.is_dir():
                shutil.rmtree(src)
            elif src.exists():
                src.unlink()

    return archived


def unzip_dir(z: Path | str, rm_policy='ask'):
    """ Extract per-file ZIPs from a directory, or unpack a directory ZIP and then extract nested JSON ZIPs.
    If `z` is a directory:   extract every direct `*.zip` child into that directory.
    If `z` is a `.zip` file: unpack it first, then extract nested `*.zip` files so the final outputs are plain files like `foo.json`.
    """
    z = Path(z)
    extracted = []
    archives_to_remove = []

    if z.is_file():
        if z.suffix.lower() != '.zip':
            raise ValueError(f"Expected a directory or .zip file, got: {z}")
        direct_files = [Path(name) for name in zipfile.ZipFile(z, 'r').namelist() if not name.endswith('/')]
        root_parts = {name.parts[0] for name in direct_files if name.parts}
        if len(root_parts) == 1 and next(iter(root_parts)) == z.stem:
            root_dir = z.parent / z.stem
            _extract_zip_file(z, out_dir=z.parent)
        else:
            root_dir = z.parent / z.stem
            _extract_zip_file(z, out_dir=root_dir)
        archives_to_remove.append(z)
    elif z.is_dir():
        root_dir = z
    else:
        raise FileNotFoundError(z)

    nested_archives = sorted(root_dir.rglob('*.zip'))
    for archive in nested_archives:
        extracted.extend(_extract_zip_file(archive, out_dir=archive.parent))
        archives_to_remove.append(archive)

    if _archive_rm_decision(rm_policy, len(archives_to_remove), "Remove ZIP archives after extracting"):
        for archive in archives_to_remove:
            if archive.exists():
                archive.unlink()

    return extracted

#* compressing helpers
def _zip_one_path(path: str | Path, protocol='zip', members=None):
    """ Archive one file or directory and return the created archive path."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)

    archive_path = path.with_suffix('.zip') if path.is_file() else path.parent / f"{path.name}.zip"
    if archive_path.exists():
        archive_path = get_unique_name(archive_path)
    if protocol != 'zip':
        # TODO: add 7z / rar support once dependency policy is settled.
        raise NotImplementedError(f"archive protocol '{protocol}' is not implemented yet")

    with zipfile.ZipFile(archive_path, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        if path.is_file():
            zf.write(path, arcname=path.name)
        else:
            children = members if members is not None else sorted(path.rglob('*'))
            for child in children:
                child = Path(child)
                if child.is_file():
                    zf.write(child, arcname=str(child.relative_to(path.parent)))
    return archive_path


def _archive_rm_decision(rm_policy, n_items, action):
    """ Resolve whether archived/source items should be removed."""
    if rm_policy not in {'keep', 'remove', 'ask'}:
        raise ValueError(f"Unknown rm_policy: {rm_policy}")
    if rm_policy == 'remove':
        return True
    if rm_policy == 'keep':
        return False
    try:
        ans = input(f"{action} {n_items} item(s)? [Y/N]: ").strip().lower()
    except EOFError:
        ans = ''
    return ans in {'y','Y', 'Yes', 'yes'}


def _extract_zip_file(zip_path: str | Path, out_dir: str | Path | None = None):
    """ Extract one ZIP archive and return the created file paths."""

    zip_path = Path(zip_path)
    if zip_path.suffix.lower() != '.zip':
        raise ValueError(f"Not a ZIP file: {zip_path}")

    with zipfile.ZipFile(zip_path, 'r') as zf:
        names = [Path(name) for name in zf.namelist() if not name.endswith('/')]
        root_parts = {name.parts[0] for name in names if name.parts}

        if out_dir is None:
            if len(root_parts) == 1 and next(iter(root_parts)) == zip_path.stem:
                out_dir = zip_path.parent
            else:
                out_dir = zip_path.parent
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        zf.extractall(out_dir)

    return [out_dir / name for name in names]


# --- unzip_all_to with separate_dir option ---
def unzip_all_to(zip_path:Path|str, extract_to:Path|str, **kwargs):

    zip_path, extract_to = Path(zip_path), Path(extract_to)

    search_tree = kwargs.get("search_tree", True)
    tree_struct = kwargs.get("tree_struct", "full")
    conflict_policy = kwargs.get("conflict_policy", "auto")
    separate_dir = kwargs.get("separate_dir", True)

    if not zip_path.exists():
        raise FileNotFoundError(zip_path)

    extract_to.mkdir(parents=True, exist_ok=True)

    # collect zip files
    if zip_path.is_file():
        zip_files = [zip_path]
        base_root = zip_path.parent
    else:
        if search_tree:
            zip_files = list(zip_path.rglob("*.zip"))
        else:
            zip_files = list(zip_path.glob("*.zip"))
        base_root = zip_path

    extracted = []

    for zf in zip_files:

        with zipfile.ZipFile(zf, "r") as z:

            # base output dir (optionally per-archive)
            base_out = (extract_to / zf.stem) if separate_dir else extract_to

            for member in z.infolist():

                if member.is_dir():
                    continue

                member_path = Path(member.filename)

                # --- target path resolution ---
                if tree_struct == "full":
                    target = base_out / member_path

                elif tree_struct == "partial":
                    rel = zf.parent.relative_to(base_root)
                    target = base_out / rel / member_path.name

                elif tree_struct == "no":
                    target = base_out / member_path.name

                else:
                    raise ValueError(f"Invalid tree_struct: {tree_struct}")

                target.parent.mkdir(parents=True, exist_ok=True)

                # --- conflict handling ---
                if target.exists():
                    if conflict_policy == "auto":
                        target = get_unique_name(target)
                    elif conflict_policy == "rewrite":
                        pass
                    else:
                        raise ValueError(f"Invalid conflict_policy: {conflict_policy}")

                # --- extraction ---
                with z.open(member) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)

                extracted.append(target)

    return extracted



#endregion

# ***** json handling ***** #x`1
def load_json_frames(json_path: str | Path):
    with open(json_path, 'r') as f:
        data = json.load(f)
    # frames =
    return data['frames'] # frames


def json_equal(f1, f2, keys=None):
# def json_equal(f1, f2, ignore_header=False):
    with open(f1) as a, open(f2) as b:
        # if ignore_header:
        #     return json.load(a).get('frames') == json.load(b).get('frames')
        if keys:
            j1, j2 = json.load(a), json.load(b)
            found = [j1.get(k) == j2.get(k)  for k in collection(keys)]
            return  all (found)
            return json.load(a).get('frames') == json.load(b).get('frames')
        return json.load(a) == json.load(b)

def compare_json_dirs(d1, d2, soft_compare=False):
    """ Compare two JSON directories;
        in soft mode only common filenames are checked."""
    d1, d2 = Path(d1),  Path(d2)

    files1 = {p.name: p for p in d1.rglob('*.json')}
    files2 = {p.name: p for p in d2.rglob('*.json')}

    if not soft_compare:
        #* Strict mode: file sets must match exactly
        if set(files1.keys()) != set(files2.keys()):
            return False, 'Different file sets'
        compare_list = files1.keys()
    else:
        #* Soft mode: compare only common names
        compare_list = set(files1.keys()) & set(files2.keys())

        if not compare_list:
            return False, 'No common JSON files to compare'

    for name in compare_list:
        if not json_equal(files1[name], files2[name]):
            return False, f'Mismatch in {name}'

    return True, 'Directories identical (based on comparison mode)' #24


def compare_json_samples(l1, l2, smp, **kwargs):
    """Randomly compare JSON samples between two lists/dirs and print match statistics."""

    list1 = list(Path(l1).rglob('*.json')) if isinstance(l1, (str, Path)) else l1
    list2 = list(Path(l2).rglob('*.json')) if isinstance(l2, (str, Path)) else l2

    if not list1:
        print('No files in first input')
        return

    # Determine number of samples
    if isinstance(smp, float) and (0 <= smp <= 1):
        n_draw = max(1, int(len(list1) * smp)) if smp > 0 else 0
    elif isinstance(smp, int) and 0 < smp :
        n_draw = min(int(smp), len(list1))
    else:
        raise ValueError('smp must a float between 0 and 1 or positive int ')

    sampled = random.sample(list1, n_draw) if n_draw > 0 else []
    # Map list2 by filename
    map2 = {p.name: p for p in list2}

    if kwargs.get('print_list', False):
        print("\t File: \t\t |\tFound\t|\tEqual ")
    found,identical = 0, 0
    for p1 in sampled:
        p2 = map2.get(Path(p1).name)
        if p2:
            found += 1
            eql = json_equal(p1, p2)
            if eql:
                identical += 1
        if kwargs.get('print_list', False):
            print(f"{Path(p1).name:20}- {'Yes' if p2 else '---'}\t{eql} ")

    percent = 100*(identical/found) if found > 0 else 0
    print(f"\nComparison result:\n {n_draw} files drawn\n"
          f" {found} found in second set\n  {identical} identical ({percent:.1f}%)")


# ***** Basic Log handling ***** #
def _save_log(lines, log_name, log_type: str | None = None):
    """Save log lines to a file; log_type reserved for future formatting tweaks."""
    if not lines:   return

    # Default: plain text, one line per entry
    if log_type is None or log_type == "default":
        log_path = Path(log_name)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("w", encoding="utf-8") as lf:
            for line in lines:
                lf.write(line + "\n")
    else:
        # Placeholder for future formats
        log_path = Path(log_name)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("w", encoding="utf-8") as lf:
            for line in lines:
                lf.write(line + "\n")

def _load_log_lines(log_source):
    """Normalize log source (path or list) into list of stripped lines."""
    if isinstance(log_source, (str, Path)):
        lp = Path(log_source)
        if not lp.is_file():
            return []
        with lp.open("r", encoding="utf-8") as f:
            return [ln.strip() for ln in f if ln.strip()]
    elif isinstance(log_source, list):
        return [str(ln).strip() for ln in log_source if str(ln).strip()]
    return []


# ***** Collection casting ***** #
def as_collection(x):
    """  If x is a collection (list/tuple/set/range/numpy array/torch tensor/etc.)
    return it as-is. Otherwise, wrap it in a single-element list.
    (*) Strings/bytes are treated as scalars (wrapped).
    (*) Multi-dim numpy / torch arrays are returned as-is (no special handling).
    """
    from collections.abc import Iterable

    if isinstance(x, (str, bytes, bytearray)):
        return [x]    #* treat strings/bytes as scalars, not collections

    #* optional numpy/torch support without hard dependency
    np_types = ()
    torch_types = ()
    try:
        import numpy as np
        np_types = (np.ndarray,)
    except Exception:
        pass
    try:
        import torch
        torch_types = (torch.Tensor,)
    except Exception:
        pass

    # collection_types = (list, tuple, set, frozenset, range, dict) + np_types + torch_types
    collection_types = (list, tuple, set, frozenset, range) + np_types + torch_types

    #* common concrete collection types
    if isinstance(x, collection_types):
        return x

    #* other iterables (e.g. generators); treat as collections and return as-is
    if isinstance(x, Iterable) and not isinstance(x, dict):
        return x

    #* scalar fallback
    return [x]

collection = as_collection



if __name__ == "__main__":
    pass
    # tst_get_pth("/mnt/local-data/Python/Projects/weSmart/work_dirs/tsm_r50_bbfrm")
    f1 = Path("/home/ejwolf/clouds/erez.wolfson/wesmart-share/data/json/RWF-2000/train_pos/cy1gi3ZJb_c_4.json")
    f2 = Path("/home/ejwolf/clouds/erez.wolfson/wesmart-share/data/json/test/pos_004.json")
    print(f2.parent.is_dir())
    print(json_equal(f1, f2))
    print(json_equal(f1, f2, keys=['frames','fps', 'step']))

#277(2,3,2)-> 260
