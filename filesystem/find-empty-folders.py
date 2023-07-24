from __future__ import generator_stop

import logging
import os
from fnmatch import fnmatch
from typing import Callable, Iterator

from genutility.filesystem import scandir_counts, scandir_error_log_warning


def log_error(path: str, exc: Exception) -> None:
    logging.warning("Removing <%s> failed: %s", path, exc)


def enum_empty_dirs(dirpath: str, pattern: str = "*") -> Iterator[str]:
    for entry, counts in scandir_counts(dirpath, files=False, others=False, onerror=scandir_error_log_warning):
        assert counts is not None  # because `files=False, others=False` above
        if counts.null():
            if fnmatch(entry.name, pattern):
                yield entry.path


def find_empty_dirs(
    dirpath: str, pattern: str = "*", remove: bool = False, errorfunc: Callable[[str, Exception], None] = log_error
) -> None:
    for path in enum_empty_dirs(dirpath, pattern):
        if remove:
            try:
                os.rmdir(path)
                print("Deleted", path)
            except OSError as e:
                errorfunc(path, e)
        else:
            print("Found", path)


if __name__ == "__main__":
    from argparse import ArgumentParser

    from genutility.args import is_dir

    parser = ArgumentParser(description="Delete empty folders. Run multiple times.")
    parser.add_argument("directory", type=is_dir, help="Directory to search")
    parser.add_argument("pattern", nargs="?", help="fnmatch pattern", default="*")
    parser.add_argument("--remove", action="store_true", help="Removed matching empty directories")
    args = parser.parse_args()

    find_empty_dirs(args.directory, args.pattern, args.remove)
