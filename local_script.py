"""Local helper script for archive duplicate search.

Scans zip files under SRC_DIR, searches their archived members in TARGET_DIR,
and writes a text report with:
1. archives whose content is fully duplicated in target
2. archived files with no exact duplicate in target
"""

from pathlib import Path

from archive_handling import search_archive


SRC_DIR = Path('./my_pc/my_backup/zip_files')
TARGET_DIR = Path('./my_pc/other_dirve')
ARCHIVE_GLOB = '*.zip'
REPORT_PATH = Path('archive_duplicate_report.txt')
THRESHOLD = 1.0
SEARCH_ARCHIVES = False
SUBDIR = True


def _write_report(report_path: Path, full_dup_archives: list[Path], unique_by_archive: dict[Path, list[dict]], summaries: list[dict]):
    with report_path.open('w', encoding='utf-8') as f:
        f.write('Archive duplicate search report\n')
        f.write('=' * 72 + '\n\n')

        f.write('Search settings\n')
        f.write(f'- source      : {SRC_DIR}\n')
        f.write(f'- target      : {TARGET_DIR}\n')
        f.write(f'- archive glob: {ARCHIVE_GLOB}\n')
        f.write(f'- threshold   : {THRESHOLD}\n')
        f.write(f'- subdir      : {SUBDIR}\n')
        f.write(f'- search_archives: {SEARCH_ARCHIVES}\n\n')

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
            f.write(
                f"{item['archive'].name}: members={item['members_total']}, "
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
                    f.write(
                        f"  {row['file1']}\n"
                        f"    best target : {best}\n"
                        f"    similarity  : {row['similarity']:.5f}\n"
                    )


def main():
    zip_files = sorted(SRC_DIR.glob(ARCHIVE_GLOB))
    if not SRC_DIR.is_dir():
        raise FileNotFoundError(f'Source dir not found: {SRC_DIR}')
    if not TARGET_DIR.exists():
        raise FileNotFoundError(f'Target path not found: {TARGET_DIR}')
    if not zip_files:
        raise FileNotFoundError(f'No archives matching {ARCHIVE_GLOB!r} found in {SRC_DIR}')

    full_dup_archives = []
    unique_by_archive = {}
    summaries = []

    for zip_path in zip_files:
        ok, report = search_archive(
            zip_path,
            TARGET_DIR,
            threshold=THRESHOLD,
            subdir=SUBDIR,
            search_archives=SEARCH_ARCHIVES,
        )

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

    _write_report(REPORT_PATH, full_dup_archives, unique_by_archive, summaries)

    print(f'Archives scanned : {len(zip_files)}')
    print(f'Fully duplicated : {len(full_dup_archives)}')
    print(f'Archives with unique files: {len(unique_by_archive)}')
    print(f'Text report saved to: {REPORT_PATH}')


if __name__ == '__main__':
    main()
