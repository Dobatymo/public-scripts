# /// script
# requires-python = ">=3.8"
# dependencies = [
#     "genutility[filesystem,rich]>=0.0.121",
#     "rich",
# ]
# ///
import csv
import os
import stat
import sys
from argparse import ArgumentParser
from itertools import chain
from pathlib import Path
from typing import Optional

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


def main() -> None:
    parser = ArgumentParser()
    group_in = parser.add_mutually_exclusive_group(required=True)
    group_in.add_argument("--in-paths", nargs="+", type=Path)
    group_in.add_argument("--all-mounts", action="store_true")
    parser.add_argument("--out-path", type=Path)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    if args.in_paths:
        for path in args.in_paths:
            if not path.is_dir():
                parser.error(f"{os.fspath(path)} is not a valid directory")

    if args.all_mounts and sys.platform != "win32":
        parser.error("--all-mounts currently only supported on Windows.")

    if sys.stdout.isatty():
        content_console = Console()
        progress_console = content_console
    else:
        content_console = Console()
        progress_console = Console(stderr=True)

    if args.in_paths:
        paths = args.in_paths
    elif args.all_mounts:
        if sys.version_info >= (3, 12):
            paths = sorted(chain.from_iterable(os.listmounts(vol) for vol in os.listvolumes()))
        else:
            from genutility.win.device import find_volumes, get_volume_path_names

            paths = sorted(chain.from_iterable(get_volume_path_names(vol) for vol in find_volumes()))
    else:
        assert False

    mode = "wt" if args.overwrite else "xt"
    with StdoutFileNoStyle(content_console, args.out_path, mode, encoding="utf-8", newline="") as csvfile:
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


if __name__ == "__main__":
    main()
