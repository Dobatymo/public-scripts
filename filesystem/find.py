import csv
import logging
import os
import os.path
import re
import subprocess
from argparse import ArgumentDefaultsHelpFormatter, ArgumentParser, Namespace
from enum import Enum
from fnmatch import fnmatch
from pathlib import Path
from typing import Callable, Collection, Iterator, Optional

from genutility._files import to_dos_path
from genutility.args import is_dir, suffix_lower
from genutility.file import is_all_byte
from genutility.filesystem import PathType, scandir_counts, scandir_error_log_warning, scandir_ext, scandir_rec
from genutility.os import realpath
from genutility.rich import Progress, StdoutFile, get_double_format_columns
from genutility.win.file import GetCompressedFileSize
from rich.logging import RichHandler
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
    with StdoutFile(progress.progress.console, args.out, "xt", encoding="utf-8", highlight=False, soft_wrap=True) as fw:
        for entry in _files(args.path, args.include_extensions, args.exclude_extensions, progress):
            try:
                with open(entry, encoding=args.encoding) as fr:
                    fr.read()
            except UnicodeDecodeError:
                fw.write(f"{entry.path} failed to decode\n")
            except PermissionError:
                fw.write(f"Cannot access {entry.path}\n")
            else:
                if args.verbose:
                    fw.write(f"{entry.path}\n")
    return 0


def line_search_regex(args: Namespace, progress: Progress) -> int:
    num = 0
    with StdoutFile(
        progress.progress.console, args.out, "xt", encoding="utf-8", newline="", highlight=False, soft_wrap=True
    ) as csvfile:
        csvwriter = csv.writer(csvfile)
        csvwriter.writerow(["path", "line"])

        for entry in _files(args.path, args.include_extensions, args.exclude_extensions, progress):
            with open(entry.path, encoding=args.encoding, errors=args.errors) as fr:
                for line in fr:
                    m = args.pattern.search(line)
                    if m:
                        num += 1
                        csvwriter.writerow([entry.path, line])
                        if args.early_stop:
                            break

    logger.info("Found %d matches", num)
    return 0


def all_zero(args: Namespace, progress: Progress) -> int:
    num = 0
    with StdoutFile(
        progress.progress.console, args.out, "xt", encoding="utf-8", newline="", highlight=False, soft_wrap=True
    ) as csvfile:
        csvwriter = csv.writer(csvfile)
        csvwriter.writerow(["path", "filesize", "mtime"])

        for entry in _files(args.path, args.include_extensions, args.exclude_extensions, progress):
            stat = entry.stat()
            if stat.st_size != 0:
                with open(entry.path, "rb") as fr:
                    if is_all_byte(fr, b"\x00"):
                        num += 1
                        csvwriter.writerow([entry.path, stat.st_size, stat.st_mtime_ns])

    logger.info("Found %d all-zero files", num)
    return 0


def sparse_or_compressed(args: Namespace, progress: Progress) -> int:
    with StdoutFile(progress.progress.console, args.out, "xt", encoding="utf-8", highlight=False, soft_wrap=True) as fw:
        for entry in _files(args.path, args.include_extensions, args.exclude_extensions, progress):
            if entry.is_file():
                try:
                    if is_sparse_or_compressed(entry):
                        fw.write(f"Sparse or compressed: {entry.path}\n")
                except OSError as e:
                    logger.warning("Error reading filesize of <%s>: %s", entry.path, e)
    return 0


class SymlinkModes(Enum):
    any = 0
    valid = 1
    invalid = 2


def symlinks(args: Namespace, progress: Progress) -> int:
    mode = SymlinkModes[args.mode]
    with StdoutFile(progress.progress.console, args.out, "xt", encoding="utf-8", highlight=False, soft_wrap=True) as fw:
        for entry in p.track(
            scandir_rec(
                args.path,
                files=True,
                dirs=True,
                others=True,
                follow_symlinks=False,
                errorfunc=scandir_error_log_warning,
            ),
            description="Processed {task.completed} files or directories",
        ):
            if entry.is_symlink():
                try:
                    if mode == SymlinkModes.any:
                        fw.write(f"{entry.path}\n")
                    elif mode == SymlinkModes.valid:
                        if os.path.exists(realpath(entry.path)):
                            fw.write(f"{entry.path}\n")
                    elif mode == SymlinkModes.invalid:
                        if not os.path.exists(realpath(entry.path)):
                            fw.write(f"{entry.path}\n")
                    else:
                        assert False  # noqa: B011
                except Exception:
                    logger.exception("Error: %r", entry.path)
    return 0


def _find_parent_paths(path, file_name: Optional[str] = None, dir_name: Optional[str] = None) -> Iterator[Path]:
    files = file_name is not None
    dirs = dir_name is not None

    assert file_name or dir_name  # for mypy
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
            logger.error("Calling %r in <%s> failed: %s", e.cmd, path, force_decode(e.output, path))

    return 0


def _find_empty_dirs(basepath: Path, pattern: str = "*") -> Iterator[str]:
    for entry, counts in scandir_counts(basepath, files=False, others=False, onerror=scandir_error_log_warning):
        assert counts is not None  # because `files=False, others=False` above
        if counts.null():
            if fnmatch(entry.name, pattern):
                yield entry.path


def empty_dirs(args: Namespace, progress: Progress) -> int:
    with StdoutFile(progress.progress.console, args.out, "xt", encoding="utf-8", highlight=False, soft_wrap=True) as fw:
        for path in p.track(_find_empty_dirs(args.path, args.pattern)):
            if args.remove:
                try:
                    os.rmdir(path)
                    logging.info("Removed %r", path)
                except OSError as e:
                    logging.warning("Removing %r failed: %s", path, e)
            else:
                fw.write(f"{path}\n")


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

    ALL_ERRORS = (
        "strict",
        "ignore",
        "replace",
        "surrogateescape",
        "xmlcharrefreplace",
        "backslashreplace",
        "namereplace",
    )

    subparser_f = subparsers.add_parser(
        "line-search-regex",
        formatter_class=ArgumentDefaultsHelpFormatter,
        help="Search for files by matching each line by regex",
        epilog="""Examples.
Find cue files which contain catalog meta data:
find.py -i .cue line-search-regex -p "^CATALOG" .""",  # %(prog)s adds the action which doesn't allow other flags
    )
    subparser_f.set_defaults(func=line_search_regex)
    subparser_f.add_argument("-p", "--pattern", type=re.compile, required=True, help="Pattern to match line")
    subparser_f.add_argument("--early-stop", action="store_true", help="Stop processing file after first match")
    subparser_f.add_argument("--encoding", default="utf-8", help="File encoding")
    subparser_f.add_argument("--errors", choices=ALL_ERRORS, default="replace", help="File decoding error handling")

    subparser_g = subparsers.add_parser(
        "empty-dirs",
        formatter_class=ArgumentDefaultsHelpFormatter,
        help="Find empty directories",
    )
    subparser_g.set_defaults(func=empty_dirs)
    subparser_g.add_argument("-p", "--pattern", default="*", help="fnmatch pattern")
    subparser_g.add_argument("--remove", action="store_true", help="Remove matching empty directories")

    parser.add_argument("path", type=is_dir, help="Path to scan for files or directories")
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

    handler = RichHandler(log_time_format="%Y-%m-%d %H-%M-%S%Z")
    FORMAT = "%(message)s"

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG, format=FORMAT, handlers=[handler])
    else:
        logging.basicConfig(level=logging.INFO, format=FORMAT, handlers=[handler])

    if args.log:
        handler = logging.FileHandler(args.log, encoding="utf-8")
        logger.addHandler(handler)

    with RichProgress(*get_double_format_columns()) as progress:
        p = Progress(progress)
        try:
            ret = args.func(args, p)
        except FileExistsError as e:
            ret = 2
            logger.error(str(e))
        except Exception:
            ret = 2
            logger.exception("Action failed")

    sys.exit(ret)
