# /// script
# requires-python = ">=3.8"
# dependencies = [
#     "ctypes-windows-sdk>=0.0.16",
#     "genutility[args,file,iter]",
# ]
# ///
import errno
import logging
import os
from argparse import ArgumentDefaultsHelpFormatter, ArgumentParser, RawTextHelpFormatter
from io import SEEK_CUR, BufferedIOBase
from shutil import disk_usage
from textwrap import dedent
from typing import BinaryIO, Iterator, Optional

from cwinsdk.shared.winerror import ERROR_INVALID_PARAMETER
from genutility.args import multiple_of
from genutility.file import FILE_IO_BUFFER_SIZE, OptionalWriteOnlyFile
from genutility.iter import progressdata
from genutility.win.device import Drive, Volume

DEFAULT_SECTOR_SIZE = 512


def save_seek_cur(fr: BinaryIO, pos: int, sector_size: int) -> None:
    fr_pos = fr.tell()

    if fr_pos == pos:
        fr.seek(sector_size, SEEK_CUR)
    elif fr_pos == pos + sector_size:
        pass
    else:
        raise RuntimeError(f"File pointer at unexpected position: {fr_pos}")


class Retries:
    def __init__(self, total_retries, sector_retries):
        self.total_retries = total_retries
        self.remaining_total_retries = total_retries
        self.sector_retries = sector_retries
        self.remaining_sector_retries = sector_retries


class PermanentOSError(OSError):
    pass


class OutOfBounds(OSError):
    pass


def readblocklimited(fr: BinaryIO, chunk_size: int, sector_size: int, retries: Retries) -> Iterator[bytes]:
    """Read `chunk_size` from `fr` in blocks of `sector_size`."""

    if chunk_size % sector_size != 0:
        raise ValueError("chunk_size must be a multiple of sector_size")

    while chunk_size > 0:
        pos = fr.tell()
        try:
            data = fr.read(sector_size)
        except OSError as e:  # OSError on python 2, PermissionError on python 3
            if e.errno == errno.EINVAL:
                raise OutOfBounds(f"Out of bounds at {pos} (sector {pos // sector_size})") from e

            """
            windows:
                failed reading attempt does not advance file pointer ? so we need to seek (or try again?)
                - yes: according to rawcopy
            linux:
                On error [...] it is left unspecified whether the file position [...] changes
                (http://man7.org/linux/man-pages/man2/read.2.html)
                so tell() is mandatory
            """
            if retries.remaining_total_retries > 0:
                if retries.remaining_sector_retries > 0:  # try again
                    logging.warning(
                        "Retrying %i bytes at %i (sector %i) (%i tries left)",
                        sector_size,
                        pos,
                        pos // sector_size,
                        retries.remaining_sector_retries,
                    )
                    if fr.tell() != pos:
                        fr.seek(pos)
                    retries.remaining_sector_retries -= 1
                    retries.remaining_total_retries -= 1
                    continue
                else:  # move to next sector
                    logging.warning("Skipping %i bytes at %i (sector %i)", sector_size, pos, pos // sector_size)
                    yield b"\0" * sector_size  # empty sector
                    save_seek_cur(fr, pos, sector_size)
            else:
                raise PermanentOSError(
                    f"Cannot read pos {pos} (sector {pos // sector_size}) (exceeded {retries.total_retries} tries)"
                ) from None
        else:
            if data:
                assert len(data) == sector_size
                yield data
            else:
                break
        retries.remaining_sector_retries = retries.sector_retries
        chunk_size -= sector_size


def _blockfileiterignore(
    fr: BinaryIO, total_size: int, sector_size: int = DEFAULT_SECTOR_SIZE, chunk_size: int = FILE_IO_BUFFER_SIZE
) -> Iterator[bytes]:
    if chunk_size % sector_size != 0:
        raise ValueError("chunk_size must be a multiple of sector_size")

    is_buffered = isinstance(fr, BufferedIOBase)

    retries = Retries(total_retries=3, sector_retries=3)

    while True:
        pos = fr.tell()
        try:
            data = fr.read(chunk_size)
        except OSError as e:  # OSError on python 2, PermissionError on python 3
            if e.errno == errno.EINVAL:
                pass  # probably just trying to read out of bounds data
            else:
                logging.exception(
                    "Error in sectors %i-%i: %s",
                    pos // sector_size,
                    (pos + chunk_size) // sector_size,
                    os.strerror(e.errno),
                )

            if fr.tell() != pos:
                fr.seek(pos)

            logging.info("Trying to read sector by sector")
            try:
                for data in readblocklimited(fr, chunk_size, sector_size, retries):
                    yield data
            except OutOfBounds as e:
                logging.warning(str(e))
                return
            except PermanentOSError as e:
                logging.warning(str(e))
                raise

        else:
            if data:
                if is_buffered and len(data) != chunk_size and fr.tell() != total_size:
                    logging.warning("Read size %i differs from expected size %i", len(data), chunk_size)
                yield data
            else:
                break


def blockfileiterignore(
    volume: str, seek: Optional[int] = None, extended: bool = False, chunk_size: int = FILE_IO_BUFFER_SIZE
) -> Iterator[bytes]:
    if extended:
        """When trying to read past usual volume boundaries, the requested size cannot be larger
        than the actual partition size. But using buffered IO, Python might request too much
        and trigger an error. So buffering needs to be disabled in that case.
        """
        buffering = 0
    else:
        buffering = -1

    fr = open(volume, "rb", buffering=buffering)

    try:
        with Drive.from_file(fr) as d:
            sector_size = d.sqp_alignment()["BytesPerPhysicalSector"]
    except OSError:
        sector_size = DEFAULT_SECTOR_SIZE
        logging.warning("Could not read physical sector size. Using default size %d", sector_size)

    # fr.seek(0, SEEK_END) # doesn't work...
    try:
        total_size = disk_usage(volume[4:]).total
    except FileNotFoundError:
        total_size = None

    if extended:  # only useful on volume handles, not drive handles
        try:
            with Volume.from_file(fr) as v:
                v.extend()
            fr.seek(total_size)
        except OSError as e:
            if e.winerror == ERROR_INVALID_PARAMETER:
                raise ValueError("Can only read extended data on volumes not drives") from e
            else:
                raise

    try:
        if seek:
            fr.seek(seek, SEEK_CUR)  # relative seek

        yield from _blockfileiterignore(fr, total_size, sector_size, chunk_size)

    finally:
        fr.close()


class HelpFormatter(RawTextHelpFormatter, ArgumentDefaultsHelpFormatter):
    pass


def main():
    DEFAULT_BLOCKSIZE = 512 * 2048 * 16

    parser = ArgumentParser(
        description="Read raw volume, disk or device to verify the contents or create an image.",
        formatter_class=HelpFormatter,
    )
    parser.add_argument("volume", type=str, help=dedent(Volume.from_raw_path.__doc__))
    group_seek = parser.add_mutually_exclusive_group(required=False)
    group_seek.add_argument(
        "--seek-byte", type=multiple_of(DEFAULT_SECTOR_SIZE), default=None, help="Seek to byte number"
    )
    group_seek.add_argument("--seek-sector", type=int, default=None, help="Seek to sector number")
    parser.add_argument(
        "--out",
        type=str,
        default=None,
        help="Create a image file of the volume or disk content. Use .gz or .bz2 file extension to store it compressed.",
    )
    parser.add_argument("--bs", type=int, default=DEFAULT_BLOCKSIZE, help="Read blocksize")
    parser.add_argument(
        "--compresslevel", type=int, default=9, help="Compression level. Only valid for gzip and bz2 files."
    )
    parser.add_argument(
        "--extended",
        action="store_true",
        help="Read the data between the end of the volume and the end of the partition. Can be used for volumes, not drives. Only supported on Windows.",
    )
    args = parser.parse_args()

    print(f"Reading {args.volume} to {args.out}")

    if args.seek_byte:
        seek = args.seek_byte
    elif args.seek_sector:
        seek = args.seek_sector * DEFAULT_SECTOR_SIZE
    else:
        seek = None

    try:
        with OptionalWriteOnlyFile(args.out, compresslevel=args.compresslevel) as fw:
            for data in progressdata(
                blockfileiterignore(args.volume, seek=seek, extended=args.extended, chunk_size=args.bs)
            ):
                fw.write(data)
    except PermissionError:
        print("Administrator privileges needed!")


if __name__ == "__main__":
    main()
