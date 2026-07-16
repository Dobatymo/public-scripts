# /// script
# requires-python = ">=3.8"
# dependencies = [
#     "genutility[filesystem,rich]",
#     "rich",
# ]
# ///
import csv
import stat
from argparse import ArgumentParser
from pathlib import Path

from genutility.filesystem import MyDirEntryT, scandir_rec
from genutility.rich import Progress
from rich.progress import Progress as RichProgress

d = {
    stat.S_IFLNK: "l",
    stat.S_IFSOCK: "s",
    stat.S_IFREG: "-",
    stat.S_IFBLK: "b",
    stat.S_IFDIR: "d",
    stat.S_IFCHR: "c",
    stat.S_IFIFO: "p",
}


def get_file_type(st_mode: int) -> str:
    file_type = stat.S_IFMT(st_mode)
    return d[file_type]


def write_row(fw, entry: MyDirEntryT, error: str) -> None:
    stats = entry.stat(follow_symlinks=False)
    file_type = get_file_type(stats.st_mode)
    st_mode = stats.st_mode & ~stat.S_IMODE(stats.st_mode) & ~stat.S_IFMT(stats.st_mode)
    assert st_mode == 0, f"{st_mode:016b}"

    is_reparse_point = stats.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT == stat.FILE_ATTRIBUTE_REPARSE_POINT
    reparse_point = "R" if is_reparse_point else "-"

    fw.writerow((file_type, reparse_point, entry.path, stats.st_size, stats.st_mtime_ns, error))


def main() -> None:
    parser = ArgumentParser()
    parser.add_argument("--in-path", type=Path, required=True)
    parser.add_argument("--out-path", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    assert args.in_path.is_dir()

    with RichProgress() as p:
        progress = Progress(p)
        mode = "wt" if args.overwrite else "xt"
        with args.out_path.open(mode, encoding="utf-8", newline="") as csvfile:
            fw = csv.writer(csvfile)
            fw.writerow(("type", "reparse_point", "path", "size", "mtime", "error"))

            def write_error(entry: MyDirEntryT, e: Exception) -> None:
                write_row(fw, entry, str(e))

            for entry in progress.track(
                scandir_rec(
                    args.in_path, files=True, dirs=True, others=True, follow_symlinks=False, errorfunc=write_error
                )
            ):
                write_row(fw, entry, "")


if __name__ == "__main__":
    main()
