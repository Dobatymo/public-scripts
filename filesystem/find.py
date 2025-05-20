import csv
import logging
import os
import os.path
import re
import subprocess
from argparse import ArgumentDefaultsHelpFormatter, ArgumentParser, Namespace
from collections import defaultdict
from enum import Flag, auto
from fnmatch import fnmatch
from functools import reduce
from operator import or_
from pathlib import Path
from typing import Collection, Iterator, Optional

from genutility._files import to_dos_path
from genutility.args import ascii, base64, is_dir, suffix_lower
from genutility.file import is_all_byte, read_file
from genutility.filesystem import PathType, scandir_counts, scandir_error_log_warning, scandir_ext, scandir_rec
from genutility.os import islink, realpath
from genutility.rich import MarkdownHighlighter, Progress, StdoutFileNoStyle, get_double_format_columns
from genutility.win.file import GetCompressedFileSize
from rich.logging import RichHandler
from rich.progress import Progress as RichProgress
from send2trash import send2trash

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
    with StdoutFileNoStyle(progress.progress.console, args.out, "xt") as fw:
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
    with StdoutFileNoStyle(progress.progress.console, args.out, "xt", newline="") as csvfile:
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
    with StdoutFileNoStyle(progress.progress.console, args.out, "xt", newline="") as csvfile:
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


def has_filesize(args: Namespace, progress: Progress) -> int:
    num = 0
    with StdoutFileNoStyle(progress.progress.console, args.out, "xt", newline="") as csvfile:
        csvwriter = csv.writer(csvfile)
        csvwriter.writerow(["path", "filesize", "mtime"])

        for entry in _files(args.path, args.include_extensions, args.exclude_extensions, progress):
            stat = entry.stat()
            if stat.st_size == args.size:
                num += 1
                csvwriter.writerow([entry.path, stat.st_size, stat.st_mtime_ns])

    logger.info("Found %d files with size %d", num, args.size)
    return 0


def sparse_or_compressed(args: Namespace, progress: Progress) -> int:
    with StdoutFileNoStyle(progress.progress.console, args.out, "xt") as fw:
        for entry in _files(args.path, args.include_extensions, args.exclude_extensions, progress):
            if entry.is_file():
                try:
                    if is_sparse_or_compressed(entry):
                        fw.write(f"Sparse or compressed: {entry.path}\n")
                except OSError as e:
                    logger.warning("Error reading filesize of `%s`: %s", entry.path, e)
    return 0


class LinkModes(Flag):
    symlink = auto()
    junction = auto()
    valid = auto()
    invalid = auto()


def symlinks(args: Namespace, progress: Progress) -> int:
    mode = reduce(or_, [LinkModes[mode] for mode in args.modes])

    if LinkModes.symlink not in mode and LinkModes.junction not in mode:
        mode |= LinkModes.symlink
        mode |= LinkModes.junction

    if LinkModes.valid not in mode and LinkModes.invalid not in mode:
        mode |= LinkModes.valid
        mode |= LinkModes.invalid

    with StdoutFileNoStyle(progress.progress.console, args.out, "xt") as fw:
        for entry in p.track(
            scandir_rec(
                args.path,
                files=True,
                dirs=True,
                others=True,
                rec=True,
                follow_symlinks=False,
                errorfunc=scandir_error_log_warning,
            ),
            description="Processed {task.completed} files or directories",
        ):
            if entry.is_symlink():
                is_symlink = True
                is_junction = False
            elif islink(entry):
                is_symlink = False
                is_junction = True
            else:
                continue

            try:
                print_symlink = LinkModes.symlink in mode
                print_junction = LinkModes.junction in mode
                if (print_symlink == is_symlink) or (print_junction == is_junction):
                    if LinkModes.valid in mode and LinkModes.invalid in mode:
                        fw.write(f"{entry.path}\n")
                    elif LinkModes.valid in mode:
                        if os.path.exists(realpath(entry.path)):
                            fw.write(f"{entry.path}\n")
                    elif LinkModes.invalid in mode:
                        if not os.path.exists(realpath(entry.path)):
                            fw.write(f"{entry.path}\n")
                    else:
                        assert False  # noqa: B011

            except Exception:
                logger.exception("Error in `%s`", entry.path)
    return 0


def _find_parent_paths(path, file_name: Optional[str] = None, dir_name: Optional[str] = None) -> Iterator[Path]:
    files = file_name is not None
    dirs = dir_name is not None

    assert file_name or dir_name  # for mypy
    name = (file_name or dir_name).lower()  # type: ignore[union-attr]

    for entry in scandir_rec(path, files=files, dirs=dirs, errorfunc=scandir_error_log_warning):
        if entry.name.lower() != name:
            continue

        yield Path(entry).parent


def force_decode(data: bytes, path: str) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        logger.warning("Failed to decode output for `%s` using utf-8", path)
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
            subprocess.check_output(args.command, shell=args.shell, cwd=path, stderr=subprocess.STDOUT)
        except subprocess.CalledProcessError as e:
            logger.error("Calling `%s` in `%s` failed: %s", e.cmd, path, force_decode(e.output, path))

    return 0


def _find_empty_dirs(basepath: Path, pattern: str = "*") -> Iterator[str]:
    for entry, counts in scandir_counts(
        basepath, files=False, others=False, follow_symlinks=False, onerror=scandir_error_log_warning
    ):
        assert counts is not None  # because `files=False, others=False` above
        if counts.null():
            if fnmatch(entry.name, pattern):
                yield entry.path


def empty_dirs(args: Namespace, progress: Progress) -> int:
    with StdoutFileNoStyle(progress.progress.console, args.out, "xt") as fw:
        for path in p.track(_find_empty_dirs(args.path, args.pattern)):
            if args.action == "print":
                fw.write(f"{path}\n")
            elif args.action == "remove":
                try:
                    os.rmdir(path)
                    logger.info("Removed `%s`", path)
                except OSError as e:
                    logger.warning("Removing `%s` failed: %s", path, e)
            elif args.action == "trash":
                try:
                    send2trash(path)
                    logger.info("Trashed `%s`", path)
                except OSError as e:
                    logger.warning("Trashing `%s` failed: %s", path, e)
            else:
                raise ValueError(f"Invalid action: {args.action}")
    return 0


def long_paths(args: Namespace, progress: Progress) -> int:
    with StdoutFileNoStyle(progress.progress.console, args.out, "xt", newline="") as csvfile:
        csvwriter = csv.writer(csvfile)
        csvwriter.writerow(["length", "relpath"])
        for entry in p.track(scandir_rec(args.path, files=True, dirs=True, rec=True, relative=True)):
            if len(entry.relpath) >= args.length:
                csvwriter.writerow([len(entry.relpath), entry.relpath])
    return 0


def exact_content_match(args: Namespace, progress: Progress) -> int:
    content = args.base64_content or args.ascii_content.encode("ascii")

    with StdoutFileNoStyle(progress.progress.console, args.out, "xt") as fw:
        for entry in _files(args.path, args.include_extensions, args.exclude_extensions, progress):
            path = entry.path
            if entry.stat().st_size == len(content):
                data = read_file(path, "rb")
                if data == content:
                    if args.trash:
                        try:
                            send2trash(path)
                            logger.info("Trashed `%s`", path)
                        except OSError as e:
                            logger.warning("Trashing `%s` failed: %s", path, e)
                    else:
                        fw.write(f"{path}\n")
    return 0


def transformed_dups(args: Namespace, progress: Progress) -> int:
    if bool(args.sub_pattern) != bool(args.sub_replacement):
        raise ValueError("Both pattern and replacement or neither must be given")

    logger.info("Using pattern `%s` and repl `%s`", args.sub_pattern.pattern, args.sub_replacement)

    out = defaultdict(list)

    with StdoutFileNoStyle(progress.progress.console, args.out, "xt") as fw:
        for entry in _files(args.path, args.include_extensions, args.exclude_extensions, progress):
            key = {"path": entry.path, "name": entry.name, "size": entry.stat().st_size}[args.key]

            if args.sub_pattern:
                assert isinstance(key, str)
                new_key, n_subs = args.sub_pattern.subn(args.sub_replacement, key)
                if n_subs > 0:
                    out[new_key].append(entry.path)
            else:
                out[key].append(entry.path)

        out = {key: paths for key, paths in out.items() if len(paths) >= args.at_least}
        for key, paths in sorted(out.items(), key=lambda pair: len(pair[1]), reverse=True):
            fw.write(f"{len(paths)}\t{key!r}\n")
            for path in paths:
                fw.write(f"{path}\n")
            fw.write("\n")

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

    subparser_d = subparsers.add_parser("symlinks", formatter_class=ArgumentDefaultsHelpFormatter, help="Find symlinks")
    subparser_d.set_defaults(func=symlinks)
    subparser_d.add_argument(
        "--modes",
        nargs="+",
        choices=tuple(e.name for e in LinkModes),
        default=tuple(e.name for e in LinkModes),
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
        "empty-dirs", formatter_class=ArgumentDefaultsHelpFormatter, help="Find empty directories"
    )
    subparser_g.set_defaults(func=empty_dirs)
    subparser_g.add_argument("-p", "--pattern", default="*", help="fnmatch pattern")
    subparser_g.add_argument(
        "--action",
        choices=("print", "remove", "trash"),
        default="print",
        help="What to do with the found empty directories",
    )

    subparser_h = subparsers.add_parser(
        "long-paths", formatter_class=ArgumentDefaultsHelpFormatter, help="Find long paths"
    )
    subparser_h.set_defaults(func=long_paths)
    subparser_h.add_argument("--length", type=int, required=True, help="Print all paths longer than length")

    subparser_i = subparsers.add_parser(
        "exact-content-match",
        formatter_class=ArgumentDefaultsHelpFormatter,
        help="Find files by exact content matching",
    )
    subparser_i.set_defaults(func=exact_content_match)
    i_group = subparser_i.add_mutually_exclusive_group(required=True)
    i_group.add_argument("--ascii-content", type=ascii, help="Find files which match this text")
    i_group.add_argument(
        "--base64-content", type=base64, help="Find binary files which match this base64 encoded string"
    )
    subparser_i.add_argument("--trash", action="store_true", help="Send matching files to trash")

    subparser_j = subparsers.add_parser(
        "transformed-dups", formatter_class=ArgumentDefaultsHelpFormatter, help="Find duplicate paths afters transform"
    )
    subparser_j.set_defaults(func=transformed_dups)
    subparser_j.add_argument(
        "--key", choices=("path", "name", "size"), required=True, help="Key to apply transformation to"
    )
    subparser_j.add_argument("--sub-pattern", type=re.compile, help="Regex sub pattern")
    subparser_j.add_argument("--sub-replacement", type=str, help="Regex sub replacement")
    subparser_j.add_argument(
        "--at-least", metavar="N", type=int, default=2, help="Print only groups with at least N paths"
    )

    subparser_k = subparsers.add_parser(
        "has-filesize", formatter_class=ArgumentDefaultsHelpFormatter, help="Find files which have a certain filesize"
    )
    subparser_k.set_defaults(func=has_filesize)
    subparser_k.add_argument("--size", type=int, required=True, help="Key to apply transformation to")

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
        help="File extensions not to process",
    )

    parser.add_argument("--out", type=Path, help="Write output to file, otherwise to stdout")
    parser.add_argument("--log", type=Path, help="Write logs to file, otherwise to stderr")
    parser.add_argument("-v", "--verbose", action="store_true")

    args = parser.parse_args()

    handler = RichHandler(log_time_format="%Y-%m-%d %H-%M-%S%Z", highlighter=MarkdownHighlighter())
    FORMAT = "%(message)s"

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG, format=FORMAT, handlers=[handler])
    else:
        logging.basicConfig(level=logging.INFO, format=FORMAT, handlers=[handler])

    if args.log:
        handler = logging.FileHandler(args.log, encoding="utf-8", delay=True)
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
