from __future__ import generator_stop

import logging
import os.path
from os import DirEntry
from typing import Set

from genutility.filesystem import entrysuffix, iter_links, realpath_win, scandir_rec
from genutility.win.file import GetCompressedFileSize


def print_error(entry: DirEntry, exc) -> None:
    if isinstance(exc, PermissionError):
        logging.warning("PermissionError in: %s (%s)", entry.path, exc)
    else:
        logging.exception("Error in: %s", entry.path)


def is_sparse_or_compressed(entry: DirEntry) -> bool:
    return GetCompressedFileSize(entry.path) < entry.stat().st_size


def print_symlinks_sparse(path: str, ignore_exts: Set[str] = set()) -> None:
    for entry in scandir_rec(
        path,
        files=True,
        dirs=True,
        others=True,
        rec=True,
        follow_symlinks=False,
        errorfunc=print_error,
    ):
        if entry.is_symlink():
            try:
                if os.path.exists(realpath_win(entry.path)):
                    print(f"Valid: {entry.path}")
                else:
                    print(f"Invalid: {entry.path}")
            except Exception:
                logging.exception("Error: %s", entry.path)

        if entry.is_file():
            ext = entrysuffix(entry)
            try:
                if ext not in ignore_exts and is_sparse_or_compressed(entry):
                    print(f"Sparse or compressed: {entry.path}")
            except OSError as e:
                logging.exception("Error reading filesize of <%s>: %s", entry.path, e)


def print_links(path) -> None:
    for p in iter_links(path):
        print(p)


if __name__ == "__main__":
    from argparse import ArgumentParser

    logging.basicConfig(level=logging.DEBUG)

    DEFAULT_IGNORE = [".!ut", ".!qB"]

    parser = ArgumentParser()
    parser.add_argument("path", help="Input path to search recursively.")
    parser.add_argument(
        "--ignore-extensions",
        default=DEFAULT_IGNORE,
        help="File extensions to ignore for sparse checks.",
    )
    args = parser.parse_args()

    print("Ignoring extensions:", args.ignore_extensions)
    print("Symlinks or sparse")
    print_symlinks_sparse(args.path, set(args.ignore_extensions))

    print("Symlinks or junctions")
    print_links(args.path)
