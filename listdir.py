# /// script
# requires-python = ">=3.8"
# dependencies = [
#     "genutility[args,filesystem,rich]>=0.0.122",
#     "polars",
#     "rich",
# ]
# ///
import csv
import os
import stat
import sys
import warnings
from argparse import ArgumentParser, RawDescriptionHelpFormatter
from contextlib import suppress
from itertools import chain
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import List, Optional

from genutility.args import non_negative_int
from genutility.filesystem import MyDirEntryT, scandir_rec
from genutility.rich import Progress, StdoutFileNoStyle
from rich.console import Console
from rich.progress import Progress as RichProgress
from rich.progress import TimeElapsedColumn

d = {
    stat.S_IFLNK: "l",
    stat.S_IFSOCK: "s",
    stat.S_IFREG: "-",
    stat.S_IFBLK: "b",
    stat.S_IFDIR: "d",
    stat.S_IFCHR: "c",
    stat.S_IFIFO: "p",
}


def get_file_type(stats: os.stat_result) -> str:
    file_type = d[stat.S_IFMT(stats.st_mode)]
    st_reparse_tag = getattr(stats, "st_reparse_tag", 0)

    if st_reparse_tag != 0:
        is_junction = st_reparse_tag == stat.IO_REPARSE_TAG_MOUNT_POINT
        if is_junction:
            assert file_type == "d", file_type
            return "j"

    return file_type


def write_row(csvwriter, entry: MyDirEntryT, error: Optional[Exception]) -> None:
    try:
        stats = entry.stat(follow_symlinks=False)
        file_type = get_file_type(stats)

        if __debug__:
            st_mode = stats.st_mode & ~stat.S_IMODE(stats.st_mode) & ~stat.S_IFMT(stats.st_mode)
            assert st_mode == 0, f"{st_mode:016b}"

        error_str = "" if error is None else str(error)

        csvwriter.writerow((file_type, entry.path, stats.st_size, stats.st_mtime_ns, error_str))
    except Exception as e:
        e.__cause__ = error
        raise RuntimeError("this shouldn't happen") from e


def query_csv(
    in_path: Path,
    out_path: Optional[Path],
    sort_column: str,
    descending: bool,
    offset: Optional[int],
    limit: Optional[int],
    errors_only: bool,
    in_place: bool,
    overwrite: bool,
) -> bool:
    import polars as pl

    dataframe = pl.read_csv(in_path)
    input_rows = dataframe.height
    if errors_only:
        if "error" not in dataframe.columns:
            raise ValueError("CSV column not found: 'error'")
        dataframe = dataframe.filter(pl.col("error").fill_null("") != "")

    if sort_column == "path-length":
        if "path" not in dataframe.columns:
            raise ValueError("CSV column not found: 'path'")
        sort_by = pl.col("path").str.len_chars()
    elif sort_column not in dataframe.columns:
        raise ValueError(f"CSV column not found: {sort_column!r}")
    else:
        sort_by = sort_column

    dataframe = dataframe.sort(sort_by, descending=descending)
    if offset:
        dataframe = dataframe.slice(offset)
    if limit is not None:
        dataframe = dataframe.head(limit)

    removed_rows = input_rows - dataframe.height
    if in_place and removed_rows:
        try:
            response = input(
                f"Query removes {removed_rows} of {input_rows} rows. Overwrite {os.fspath(in_path)!r}? [y/N] "
            )
        except EOFError:
            response = ""
        if response.casefold() not in ("y", "yes"):
            return False

    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="Polars found a filename")
        if in_place:
            temp_path = None
            try:
                with NamedTemporaryFile(
                    "wb", dir=in_path.parent, prefix=".listdir-", suffix=".tmp", delete=False
                ) as csvfile:
                    temp_path = Path(csvfile.name)
                    dataframe.write_csv(csvfile)
                os.replace(temp_path, in_path)
            except BaseException:
                if temp_path is not None:
                    with suppress(FileNotFoundError):
                        temp_path.unlink()
                raise
        elif out_path is None:
            dataframe.write_csv(sys.stdout.buffer)
        else:
            with out_path.open("wb" if overwrite else "xb") as csvfile:
                dataframe.write_csv(csvfile)
    return True


def scan_paths(
    in_paths: List[Path],
    all_mounts: bool,
    out_path: Optional[Path],
    overwrite: bool,
    parser: ArgumentParser,
) -> None:
    for path in in_paths:
        if not path.is_dir():
            parser.error(f"{os.fspath(path)} is not a valid directory")

    if all_mounts and sys.platform != "win32":
        parser.error("--all-mounts currently only supported on Windows.")

    if sys.stdout.isatty():
        content_console = Console()
        progress_console = content_console
    else:
        content_console = Console()
        progress_console = Console(stderr=True)

    if in_paths:
        paths = in_paths
    elif all_mounts:
        if sys.version_info >= (3, 12):
            paths = sorted(chain.from_iterable(os.listmounts(vol) for vol in os.listvolumes()))
        else:
            from genutility.win.device import find_volumes, get_volume_path_names

            paths = sorted(chain.from_iterable(get_volume_path_names(vol) for vol in find_volumes()))
    else:
        assert False

    mode = "wt" if overwrite else "xt"
    with StdoutFileNoStyle(content_console, out_path, mode, encoding="utf-8", newline="") as csvfile:
        fw = csv.writer(csvfile)
        fw.writerow(("type", "path", "size", "mtime", "error"))

        def write_error(entry: MyDirEntryT, e: Exception) -> None:
            write_row(fw, entry, e)

        for path in paths:
            with RichProgress(
                f"Scanning {os.fspath(path)}...",
                "{task.completed} entries",
                TimeElapsedColumn(),
                console=progress_console,
            ) as p:
                progress = Progress(p)

                for entry in progress.track(
                    scandir_rec(path, files=True, dirs=True, others=True, follow_symlinks=False, errorfunc=write_error)
                ):
                    write_row(fw, entry, None)


def main(argv=None) -> None:
    parser = ArgumentParser(description="Scan directories to CSV and query inventory CSV files.")
    subparsers = parser.add_subparsers(dest="mode", required=True)

    scan_parser = subparsers.add_parser(
        "scan",
        help="scan directories and write inventory CSV",
        description="Scan directories without following symlinks or junctions and write inventory CSV.",
        epilog="""Examples:
  py listdir.py scan C:\\ D:\\ --out-path inventory.csv
  py listdir.py scan --all-mounts --out-path mounts.csv --overwrite""",
        formatter_class=RawDescriptionHelpFormatter,
    )
    scan_parser.add_argument("in_paths", nargs="*", metavar="PATH", type=Path, help="directories to scan")
    scan_parser.add_argument("--all-mounts", action="store_true", help="scan all Windows mount points")
    scan_parser.add_argument("--out-path", type=Path, help="output CSV path (default: stdout)")
    scan_parser.add_argument("--overwrite", action="store_true", help="overwrite --out-path if it exists")

    query_parser = subparsers.add_parser(
        "query",
        help="filter, sort, and page an inventory CSV",
        description="Load an inventory CSV with Polars, filter and sort it, then write CSV.",
        epilog="""Examples:
  py listdir.py query inventory.csv --sort-column path-length --sort-order desc --in-place
  py listdir.py query inventory.csv --sort-column path --errors-only --in-place""",
        formatter_class=RawDescriptionHelpFormatter,
    )
    query_parser.add_argument("in_csv", metavar="CSV", type=Path, help="inventory CSV file to query")
    query_parser.add_argument("--sort-column", required=True, help="CSV column used to sort, or path-length")
    query_parser.add_argument("--sort-order", choices=("asc", "desc"), default="asc", help="sort order (default: asc)")
    query_parser.add_argument("--offset", type=non_negative_int, help="rows to skip after filtering and sorting")
    query_parser.add_argument("--limit", type=non_negative_int, help="maximum rows to write after --offset")
    query_parser.add_argument(
        "--errors-only", action="store_true", help="only write rows with a non-empty error column"
    )
    output_group = query_parser.add_mutually_exclusive_group()
    output_group.add_argument("--out-path", type=Path, help="output CSV path (default: stdout)")
    output_group.add_argument("--in-place", action="store_true", help="atomically replace the input CSV")
    query_parser.add_argument("--overwrite", action="store_true", help="overwrite --out-path if it exists")

    args = parser.parse_args(argv)
    if args.mode == "scan":
        if bool(args.in_paths) == args.all_mounts:
            scan_parser.error("provide directory paths or --all-mounts, but not both")
        scan_paths(args.in_paths, args.all_mounts, args.out_path, args.overwrite, scan_parser)
    elif args.mode == "query":
        if not args.in_csv.is_file():
            query_parser.error(f"{os.fspath(args.in_csv)} is not a valid file")
        if args.in_place and args.overwrite:
            query_parser.error("--overwrite cannot be used with --in-place")
        if args.in_place and args.in_csv.is_symlink():
            query_parser.error("--in-place does not replace symlink inputs")
        if args.out_path is not None and args.out_path.exists() and os.path.samefile(args.in_csv, args.out_path):
            query_parser.error("use --in-place to overwrite the input CSV")
        try:
            completed = query_csv(
                args.in_csv,
                args.out_path,
                args.sort_column,
                args.sort_order == "desc",
                args.offset,
                args.limit,
                args.errors_only,
                args.in_place,
                args.overwrite,
            )
        except ValueError as e:
            query_parser.error(str(e))
        if not completed:
            query_parser.exit(1, "Cancelled; input CSV was not changed.\n")
    else:
        assert False


if __name__ == "__main__":
    main()
