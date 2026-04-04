import re
from pathlib import Path
from typing import Callable


# -----------------------------------------
#* Helper functions
# -----------------------------------------

def _strip_side(s: str, exclude: list[str], is_start: bool) -> str:

    def is_allowed_edge_char(c:str, is_start:bool) -> bool:

        if c.isalnum():
            return True
        if is_start and c in '([':
            return True
        if not is_start and c in ')]':
            return True
        return False

    changed = True

    while changed and s:
        changed = False
        #* check exclude first
        for ex in exclude:
            if not ex:
                continue
            if is_start and s.startswith(ex):
                s = s[len(ex):]
                changed = True
                break
            if not is_start and s.endswith(ex):
                s = s[:-len(ex)]
                changed = True
                break
        if changed:
            continue

        #* check single char
        if s and not is_allowed_edge_char(s[0] if is_start else s[-1], is_start):
            s = s[1:] if is_start else s[:-1]
            changed = True
    return s


# -----------------------------------------
#* Sanitize file names
# -----------------------------------------

def sanitize_file_name(file_name:str, exclude:str|list[str]|None = None) -> str:

    if isinstance(exclude, str):
        exclude = [exclude]
    if exclude is None:
        exclude = []

    # split extension
    if '.' in file_name:
        base, ext = file_name.rsplit('.', 1)
        ext = '.' + ext
    else:
        base, ext = file_name, ''

    s = base
    s = _strip_side(s, exclude, True)
    s = _strip_side(s, exclude, False)

    return s + ext


def sanitize_tags(file_name:str, sub_str: str | list[str]) -> str:
    """ Remove given substrings and normalize separators and edges """
    if isinstance(sub_str, str):
        sub_str = [sub_str]

    #* remove substrings
    for s in sub_str:
        if not s:
            continue
        file_name = file_name.replace(s, "")
    #* collapse duplicate non-alphanumeric separators
    file_name = re.sub(r'([^a-zA-Z0-9])\1+', r'\1', file_name)
    #* remove leading non-alphanumeric characters
    file_name = re.sub(r'^[^a-zA-Z0-9]+', '', file_name)
    #* strip trailing spaces
    return file_name.strip()


# -----------------------------------------
# replace_string
# -----------------------------------------

def replace_string(file_name: str, sub_str_1 : str|list, sub_str_2:str|list) -> str:
    """ Replace substrings: one-to-one or many-to-one mapping """
    if isinstance(sub_str_1, str):
        sub_str_1 = [sub_str_1]

    if isinstance(sub_str_2, str):
        for s1 in sub_str_1:
            file_name = file_name.replace(s1, sub_str_2)
        return file_name

    if len(sub_str_1) != len(sub_str_2):
        raise ValueError("sub_str_1 and sub_str_2 must have the same length")

    for s1, s2 in zip(sub_str_1, sub_str_2):
        file_name = file_name.replace(s1, s2)

    return file_name


# -----------------------------------------
#* Fix numbering
# -----------------------------------------

def _parse_format(fmt: str) -> list[str]:
    """ Parse format string into tokens (x, o, n, digits, -n)."""
    tokens: list[str] = []
    i = 0
    while i < len(fmt):
        c = fmt[i]
        if c == '-' and i + 1 < len(fmt) and fmt[i+1].isdigit():
            tokens.append(fmt[i:i+2])  # e.g. -3
            i += 2
        elif c.isdigit() or c in {'x', 'o', 'n'}:
            tokens.append(c)
            i += 1
        else:
            i += 1
    return tokens


def _extract_number_segment(stem:str, idx:int) -> tuple[int, int, str]|None:
    """ Extract contiguous digit segment based on index rule."""
    s = stem.rstrip()

    if idx == -1:
        m = re.search(r'(\d+)(?!.*\d)', s)
        if not m:
            return None
        return m.start(), m.end(), m.group(1)

    pos = idx if idx >= 0 else len(s) + idx

    if pos < 0 or pos >= len(s) or not s[pos].isdigit():
        return None

    start = pos
    while start - 1 >= 0 and s[start-1].isdigit():
        start -= 1

    end = pos
    while end + 1 < len(s) and s[end+1].isdigit():
        end += 1

    return start, end + 1, s[start:end+1]


def fix_numbering(file_name: str, idx: int = -1, frmt:str = "") -> str:
    """ Reformat numeric part of filename according to custom pattern """
    if '.' in file_name:
        base, ext = file_name.rsplit('.', 1)
        ext = '.' + ext
    else:
        base, ext = file_name, ''

    seg = _extract_number_segment(base, idx)
    if not seg:
        return file_name

    start, end, num = seg
    tokens = _parse_format(frmt)
    res:list[str] = []
    ptr = 0

    for t in tokens:
        if t == 'x':
            if ptr < len(num):
                ptr += 1
        elif t in {'o', 'n'}:
            if ptr < len(num):
                res.append(num[ptr])
                ptr += 1
        else:
            if t.startswith('-'):
                idx2 = len(num) + int(t)
            else:
                idx2 = int(t) - 1

            if 0 <= idx2 < len(num):
                res.append(num[idx2])

    # new_num = ''.join(res)
    new_base = base[:start] + ''.join(res) + base[end:]

    return new_base + ext


# -----------------------------------------
#* sanitize_dir
# -----------------------------------------

# def sanitize_dir(path: str | Path, func: Callable, depth, **kwargs: dict):
def map_dirs(path:str|Path, func: Callable, **kwargs):
        """ Map a name-transforming function over files/directories in 'path' tree.
        Applies a renaming/ sanitation/...  `func(name, **kwargs)` to each item in the  tree
        Supports depth-limited traversal,
        optional inclusion of directories, and safe/verbose execution.
        """
        # """Apply renaming function to files/dirs with optional depth control and logging."""

        p = Path(path)

        if not p.exists() or not p.is_dir():
            raise ValueError(f"{path} is not a valid directory")

        depth = kwargs.pop("depth", 0)  # replaces sub_dir
        rm_dir: bool = kwargs.pop("rm_dir", True)
        print2cli: bool = kwargs.pop("print2cli", True)

        # normalize depth
        if isinstance(depth, bool):
            depth = float('inf') if depth else 0

        items = []

        if depth == 0:
            items = list(p.iterdir())
        else:
            for item in p.rglob("*"):
                rel_depth = len(item.relative_to(p).parts)
                if rel_depth <= depth:
                    items.append(item)

            # rename deeper paths first
            items.sort(key=lambda x: len(x.parts), reverse=True)

        for item in items:
            try:
                if item.is_dir() and not rm_dir:
                    continue  #* skip dir if rm_dir is False

                old_name = item.name
                new_name = func(old_name, **kwargs)
                if new_name == old_name:
                    continue #* skip if name unchanged

                new_path = item.with_name(new_name)
                if new_path.exists():
                    if print2cli:
                        print(f"[SKIP exists] {item} -> {new_path}")
                    continue #* skip if target name exists

                #* rename and report
                item.rename(new_path)
                if print2cli:
                    print(f"{item} -> {new_path}")

            except Exception as e:
                if print2cli:
                    print(f"[ERROR] {item} -> {e}")
                continue #* report error and continue


# 287(2,13,2) -----------------------------
