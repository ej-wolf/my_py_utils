"""     Local helper script for archive duplicate search.
    Scans zip files under a source dir, searches their archived members
    in a target dir,  and reports:
    1. archives whose content is fully duplicated in target
    2. archived files with no exact duplicate in target
"""

from pathlib import Path
from archive_handling import search_archive

#* defaults
ARCHIVE_GLOB = '*.zip'
REPORT_PATH = Path('archive_duplicate_report.txt')
THRESHOLD = 1.0
SEARCH_ARCHIVES = False
SUBDIR = True

def _write_report(report_path: Path, src_dir: Path, trg_dir: Path,
                  archive_glob: str, threshold: float,
                  subdir: bool, search_archives: bool,
                  full_dup_archives: list[Path],
                  unique_by_archive: dict[Path, list[dict]],
                  summaries: list[dict]):
    with report_path.open('w', encoding='utf-8') as f:
        f.write('Archive duplicate search report\n')
        f.write('=' * 72 + '\n\n')

        f.write('Search settings\n')
        f.write(f'- source      : {src_dir}\n')
        f.write(f'- target      : {trg_dir}\n')
        f.write(f'- archive glob: {archive_glob}\n')
        f.write(f'- threshold   : {threshold}\n')
        f.write(f'- subdir      : {subdir}\n')
        f.write(f'- search_archives: {search_archives}\n\n')

        f.write('Archives that contain only duplicates\n')
        f.write('-' * 72 + '\n')
        if not full_dup_archives:
            f.write('None\n')
        else:
            for arch_path in full_dup_archives:
                f.write(f'{arch_path}\n')
        f.write('\n')

        f.write('Per-archive summary\n')
        f.write('-' * 72 + '\n')
        for item in summaries:
            f.write(f"{item['archive'].name}: members={item['members_total']}, "
                    f"matched={item['members_matched']}, "
                    f"similarity={item['similarity']:.5f}, ok={item['ok']}\n"
                    )
        f.write('\n')

        f.write('Unique archived files (no exact duplicate in target)\n')
        f.write('-' * 72 + '\n')
        if not unique_by_archive:
            f.write('None\n')
        else:
            for arch_path, rows in unique_by_archive.items():
                f.write(f'\n{arch_path}\n')
                for row in rows:
                    best = row['file2'] or '<no match>'
                    f.write(f"  {row['file1']}\n"
                            f"    best target : {best}\n"
                            f"    similarity  : {row['similarity']:.5f}\n")


def main(src_dir, trg_dir, **kwargs):
    """Search archive members under `src_dir` against files under `trg_dir`."""
    src_dir = Path(src_dir)
    trg_dir = Path(trg_dir)
    archive_glob = kwargs.get('archive_glob', ARCHIVE_GLOB)
    report_path = Path(kwargs.get('report_path', REPORT_PATH))
    threshold = kwargs.get('threshold', THRESHOLD)
    search_archives = kwargs.get('search_archives', SEARCH_ARCHIVES)
    subdir = kwargs.get('subdir', SUBDIR)

    zip_files = sorted(src_dir.glob(archive_glob))
    if not src_dir.is_dir():
        raise FileNotFoundError(f'Source dir not found: {src_dir}')
    if not trg_dir.exists():
        raise FileNotFoundError(f'Target path not found: {trg_dir}')
    if not zip_files:
        raise FileNotFoundError(f'No archives matching {archive_glob!r} found in {src_dir}')

    full_dup_archives = []
    unique_by_archive = {}
    summaries = []

    for zip_path in zip_files:
        ok, report = search_archive( zip_path, trg_dir, threshold=threshold,
                                    subdir=subdir, search_archives=search_archives,)

        member_rows = report[:-1]
        summary = report[-1]

        if ok:
            full_dup_archives.append(zip_path)

        unique_rows = [row for row in member_rows if row['info']['matches_count'] == 0]
        if unique_rows:
            unique_by_archive[zip_path] = unique_rows

        summaries.append({
            'archive': zip_path,
            'ok': ok,
            'members_total': summary['info']['archive_members_total'],
            'members_matched': summary['info']['archive_members_matched'],
            'similarity': summary['similarity'],
        })

    _write_report(report_path, src_dir, trg_dir, archive_glob, threshold, subdir,
                  search_archives, full_dup_archives, unique_by_archive, summaries)

    print(f'Archives scanned : {len(zip_files)}')
    print(f'Fully duplicated : {len(full_dup_archives)}')
    print(f'Archives with unique files: {len(unique_by_archive)}')
    print(f'Text report saved to: {report_path}')

    return {'zip_files': zip_files,
            'full_dup_archives': full_dup_archives,
            'unique_by_archive': unique_by_archive,
            'summaries': summaries,
            'report_path': report_path,
            }

#143(,1,)->127()
if __name__ == '__main__':

    src_d = input('Source dir: ').strip()
    trg_d = input('Target dir: ').strip()
    main(src_d, trg_d)
