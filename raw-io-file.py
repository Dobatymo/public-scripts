import contextlib
import logging
import mmap
import os
import stat
from pathlib import Path

from genutility.rich import Progress
from rich.logging import RichHandler
from rich.progress import Progress as RichProgress

"""
from cwinsdk.um.ntsecapi import LsaNtStatusToWinError
from cwinsdk.shared.ntstatus import STATUS_FILE_CORRUPT_ERROR
from cwinsdk.shared.winerror import ERROR_FILE_CORRUPT
LsaNtStatusToWinError(STATUS_FILE_CORRUPT_ERROR) == ERROR_FILE_CORRUPT  # 1392
"""


def read(path, chunk_size: int, *, buffering: int = -1, use_mmap: bool = False) -> None:
    logging.info("Reading %r with chunk_size=%d, buffering=%s, mmap=%s", path, chunk_size, buffering, use_mmap)

    stats = path.stat()
    filesize = stats.st_size
    is_block_device = stat.S_ISBLK(stats.st_mode)

    if filesize == 0 and is_block_device:
        logging.warning("Could not read size for block device")
        # fr.seek(0, os.SEEK_END) doesn't work...

    with open(path, "rb", buffering=buffering) as fp, RichProgress() as progress:
        if use_mmap:
            context = mmap.mmap(fp.fileno(), 0, access=mmap.ACCESS_READ)
        else:
            context = contextlib.nullcontext(fp)

        with context as fr:
            old_pos = None
            p = Progress(progress)
            with p.task(total=filesize) as task:
                while True:
                    cur_pos = fr.tell()
                    task.update(completed=cur_pos)
                    if old_pos is not None:
                        delta = cur_pos - old_pos
                        if delta == 0:
                            logging.warning("At %d, read did not advance, skip", cur_pos)
                            fr.seek(chunk_size, os.SEEK_CUR)
                        elif delta == chunk_size:
                            pass
                        else:
                            logging.warning("At %d, only read %d bytes", cur_pos, delta)
                    try:
                        out = fr.read(chunk_size)
                    except OSError as e:
                        winerror = getattr(e, "winerror", None)
                        logging.error("At %d, OSError: %s, winerror=%s", cur_pos, e, winerror)
                    except Exception:
                        logging.exception("At %d, Exception", cur_pos)
                    else:
                        if not out:
                            break
                    old_pos = cur_pos


if __name__ == "__main__":
    from argparse import ArgumentDefaultsHelpFormatter, ArgumentParser

    DEFAULT_PATH = Path()
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
    args = parser.parse_args()

    handler = RichHandler(log_time_format="%Y-%m-%d %H-%M-%S%Z")
    FORMAT = "%(message)s"
    logging.basicConfig(level=logging.INFO, format=FORMAT, handlers=[handler])

    read(args.path, args.chunk_size, buffering=args.buffering, use_mmap=args.mmap)
