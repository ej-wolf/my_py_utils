import re
from pathlib import Path
from typing import Callable

from my_local_utils import get_unique_name

# -----------------------------------------
#* Helper functions
# -----------------------------------------

def _normalize_overwrite_policy(policy: str) -> str:
    aliases = {'overwite': 'overwrite'}
    policy = aliases.get(policy, policy)
    if policy not in {'overwrite', 'skip', 'rename'}:
        raise ValueError("overwrite_policy must be 'overwrite', 'skip', or 'rename'")
    return policy


def _rename_file_path(file_path: Path, new_name: str, overwrite_policy='skip', print2cli=True) -> Path:
    old_name = file_path.name
    if new_name == old_name:
        return file_path

    overwrite_policy = _normalize_overwrite_policy(overwrite_policy)
    new_path = file_path.with_name(new_name)

    if new_path.exists():
        if new_path.is_dir():
            if print2cli:
                print(f'[SKIP dir] {file_path} -> {new_path}')
            return file_path

        if overwrite_policy == 'skip':
            if print2cli:
                print(f'[SKIP exists] {file_path} -> {new_path}')
            return file_path

        if overwrite_policy == 'rename':
            new_path = get_unique_name(new_path)
        else:
            new_path.unlink()

    file_path.rename(new_path)
    if print2cli:
        print(f'{file_path} -> {new_path}')
    return new_path


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


def replace_string_in_files(files, org_str: str, new_str: str, **kwargs):
    """Rename files by replacing a substring in their names."""

    def _replace_existing_file(file_path: Path):
        old_name = file_path.name
        if org_str not in old_name:
            return file_path

        new_name = replace_string(old_name, org_str, new_str)
        return _rename_file_path(file_path, new_name, overwrite_policy=overwrite_policy, print2cli=print2cli)

    def _replace_path_like(item):
        item_path = Path(item)

        if item_path.exists():
            if item_path.is_dir():
                raise ValueError(f'{item} is a directory; pass a single file or directory root')
            return _replace_existing_file(item_path)

        if org_str not in item_path.name:
            return item

        new_name = replace_string(item_path.name, org_str, new_str)
        new_path = item_path.with_name(new_name)
        return str(new_path) if isinstance(item, str) else new_path

    def _iter_dir_files(root: Path) -> list[Path]:
        if sub_dirs:
            return [p for p in root.rglob('*') if p.is_file()]
        return [p for p in root.iterdir() if p.is_file()]

    def _replace_dir(root: Path) -> list[Path]:
        results = []
        for file_path in _iter_dir_files(root):
            results.append(_replace_existing_file(file_path))
        return results

    overwrite_policy = kwargs.get('overwrite_policy', 'skip')
    print2cli = kwargs.get('print2cli', True)
    sub_dirs = kwargs.get('sub_dirs', kwargs.get('sub_dir', False))

    if isinstance(files, (list, tuple)):
        replaced = [_replace_path_like(item) for item in files]
        return type(files)(replaced)

    files_path = Path(files)
    if files_path.exists() and files_path.is_dir():
        return _replace_dir(files_path)

    return _replace_path_like(files)


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
#* Shift numeration
# -----------------------------------------

def _split_name_parts(file_name: str, include_extension: bool) -> tuple[str, str]:
    """Split file name into editable text and preserved tail."""
    if include_extension or '.' not in file_name:
        return file_name, ''

    base, ext = file_name.rsplit('.', 1)
    return base, '.' + ext


def _numeration_matches(text: str) -> list[re.Match]:
    return list(re.finditer(r'\d+', text))


def _normalize_num_index(index:int|str, count:int)-> int:
    try:
        index = int(index)
    except (TypeError, ValueError):
        raise ValueError('index must be an int or numeric string')

    if count == 0:
        raise ValueError('No numeration found')
    if index == 0:
        raise ValueError('index=0 is not supported; use positive 1-based or negative indexing')

    if index > 0:
        pos = index - 1
    else:
        pos = count + index

    if pos < 0 or pos >= count:
        raise IndexError('index is out of numeration range')

    return pos


def _shift_num_str(num_str: str, shift: int, minus_char='-') -> str:
    value = int(num_str) + int(shift)
    width = len(num_str)

    if value < 0:
        if minus_char is None:
            value = 0
        else:
            return f'{minus_char}{abs(value):0{width}d}'

    return f'{value:0{width}d}'


def _shift_name_numeration(file_name: str, shift: int = 1, index: int | str = -1, **kwargs) -> str:
    """Shift one selected digit sequence inside a file name."""
    include_extension = kwargs.get('include_extension', kwargs.get('include_extention', False))
    minus_char = kwargs.get('minus_char', '-')

    text, tail = _split_name_parts(file_name, include_extension)
    matches = _numeration_matches(text)
    if not matches:
        return file_name

    try:
        pos = _normalize_num_index(index, len(matches))
    except (ValueError, IndexError):
        return file_name

    match = matches[pos]
    old_num = match.group(0)
    new_num = _shift_num_str(old_num, shift, minus_char)
    new_text = text[:match.start()] + new_num + text[match.end():]
    return new_text + tail


def shift_numeration(path, shift: int = 1, index: int | str = -1, **kwargs):
    """ Shift numerated file names for one path, a file collection, or a directory.
    Positive `index` is 1-based from the start; negative `index` counts from the end.
    By default, only the stem is searched; pass `include_extension=True` to include the suffix.
    """

    def _rename_existing_file(file_path: Path):
        old_name = file_path.name
        new_name = _shift_name_numeration(old_name, shift=shift, index=index, **kwargs)
        return _rename_file_path(file_path, new_name, overwrite_policy=overwrite_policy, print2cli=print2cli)

    def _shift_path_like(item):
        item_path = Path(item)

        if item_path.exists():
            if item_path.is_dir():
                raise ValueError(f'{item} is a directory; pass a single file or directory root')
            return _rename_existing_file(item_path)

        new_name = _shift_name_numeration(item_path.name, shift=shift, index=index, **kwargs)
        new_path = item_path.with_name(new_name)
        return str(new_path) if isinstance(item, str) else new_path

    def _iter_dir_files(root:Path) -> list[Path]:
        if sub_dirs:
            return [p for p in root.rglob('*') if p.is_file()]
        return [p for p in root.iterdir() if p.is_file()]

    def _shift_dir(root: Path) -> list[Path]:
        results = []
        for file_path in _iter_dir_files(root):
            results.append(_rename_existing_file(file_path))
        return results

    sub_dirs = kwargs.get('sub_dirs', False)
    overwrite_policy = kwargs.get('overwrite_policy', 'skip')
    print2cli = kwargs.get('print2cli', True)

    if isinstance(path, (list, tuple)):
        shifted = [_shift_path_like(item) for item in path]
        return type(path)(shifted)

    path_obj = Path(path)
    if path_obj.exists() and path_obj.is_dir():
        return _shift_dir(path_obj)

    return _shift_path_like(path)


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
