from __future__ import generator_stop

import logging
import os
import os.path
import subprocess
from argparse import ArgumentDefaultsHelpFormatter, ArgumentParser, Namespace
from pathlib import Path
from typing import Collection, Iterator, Optional

from genutility._files import to_dos_path
from genutility.args import is_dir, suffix_lower
from genutility.file import is_all_byte
from genutility.filesystem import PathType, scandir_error_log_warning, scandir_ext, scandir_rec
from genutility.os import realpath
from genutility.rich import Progress, get_double_format_columns
from genutility.win.file import GetCompressedFileSize
from rich.progress import Progress as RichProgress

logger = logging.getLogger(__name__)


def is_sparse_or_compressed(entry: os.DirEntry) -> bool:
    return GetCompressedFileSize(entry.path) < entry.stat().st_size


def _files(path: PathType, include: Collection[str], exclude: Collection[str], progress: Progress):
    include = set(include) if include is not None else None
    exclude = set(exclude) if exclude is not None else None

    yield from p.track(
        scandir_ext(path, include, exclude, errorfunc=scandir_error_log_warning),
        description="Processed {task.completed} files",
    )


def bad_encoding(args: Namespace, progress: Progress) -> int:
    for entry in _files(args.path, args.include_extensions, args.exclude_extensions, progress):
        try:
            with open(entry, encoding=args.encoding) as fr:
                fr.read()
        except UnicodeDecodeError:
            print(f"{entry.path} failed to decode")
        except PermissionError:
            print(f"Cannot access {entry.path}")
        else:
            if args.verbose:
                print(entry.path)
    return 0


def all_zero(args: Namespace, progress: Progress) -> int:
    for entry in _files(args.path, args.include_extensions, args.exclude_extensions, progress):
        if entry.stat().st_size != 0:
            with open(entry.path, "rb") as fr:
                if is_all_byte(fr, b"\x00"):
                    print(entry.path)
    return 0


def sparse_or_compressed(args: Namespace, progress: Progress) -> int:
    for entry in _files(args.path, args.include_extensions, args.exclude_extensions, progress):
        if entry.is_file():
            try:
                if is_sparse_or_compressed(entry):
                    print(f"Sparse or compressed: {entry.path}")
            except OSError as e:
                logger.warning("Error reading filesize of <%s>: %s", entry.path, e)
    return 0


from enum import Enum


class SymlinkModes(Enum):
    any = 0
    valid = 1
    invalid = 2


def symlinks(args: Namespace, progress: Progress) -> int:
    mode = SymlinkModes[args.mode]
    for entry in p.track(
        scandir_rec(
            args.path, files=True, dirs=True, others=True, follow_symlinks=False, errorfunc=scandir_error_log_warning
        ),
        description="Processed {task.completed} files or directories",
    ):
        if entry.is_symlink():
            try:
                if mode == SymlinkModes.any:
                    print(entry.path)
                elif mode == SymlinkModes.valid:
                    if os.path.exists(realpath(entry.path)):
                        print(entry.path)
                elif mode == SymlinkModes.invalid:
                    if not os.path.exists(realpath(entry.path)):
                        print(entry.path)
                else:
                    assert False
            except Exception:
                logger.exception("Error: %s", entry.path)
    return 0


def _find_parent_paths(path, file_name: Optional[str] = None, dir_name: Optional[str] = None) -> Iterator[Path]:
    files = file_name is not None
    dirs = dir_name is not None

    name = (file_name or dir_name).lower()

    for entry in scandir_rec(path, files=files, dirs=dirs, errorfunc=scandir_error_log_warning):
        if entry.name.lower() != name:
            continue

        yield Path(entry).parent


def force_decode(data: bytes, path: str) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        logger.warning("Failed to decode output for <%s> using utf-8", path)
        try:
            return data.decode()  # try default encoding
        except UnicodeDecodeError:
            return data.decode("latin1")  # should never fail


def find_and_run(args: Namespace, progress: Progress) -> int:
    if (args.file_name is None) == (args.dir_name is None):
        raise ValueError("either --file-name or --dir-name must be given")

    paths = list(
        progress.track(
            _find_parent_paths(args.path, args.file_name, args.dir_name),
            description="Processed {task.completed} files or directories",
        )
    )

    for _path in progress.track(paths, description="Processed {task.completed}/{task.total:.0f} paths"):
        path = os.fspath(_path)

        if not args.device_path:
            path = to_dos_path(path)

        try:
            subprocess.check_output(
                args.command,
                shell=args.shell,
                cwd=path,
                stderr=subprocess.STDOUT,
            )
        except subprocess.CalledProcessError as e:
            logger.error("Calling `%s` in <%s> failed: %s", e.cmd, path, force_decode(e.output, path))

    return 0


if __name__ == "__main__":
    import sys

    DEFAULT_ENCODING = "utf-8"

    parser = ArgumentParser(formatter_class=ArgumentDefaultsHelpFormatter)
    subparsers = parser.add_subparsers(dest="action", required=True)

    subparser_a = subparsers.add_parser(
        "bad-encoding",
        formatter_class=ArgumentDefaultsHelpFormatter,
        help="Find files which don't adhere to a certain encoding.",
    )
    subparser_a.set_defaults(func=bad_encoding)
    subparser_a.add_argument("--encoding", default=DEFAULT_ENCODING, help="Use this encoding to decode files")

    subparser_b = subparsers.add_parser(
        "all-zero", formatter_class=ArgumentDefaultsHelpFormatter, help="Find files which consist only of zero bytes"
    )
    subparser_b.set_defaults(func=all_zero)

    subparser_c = subparsers.add_parser(
        "sparse-or-compressed",
        formatter_class=ArgumentDefaultsHelpFormatter,
        help="Find files which are sparse or compressed",
    )
    subparser_c.set_defaults(func=sparse_or_compressed)

    subparser_d = subparsers.add_parser(
        "symlinks",
        formatter_class=ArgumentDefaultsHelpFormatter,
        help="Find symlinks",
    )
    subparser_d.set_defaults(func=symlinks)
    subparser_d.add_argument(
        "--mode",
        choices=tuple(e.name for e in SymlinkModes),
        default="any",
        help="Only display certain types of symlinks",
    )

    subparser_e = subparsers.add_parser(
        "find-and-run",
        formatter_class=ArgumentDefaultsHelpFormatter,
        help="Search for certain files or folders and then run a command in this directory",
    )
    subparser_e.set_defaults(func=find_and_run)
    subparser_e.add_argument("--file-name", help="Filename to find")
    subparser_e.add_argument("--dir-name", help="Name of directory to find. For example `.git`.")
    subparser_e.add_argument("--command", help="Command to run on each directory. For example `git gc`.", required=True)
    subparser_e.add_argument("--shell", action="store_true", help="Execute the specified command through the shell")
    subparser_e.add_argument(
        "--device-path",
        action="store_true",
        help="Use DOS device path as the commands current directory. Doesn't support --shell and also might cause issues when the command calls other processes.",
    )

    parser.add_argument("path", type=is_dir, help="Path to scan for files")
    parser.add_argument(
        "-i",
        "--include-extensions",
        type=suffix_lower,
        metavar=".EXT",
        action="append",
        help="File extensions to process",
    )
    parser.add_argument(
        "-e",
        "--exclude-extensions",
        type=suffix_lower,
        metavar=".EXT",
        action="append",
        nargs="+",
        help="File extensions not to process",
    )

    parser.add_argument(
        "--out",
        help="Write output to file, otherwise to stdout",
    )

    parser.add_argument(
        "--log",
        help="Write logs to file, otherwise to stderr",
    )

    parser.add_argument("-v", "--verbose", action="store_true")

    args = parser.parse_args()

    if args.log:
        handler = logging.FileHandler(args.log, encoding="utf-8")
        logger.addHandler(handler)

    with RichProgress(*get_double_format_columns()) as progress:
        p = Progress(progress)
        sys.exit(args.func(args, p))
