# /// script
# requires-python = ">=3.8"
# dependencies = [
#     "genutility[datetime,filesystem,logging,rich]",
#     "rich",
# ]
# ///
import contextlib
import logging
import mmap
import os
import re
import stat
from argparse import ArgumentDefaultsHelpFormatter, ArgumentParser, Namespace
from pathlib import Path
from typing import Optional

from genutility.datetime import now
from genutility.filesystem import filter_recall, scandir_error_log_warning, scandir_rec
from genutility.logging import IsoDatetimeFormatter
from genutility.rich import Progress
from rich.highlighter import NullHighlighter
from rich.logging import RichHandler
from rich.progress import BarColumn, MofNCompleteColumn, TextColumn, TimeElapsedColumn
from rich.progress import Progress as RichProgress

"""
from cwinsdk.um.ntsecapi import LsaNtStatusToWinError
from cwinsdk.shared.ntstatus import STATUS_FILE_CORRUPT_ERROR
from cwinsdk.shared.winerror import ERROR_FILE_CORRUPT
LsaNtStatusToWinError(STATUS_FILE_CORRUPT_ERROR) == ERROR_FILE_CORRUPT  # 1392
"""

logger = logging.getLogger(None)


def get_block_device_size(fp) -> Optional[int]:
    try:
        # usually works on linux but not on windows
        filesize = fp.seek(0, os.SEEK_END)
        fp.seek(0, os.SEEK_SET)
    except OSError:
        filesize = None

    if filesize is None and os.name == "nt":
        from genutility.win.device import Storage

        with Storage.from_file(fp) as h:
            # try to get filesystem size, will fails if it's not a volume with a filesytem
            try:
                fs = h.fs_full_size()
                filesize = fs["TotalAllocationUnits"] * fs["SectorsPerAllocationUnit"] * fs["BytesPerSector"]
            except OSError:
                filesize = None

            # get the drive or the partition size
            # the partition size is usually slightly larger then the filesystem size
            if filesize is None:
                filesize = h.length_info()

    return filesize


def read_path(path: Path, chunk_size: int, p: Progress, *, buffering: int = -1, use_mmap: bool = False) -> bool:
    """Returns True if the file (or device) was read sucessfully and False if an error was found."""

    logger.info("Reading %r with chunk_size=%d, buffering=%s, mmap=%s", path, chunk_size, buffering, use_mmap)

    stats = path.stat()
    filesize: Optional[int] = stats.st_size
    is_block_device = stat.S_ISBLK(stats.st_mode)
    is_buffered = buffering != 0

    ret = True

    with open(path, "rb", buffering=buffering) as fp:
        if filesize == 0 and is_block_device:
            filesize = get_block_device_size(fp)
            if filesize is None:
                logger.warning("Could not read size for block device")

        if use_mmap:
            context = mmap.mmap(fp.fileno(), 0, access=mmap.ACCESS_READ)
        else:
            context = contextlib.nullcontext(fp)

        with context as fr:
            old_pos: Optional[int] = None
            total_read = 0
            with p.task(total=filesize, description=f"Reading {path.name}", transient=True) as task:
                while True:
                    cur_pos = fr.tell()
                    task.update(completed=cur_pos)
                    if old_pos is not None:
                        delta = cur_pos - old_pos
                        if delta == 0:
                            logger.warning("At %d, read did not advance, skip", cur_pos)
                            fr.seek(chunk_size, os.SEEK_CUR)
                            cur_pos += chunk_size
                        elif delta == chunk_size:
                            pass
                        elif is_buffered and cur_pos != filesize:
                            # should only occur for unbuffered reads or at the end of the file
                            logger.warning("At %d, only read %d/%d bytes", cur_pos, delta, chunk_size)

                    try:
                        out = fr.read(chunk_size)
                        total_read += len(out)
                    except OSError as e:
                        winerror = getattr(e, "winerror", None)
                        logger.error("At %d, OSError: %s, winerror=%s", cur_pos, e, winerror)
                        ret = False
                    except Exception:
                        logger.exception("At %d, Exception", cur_pos)
                        ret = False
                    else:
                        if not out:
                            break
                    old_pos = cur_pos

            p.print(f"Read {path.name}")

    if total_read != filesize:
        logger.warning("Only read a total of %d out of %s expected bytes", total_read, filesize)

    return ret


def main(args: Namespace) -> int:
    columns = [
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
    ]

    with RichProgress(*columns) as progress:
        p = Progress(progress)
        if args.path.is_dir():
            for entry in p.track(
                filter(
                    filter_recall(args.recall),
                    scandir_rec(
                        args.path, dirs=False, files=True, follow_symlinks=False, errorfunc=scandir_error_log_warning
                    ),
                )
            ):
                try:
                    read_path(Path(entry), args.chunk_size, p, buffering=args.buffering, use_mmap=args.mmap)
                except OSError as e:
                    if e.filename:
                        logger.error("Reading failed: %s", e)
                    else:
                        logger.error("Reading %s failed: %s", entry.path, e)
        else:
            read_path(args.path, args.chunk_size, p, buffering=args.buffering, use_mmap=args.mmap)

    return 0


def setup_logging(level: int = logging.NOTSET) -> None:
    if len(logger.handlers) != 0:
        logger.warning("Logger already has handlers set, skipping setup")

    log_filename = re.sub("[:-]", "", now().isoformat(timespec="seconds"))

    stream_fmt = "%(message)s"
    file_fmt = "%(asctime)s\t%(levelname)s\t%(message)s"

    stream_formatter = logging.Formatter(stream_fmt)
    file_formatter = IsoDatetimeFormatter(file_fmt, sep=" ", timespec="seconds", aslocal=True)

    stream_handler = RichHandler(log_time_format="%Y-%m-%d %H-%M-%S%Z", highlighter=NullHighlighter())
    file_handler = logging.FileHandler(f"raw-io-file-{log_filename}.log", encoding="utf-8", delay=True)

    stream_handler.setFormatter(stream_formatter)
    file_handler.setFormatter(file_formatter)

    logger.addHandler(stream_handler)
    logger.addHandler(file_handler)

    logger.setLevel(level)


if __name__ == "__main__":
    import sys

    DEFAULT_CHUNK_SIZE = 32 * 1024**2
    DEFAULT_BUFFERING = -1

    parser = ArgumentParser(formatter_class=ArgumentDefaultsHelpFormatter)
    parser.add_argument("--path", type=Path, required=True, help="Input file path")
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE, help="Chunk size for one read call")
    parser.add_argument(
        "--buffering",
        type=int,
        default=DEFAULT_BUFFERING,
        help="Use -1 for default buffering, 0 for unbuffered and larger values the buffer size",
    )
    parser.add_argument(
        "--mmap", action="store_true", help="Use memory-mapped file access. Python <3.13 can crash on broken files."
    )
    parser.add_argument(
        "--recall",
        action="store_true",
        help="Download files which are currently only available online (on OneDrive for example), otherwise they are skipped.",
    )
    parser.add_argument("-v", "--verbose", action="count", default=0)
    args = parser.parse_args()

    if args.verbose == 0:
        setup_logging(logging.WARNING)
    elif args.verbose == 1:
        setup_logging(logging.INFO)
    else:
        setup_logging(logging.DEBUG)

    try:
        sys.exit(main(args))
    except KeyboardInterrupt:
        logger.warning("Interrupted by user. Exiting.")
    except Exception:
        logger.exception("Reading file failed. Exiting.")
        sys.exit(1)
