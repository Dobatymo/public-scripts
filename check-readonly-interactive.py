# /// script
# requires-python = ">=3.8"
# dependencies = [
#     "genutility[args,filesystem,stdio]>=0.0.123",
# ]
# ///
import logging
from argparse import ArgumentParser, ArgumentTypeError
from pathlib import Path
from typing import Iterable

from genutility.args import is_dir, is_dir_nofollow
from genutility.filesystem import is_writeable, make_writeable, scandir_rec
from genutility.stdio import confirm


def do(paths: Iterable[Path], yes: bool, follow_links: bool = False) -> None:
    for path in paths:
        for entry in scandir_rec(path, dirs=False, files=True, follow_symlinks=follow_links):
            stats = entry.stat()
            try:
                if not is_writeable(stats):
                    if yes or confirm(f"Make {entry.path} writeable?"):
                        make_writeable(entry.path, stats)
                        if yes:
                            print(f"Made {entry.path} writeable")
            except Exception:
                logging.exception(entry.path)


def main() -> None:
    parser = ArgumentParser()
    parser.add_argument("paths", metavar="PATH", type=Path, nargs="+")
    parser.add_argument("-y", "--yes", action="store_true", help="yes to all")
    parser.add_argument("--follow-links", action="store_true", help="Follow file and directory links during scanning")
    args = parser.parse_args()

    try:
        for path in args.paths:
            (is_dir if args.follow_links else is_dir_nofollow)(path)
    except ArgumentTypeError as e:
        parser.error(str(e))

    do(args.paths, args.yes, args.follow_links)


if __name__ == "__main__":
    main()
