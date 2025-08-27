# /// script
# requires-python = ">=3.8"
# dependencies = [
#     "genutility[filesystem,stdio]",
# ]
# ///
import logging
from argparse import ArgumentParser
from pathlib import Path
from typing import Iterable

from genutility.filesystem import is_writeable, make_writeable, scandir_rec
from genutility.stdio import confirm


def do(paths: Iterable[Path], yes: bool) -> None:
    for path in paths:
        for entry in scandir_rec(path, dirs=False, files=True):
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
    args = parser.parse_args()

    do(args.paths, args.yes)


if __name__ == "__main__":
    main()
