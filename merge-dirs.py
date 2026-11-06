# /// script
# requires-python = ">=3.8"
# dependencies = [
#     "genutility[args,exceptions,filesystem]>=0.0.123",
# ]
# ///
import errno
import logging
import os
from argparse import ArgumentParser, ArgumentTypeError
from pathlib import Path
from typing import Callable, Optional, cast

from genutility.args import is_dir, is_dir_nofollow
from genutility.exceptions import assert_choice
from genutility.filesystem import MyDirEntry, scandir_rec

logger = logging.getLogger(__name__)


def remove_empty_error_log(path: str, e: OSError) -> None:
    if e.errno != errno.ENOTEMPTY:
        logger.warning("Failed to remove %s (%s)", path, e)


def remove_empty_dirs(path: str, ignore_errors: bool = False, onerror: Optional[Callable] = None) -> None:
    for dirpath, _dirnames, filenames in os.walk(path, topdown=False):
        if filenames:
            continue  # skip remove

        try:
            os.rmdir(dirpath)
        except OSError as e:
            if ignore_errors:
                pass
            elif onerror:
                onerror(dirpath, e)
            else:
                raise


def rename_no_replace(src: Path, dst: Path) -> None:
    """Rename a file without replacing an existing destination."""
    if os.name == "nt":
        src.rename(dst)
    else:
        os.link(src, dst, follow_symlinks=False)
        src.unlink()


def move_rename(src: Path, dst: Path) -> None:
    for i in range(1, 2**31):
        candidate = dst.with_name(f"{dst.stem} ({i}){dst.suffix}")
        try:
            rename_no_replace(src, candidate)
        except FileExistsError:
            continue
        else:
            return

    raise RuntimeError(f"Could not find a free name for {dst}")


MODES = ("fail", "no_move", "overwrite", "overwrite-newer", "rename", "interactive")


def merge(src: Path, dst: Path, mode: str = "no_move", do: bool = False, follow_links: bool = False) -> None:
    assert_choice("mode", mode, MODES)

    if not src.is_dir() or not dst.is_dir():
        raise ValueError("src and dst must be directories")

    for entry in scandir_rec(src, files=True, dirs=True, follow_symlinks=follow_links, relative=True):
        entry = cast(MyDirEntry, entry)
        path = Path(entry.path)
        relpath = Path(entry.relpath)

        if entry.is_dir():
            if do:
                (dst / relpath).mkdir(parents=True, exist_ok=True)
            else:
                if not (dst / relpath).exists():
                    print("mkdir", dst / relpath)
        elif entry.is_file():
            if do:
                (dst / relpath.parent).mkdir(parents=True, exist_ok=True)
            else:
                if not (dst / relpath.parent).exists():
                    print("mkdir", dst / relpath.parent)
            target = dst / relpath
            if target.exists():
                if mode == "fail":
                    raise FileExistsError(os.fspath(path))
                elif mode == "no_move":
                    pass
                elif mode == "overwrite":
                    if do:
                        os.replace(os.fspath(path), os.fspath(target))
                    else:
                        print("Replace", path, target)
                elif mode == "overwrite-newer":
                    if path.stat().st_mtime_ns > target.stat().st_mtime_ns:
                        if do:
                            os.replace(os.fspath(path), os.fspath(target))
                        else:
                            print("Replace", path, target)
                elif mode == "rename":
                    if do:
                        move_rename(path, target)
                    else:
                        print("move_rename", path, target)
                elif mode == "interactive":
                    if do:
                        try:
                            answer = input(f"Replace {target} with {path}? [y/N] ")
                        except EOFError:
                            answer = ""
                        if answer.lower() in ("y", "yes"):
                            os.replace(os.fspath(path), os.fspath(target))
                    else:
                        print("Ask", path, target)
            else:
                if do:
                    path.rename(target)  # race condition on linux, us renameat2 with RENAME_NOREPLACE?
                else:
                    print("Rename", path, target)
        else:
            raise RuntimeError(f"Unhandled file: {path}")

    if do:
        remove_empty_dirs(os.fspath(src), onerror=remove_empty_error_log)


def main():
    parser = ArgumentParser(
        description="Merge source into target. Actual moves require both directories to be on the same filesystem; dry runs work across filesystems."
    )
    parser.add_argument("src", type=Path, help="Source directory")
    parser.add_argument("dst", type=is_dir_nofollow, help="Target directory")
    parser.add_argument(
        "--mode",
        choices=MODES,
        default="no_move",
        metavar="MODE",
        help=(
            "Collision policy for files already in dst: "
            "fail aborts, no_move skips (default), overwrite replaces dst, "
            "overwrite-newer replaces only when src is newer, rename keeps both "
            "using the first available numbered name, and interactive asks before replacing."
        ),
    )
    parser.add_argument("--do", action="store_true", help="Actually perform actions")
    parser.add_argument("--follow-links", action="store_true", help="Follow file and directory links during scanning")
    args = parser.parse_args()

    try:
        (is_dir if args.follow_links else is_dir_nofollow)(args.src)
    except ArgumentTypeError as e:
        parser.error(str(e))

    merge(args.src, args.dst, args.mode, args.do, args.follow_links)


if __name__ == "__main__":
    main()
