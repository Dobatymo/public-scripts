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
from genutility.filesystem import scandir_error_log_warning, scandir_rec
from genutility.logging import IsoDatetimeFormatter
from genutility.rich import Progress
from rich.logging import RichHandler
from rich.progress import Progress as RichProgress

"""
from cwinsdk.um.ntsecapi import LsaNtStatusToWinError
from cwinsdk.shared.ntstatus import STATUS_FILE_CORRUPT_ERROR
from cwinsdk.shared.winerror import ERROR_FILE_CORRUPT
LsaNtStatusToWinError(STATUS_FILE_CORRUPT_ERROR) == ERROR_FILE_CORRUPT  # 1392
"""

logger = logging.getLogger(None)


def read(path, chunk_size: int, *, buffering: int = -1, use_mmap: bool = False) -> None:
    logger.info("Reading %r with chunk_size=%d, buffering=%s, mmap=%s", path, chunk_size, buffering, use_mmap)

    stats = path.stat()
    filesize = stats.st_size
    is_block_device = stat.S_ISBLK(stats.st_mode)

    if filesize == 0 and is_block_device:
        logger.warning("Could not read size for block device")
        # fr.seek(0, os.SEEK_END) doesn't work...

    with open(path, "rb", buffering=buffering) as fp, RichProgress() as progress:
        if use_mmap:
            context = mmap.mmap(fp.fileno(), 0, access=mmap.ACCESS_READ)
        else:
            context = contextlib.nullcontext(fp)

        with context as fr:
            old_pos: Optional[int] = None
            p = Progress(progress)
            with p.task(total=filesize) as task:
                while True:
                    cur_pos = fr.tell()
                    task.update(completed=cur_pos)
                    if old_pos is not None:
                        delta = cur_pos - old_pos
                        if delta == 0:
                            logger.warning("At %d, read did not advance, skip", cur_pos)
                            fr.seek(chunk_size, os.SEEK_CUR)
                        elif delta == chunk_size:
                            pass
                        else:
                            logger.debug("At %d, only read %d bytes", cur_pos, delta)  # seems to be common behaviour
                    try:
                        out = fr.read(chunk_size)
                    except OSError as e:
                        winerror = getattr(e, "winerror", None)
                        logger.error("At %d, OSError: %s, winerror=%s", cur_pos, e, winerror)
                    except Exception:
                        logger.exception("At %d, Exception", cur_pos)
                    else:
                        if not out:
                            break
                    old_pos = cur_pos


def main(args: Namespace) -> None:
    if args.path.is_dir():
        for entry in scandir_rec(
            args.path, dirs=False, files=True, follow_symlinks=False, errorfunc=scandir_error_log_warning
        ):
            read(Path(entry), args.chunk_size, buffering=args.buffering, use_mmap=args.mmap)
    else:
        read(args.path, args.chunk_size, buffering=args.buffering, use_mmap=args.mmap)


def setup_logging(level: int = logging.NOTSET) -> None:
    if len(logger.handlers) != 0:
        logger.warning("Logger already has handlers set, skipping setup")

    log_filename = re.sub("[:-]", "", now().isoformat(timespec="seconds"))

    stream_fmt = "%(message)s"
    file_fmt = "%(asctime)s\t%(levelname)s\t%(message)s"

    stream_formatter = logging.Formatter(stream_fmt)
    file_formatter = IsoDatetimeFormatter(file_fmt, sep=" ", timespec="seconds", aslocal=True)

    stream_handler = RichHandler(log_time_format="%Y-%m-%d %H-%M-%S%Z")
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
        "--mmap", action="store_true", help="Use memory-mapped file access. Requires Python 3.13+ to work correctly."
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
        main(args)
    except KeyboardInterrupt:
        logger.warning("Interrupted by user. Exiting.")
    except Exception:
        logger.exception("Reading file failed. Exiting.")
        sys.exit(1)
