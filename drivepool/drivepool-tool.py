# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "genutility[file,filesystem,iter,rich,time]>=0.0.121",
#     "rich",
# ]
# ///

# Limitations:
# - Any file with multiple copies satisfies duplication, regardless of the configured count.
# - Duplication tags below root folders are ignored, including nested M-tag overrides.
# - Traversal errors are logged and ignored, but the scan is still marked complete.
# - Completed databases are reused without checking the pool, filters, or freshness.
# - --include is accepted but does not filter the scan.
# - --size-only leaves matching files marked UNCHECKED.
# - KeyboardInterrupt is logged but exits with a successful status.
# - PoolPart directories from different pools on separate drives are combined.

import json
import logging
import os
import os.path
import shutil
import sqlite3
import sys
from argparse import ArgumentDefaultsHelpFormatter, ArgumentParser, Namespace
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

from genutility.file import equal_files
from genutility.filesystem import scandir_error_log_warning, scandir_rec
from genutility.iter import all_equal, batch
from genutility.rich import Progress
from genutility.time import DeltaTime
from rich.highlighter import NullHighlighter
from rich.logging import RichHandler
from rich.progress import BarColumn, MofNCompleteColumn, TextColumn, TimeElapsedColumn
from rich.progress import Progress as RichProgress

PartsT = list[tuple[int, int, int]]
FilesT = dict[str, PartsT]

logger = logging.getLogger(__name__)
COMMIT_PERIOD_SECONDS = 60


@dataclass(frozen=True)
class DuplicationTag:
    duplication_count: Optional[int]
    subfolders_may_differ: bool

    @classmethod
    def from_bytes(cls, data: bytes) -> "DuplicationTag":
        value = data.decode("utf-16-le")
        subfolders_may_differ = value.startswith("M")
        if subfolders_may_differ:
            value = value[1:]

        if value == "I":
            duplication_count = None
        else:
            duplication_count = int(value)
            if duplication_count < 1:
                raise ValueError(f"Invalid duplication count: {duplication_count}")

        return cls(duplication_count, subfolders_may_differ)

    def __str__(self) -> str:
        value = "inherited" if self.duplication_count is None else f"x{self.duplication_count}"
        if self.subfolders_may_differ:
            value += " (subfolders may differ)"
        return value


def get_files(pool: list[Path], include: list[str], exclude: list[str], p: Progress) -> FilesT:
    files = defaultdict(list)

    for driveindex, drive in enumerate(p.track(pool, description="Processing pools...")):
        for entry in p.track(
            scandir_rec(
                drive, dirs=False, files=True, relative=True, follow_symlinks=False, errorfunc=scandir_error_log_warning
            ),
            description=f"Collecting files ({drive.drive})...",
        ):
            meta = entry.stat()

            for ign in exclude:
                if entry.relpath.startswith(ign + os.sep):
                    break
            else:
                files[entry.relpath].append((driveindex, meta.st_size, meta.st_mtime_ns))

    return files


def move_del(src: Path, where: str, filename_only: bool = False) -> None:
    if filename_only:
        tail = src.name
    else:
        tail = os.path.join(*src.parts[2:])

    dest = src.anchor / where / tail
    dest.parent.mkdir(parents=True, exist_ok=True)

    if not dest.exists():
        shutil.move(src, dest)
    else:
        logger.error("%s cannot be moved", src)


def find_good_copy(infos: PartsT, sizes: list[int]) -> Optional[int]:
    sizesdict = Counter(sizes)
    s = sizesdict.keys()
    c = sizesdict.items()
    if len(sizesdict) != 2:
        return None

    if s[0] > s[1] and c[0] > c[1]:
        return infos[sizes.index(s[1])][0]
    elif s[0] < s[1] and c[0] < c[1]:
        return infos[sizes.index(s[0])][0]
    return None


def check_files(conn: sqlite3.Connection, duplicated: list[str], size_only: bool, p: Progress) -> None:
    cur_select = conn.cursor()
    cur_update = conn.cursor()

    sql_select = f"SELECT idx, path FROM {TABLE_NAME_DRIVES} ORDER BY idx"  # noqa: S608
    pool = [Path(path) for (idx, path) in cur_select.execute(sql_select)]

    sql_count = f"SELECT count(*) FROM {TABLE_NAME_FILES} WHERE json_array_length(pool_parts) = 1"  # noqa: S608
    sql_select = f"SELECT path, pool_parts, status, error_message FROM {TABLE_NAME_FILES} WHERE json_array_length(pool_parts) = 1"  # noqa: S608
    total = cur_select.execute(sql_count).fetchone()[0]
    for path, _pool_parts, _status, _error_message in p.track(
        cur_select.execute(sql_select), total=total, description="Finding unduplicated files..."
    ):
        pool_parts = json.loads(_pool_parts)

        for dup in duplicated:
            if path.startswith(dup + os.sep):
                logger.warning("`%s` is not duplicated", path)

    sql_count = f"SELECT count(*) FROM {TABLE_NAME_FILES} WHERE status = ? AND json_array_length(pool_parts) > 1"  # noqa: S608
    sql_select = f"SELECT path, pool_parts, status, error_message FROM {TABLE_NAME_FILES} WHERE status = ? AND json_array_length(pool_parts) > 1"  # noqa: S608
    sql_update = f"UPDATE {TABLE_NAME_FILES} SET status = ?, error_message = ? WHERE path = ?"  # noqa: S608

    # https://www.sqlite.org/isolation.html
    # No Isolation Between Operations On The Same Database Connection

    total = cur_select.execute(sql_count, ("UNCHECKED",)).fetchone()[0]
    delta = DeltaTime()
    for path, _pool_parts, _status, _error_message in p.track(
        cur_select.execute(sql_select, ("UNCHECKED",)), total=total, description="Comparing files..."
    ):
        pool_parts = json.loads(_pool_parts)

        logger.debug("`%s` found on %i drives", path, len(pool_parts))

        driveindexes, sizes, _modtimes = zip(*pool_parts)

        if not all_equal(sizes):
            logger.warning("Filesizes different for %s %s", path, driveindexes)
            cur_update.execute(sql_update, ("INCONSISTENT_SIZE", None, path))
            assert cur_update.rowcount == 1
            # good_index = find_good_copy(pool_parts, sizes)
            # logger.warning("Found good index for %s: %s", path, good_index)
            continue

        if not size_only:
            try:
                paths = tuple(pool[di] / path for di in driveindexes)
                if not equal_files(*paths):
                    logger.warning("File content different for %s %s", path, driveindexes)
                    cur_update.execute(sql_update, ("INCONSISTENT_CONTENT", None, path))
                    assert cur_update.rowcount == 1
                else:
                    logger.debug("File content for %s consistent", path)
                    cur_update.execute(sql_update, ("CONSISTENT", None, path))
                    assert cur_update.rowcount == 1
            except OSError as e:
                logger.error("Error for %s: %s", path, e)
                cur_update.execute(sql_update, ("ERROR", str(e), path))
                assert cur_update.rowcount == 1

        if delta.get() > COMMIT_PERIOD_SECONDS:
            conn.commit()
            delta.reset()

    conn.commit()


def iter_pools() -> Iterator[Path]:
    for drive in os.listdrives():
        paths = list(Path(drive).glob("PoolPart.*"))
        if len(paths) == 0:
            pass
        elif len(paths) == 1:
            yield paths[0]
        else:
            raise ValueError(f"More than one pool found on {drive}")


TABLE_NAME_DRIVES = "drivepool_drives"
TABLE_NAME_FILES = "drivepool_files"
TABLE_NAME_META = "drivepool_meta"


def init_db(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()

    sql = f"""
    CREATE TABLE IF NOT EXISTS {TABLE_NAME_META} (
        id INTEGER PRIMARY KEY CHECK(id = 1),
        scan_insert_complete INTEGER NOT NULL CHECK(scan_insert_complete IN (0, 1))
    )
    """
    cur.execute(sql)
    cur.execute(f"INSERT OR IGNORE INTO {TABLE_NAME_META} (id, scan_insert_complete) VALUES (1, 0)")  # noqa: S608

    sql = f"""
    CREATE TABLE IF NOT EXISTS {TABLE_NAME_DRIVES} (
        idx INTEGER PRIMARY KEY,
        path TEXT NOT NULL
    )
    """
    cur.execute(sql)

    sql = f"""
    CREATE TABLE IF NOT EXISTS {TABLE_NAME_FILES} (
        path TEXT PRIMARY KEY,
        pool_parts JSON,
        status TEXT CHECK(status IN ('UNCHECKED', 'CONSISTENT', 'INCONSISTENT_SIZE', 'INCONSISTENT_CONTENT', 'ERROR', 'OUTOFDATE')),
        error_message TEXT
    )
    """
    cur.execute(sql)

    conn.commit()


def insert_files(conn: sqlite3.Connection, pool: list[str], files: FilesT, p: Progress) -> None:
    batch_size = 1000
    cur = conn.cursor()

    sql = f"INSERT INTO {TABLE_NAME_DRIVES} (idx, path) VALUES (?, ?)"  # noqa: S608
    parameters = [(idx, os.fspath(path)) for idx, path in enumerate(pool)]
    cur.executemany(sql, parameters)
    conn.commit()

    sql = f"""
    INSERT INTO {TABLE_NAME_FILES} (path, pool_parts, status, error_message)
    VALUES (?, ?, ?, ?)
    """  # noqa: S608

    for b in batch(p.track(files.items(), description="Storing file info..."), batch_size, list):
        parameters = [(path, json.dumps(parts), "UNCHECKED", None) for path, parts in b]
        cur.executemany(sql, parameters)
        conn.commit()

    cur.execute(f"UPDATE {TABLE_NAME_META} SET scan_insert_complete = 1 WHERE id = 1")  # noqa: S608
    conn.commit()


def query_duplication_count(pool: list[Path], relpath: Path) -> dict[Path, Optional[DuplicationTag]]:
    tags = {}

    for path in pool:
        target = path / relpath
        if not target.is_dir():
            logger.warning("Path not found: %s", target)
            continue

        try:
            with Path(f"{target}:DuplicationCount.Tag.CoveFs:$DATA").open("rb") as fr:
                tag = DuplicationTag.from_bytes(fr.read())
        except FileNotFoundError:
            tag = None

        tags[target] = tag

    return tags


def validate_duplication_tags(relpath: Path, tags: dict[Path, Optional[DuplicationTag]]) -> None:
    if len(set(tags.values())) > 1:
        values = ", ".join(f"{path}={tag}" for path, tag in tags.items())
        raise ValueError(f"Duplication tags disagree for {relpath}: {values}")


def get_duplicated(pool: list[Path]) -> list[str]:
    pool_tags = query_duplication_count(pool, Path())
    validate_duplication_tags(Path(), pool_tags)
    pool_duplication_count = max(
        (tag.duplication_count for tag in pool_tags.values() if tag is not None and tag.duplication_count is not None),
        default=1,
    )
    root_directories = sorted(
        {
            entry.name
            for poolpart in pool
            for entry in poolpart.iterdir()
            if entry.is_dir() and entry.name.casefold() != ".covefs"
        }
    )
    duplicated = []
    for relpath in root_directories:
        tags = query_duplication_count(pool, Path(relpath))
        validate_duplication_tags(Path(relpath), tags)
        duplication_count = max(
            (tag.duplication_count for tag in tags.values() if tag is not None and tag.duplication_count is not None),
            default=pool_duplication_count,
        )
        if duplication_count > 1:
            duplicated.append(relpath)

    return duplicated


def check_duplication(parser: ArgumentParser, args: Namespace) -> int:
    if args.include and args.exclude:
        parser.error("Cannot use --include and --exclude and the same time")

    pool = list(iter_pools())

    if not pool:
        print("No pools found")
        return 1

    columns = [
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
    ]

    with RichProgress(*columns) as progress:
        p = Progress(progress)
        is_old = args.db.exists()
        conn = sqlite3.connect(args.db)

        try:
            if not is_old:
                init_db(conn)
                files = get_files(pool, args.include, args.exclude, p)
                insert_files(conn, pool, files, p)

            try:
                scan_insert_complete = conn.execute(
                    f"SELECT scan_insert_complete FROM {TABLE_NAME_META} WHERE id = 1"  # noqa: S608
                ).fetchone()
            except sqlite3.OperationalError as e:
                raise RuntimeError(f"Database has no scan completion metadata: {args.db}") from e

            if scan_insert_complete != (1,):
                raise RuntimeError(f"Database scan/insert is incomplete: {args.db}")

            check_files(conn, get_duplicated(pool), args.size_only, p)
        finally:
            conn.close()

    return 0


def show_ads(parser: ArgumentParser, args: Namespace) -> int:
    pool = list(iter_pools())
    if not pool:
        print("No pools found")
        return 1

    tags = query_duplication_count(pool, args.path)

    for path, tag in tags.items():
        print(path.drive, tag if tag is not None else "not set")

    return 0


def show_duplicated(parser: ArgumentParser, args: Namespace) -> int:
    pool = list(iter_pools())
    if not pool:
        print("No pools found")
        return 1

    for relpath in get_duplicated(pool):
        print(relpath)

    return 0


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("-v", "--verbose", action="store_true", help="Show debug information")
    parser.add_argument("--log", type=Path, help="Write logs to file, otherwise to stderr")

    subparsers = parser.add_subparsers(dest="action", required=True)

    subparser_a = subparsers.add_parser(
        "check-duplication", formatter_class=ArgumentDefaultsHelpFormatter, help="Check consistency of pool duplication"
    )
    subparser_a.set_defaults(func=check_duplication)
    subparser_a.add_argument("--include", nargs="+", metavar="DIR", default=[], help="Include only these root folders")
    subparser_a.add_argument("--exclude", nargs="+", metavar="DIR", default=[], help="Ignore root folders")
    subparser_a.add_argument("--size-only", action="store_true", help="Only compare file sizes not actual file content")
    subparser_a.add_argument(
        "--db", type=Path, default="drivepool.sqlite", help="Path of Sqlite database which stores progress"
    )

    subparser_b = subparsers.add_parser(
        "show-ads", formatter_class=ArgumentDefaultsHelpFormatter, help="Show duplication alternate data streams"
    )
    subparser_b.set_defaults(func=show_ads)
    subparser_b.add_argument(
        "--path",
        metavar="PATH",
        type=Path,
        required=True,
        help="Path relative to the pool volume root",
    )

    subparser_c = subparsers.add_parser("show-duplicated", help="Show duplicated root folders")
    subparser_c.set_defaults(func=show_duplicated)

    args = parser.parse_args()

    handler = RichHandler(log_time_format="%Y-%m-%d %H-%M-%S%Z", highlighter=NullHighlighter())
    FORMAT = "%(message)s"

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG, format=FORMAT, handlers=[handler])
    else:
        logging.basicConfig(level=logging.INFO, format=FORMAT, handlers=[handler])

    if args.log:
        handler = logging.FileHandler(args.log, encoding="utf-8", delay=True)
        logger.addHandler(handler)

    try:
        sys.exit(args.func(parser, args))
    except KeyboardInterrupt:
        logger.warning("Interrupted by user. Exiting.")
    except Exception:
        logger.exception("Reading file failed. Exiting.")
        sys.exit(1)
