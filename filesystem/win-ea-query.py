import logging
import os
from argparse import ArgumentParser, Namespace
from collections import Counter
from ctypes import WinError, byref, create_string_buffer, sizeof
from struct import unpack
from typing import List, NamedTuple

from cwinsdk.km.wdm import IO_STATUS_BLOCK
from cwinsdk.ntdll import NtQueryEaFile
from cwinsdk.shared.ntstatus import STATUS_NO_EAS_ON_FILE
from cwinsdk.um.fileapi import OPEN_EXISTING, CreateFileW
from cwinsdk.um.handleapi import CloseHandle
from cwinsdk.um.winnt import FILE_GENERIC_READ
from cwinsdk.um.winternl import RtlNtStatusToDosError
from genutility.args import is_file_or_dir
from genutility.filesystem import scandir_rec
from genutility.rich import MarkdownHighlighter, Progress
from rich.logging import RichHandler
from rich.progress import Progress as RichProgress
from typing_extensions import Buffer


class EA(NamedTuple):
    flags: int
    name: str
    value: bytes


def parse_buffer(buffer: Buffer) -> List[EA]:
    out: List[EA] = []
    offset = 0
    while True:
        NextEntryOffset, Flags, EaNameLength, EaValueLength = unpack("LBBH", buffer[offset : offset + 8])
        EaName = buffer[offset + 8 : offset + 8 + EaNameLength]
        EaValue = buffer[offset + 8 + EaNameLength + 1 : offset + 8 + EaNameLength + 1 + EaValueLength]
        out.append(EA(Flags, str(EaName, "ascii"), bytes(EaValue)))
        if NextEntryOffset == 0:
            break
        offset += NextEntryOffset
    return out


DEFAULT_BUFFER_SIZE = 2**16


def read_ea(path: str, buffer_size: int = DEFAULT_BUFFER_SIZE) -> List[EA]:
    # https://learn.microsoft.com/en-us/windows/win32/api/winternl/nf-winternl-ntopenfile
    # https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/ntifs/nf-ntifs-zwqueryeafile

    # http://undocumented.ntinternals.net/index.html?page=UserMode%2FUndocumented%20Functions%2FNT%20Objects%2FFile%2FNtQueryEaFile.html
    # https://www.powershellgallery.com/packages/PSReflect-Functions/1.1/Content/ntdll%5CNtQueryEaFile.ps1
    # https://stackoverflow.com/questions/27326109/pinvoke-ntopenfile-and-ntqueryeafile-in-order-to-read-ntfs-extended-attributes-i

    FileHandle = CreateFileW(path, FILE_GENERIC_READ, 0, None, OPEN_EXISTING, 0, None)
    try:
        IoStatusBlock = IO_STATUS_BLOCK()
        Buffer = create_string_buffer(buffer_size)
        NtQueryEaFile(FileHandle, byref(IoStatusBlock), Buffer, sizeof(Buffer), False, None, 0, None, True)
    except PermissionError:
        raise
    except OSError as e:
        if e.winerror == STATUS_NO_EAS_ON_FILE:
            return []
        else:
            code = RtlNtStatusToDosError(e.winerror)
            raise WinError(code) from None
    finally:
        CloseHandle(FileHandle)

    return parse_buffer(memoryview(Buffer))


def main(args: Namespace) -> int:
    handler = RichHandler(log_time_format="%Y-%m-%d %H-%M-%S%Z", highlighter=MarkdownHighlighter())
    FORMAT = "%(message)s"

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG, format=FORMAT, handlers=[handler])
    else:
        logging.basicConfig(level=logging.INFO, format=FORMAT, handlers=[handler])

    if args.path.is_dir():
        c = Counter()

        with RichProgress() as progress:
            p = Progress(progress)
            for entry in p.track(scandir_rec(args.path, rec=args.recursive, follow_symlinks=args.follow_links)):
                path = entry.path
                try:
                    ealist = read_ea(path)
                except OSError as e:
                    logging.error("Failed to read EA from <%s>: %s", path, e)
                    continue
                except Exception:
                    logging.exception("Failed to read EA from <%s>", path)
                    return 1

                if ealist:
                    c.update([ea.name for ea in ealist])
                    p.print(f"<{path}> {ealist}")

        print(dict(c))

    elif args.path.is_file():
        try:
            ealist = read_ea(os.fspath(args.path))
        except OSError as e:
            logging.error("Failed to read EA from <%s>: %s", args.path, e)
            return 2
        except Exception:
            logging.exception("Failed to read EA from <%s>", args.path)
            return 1
        if ealist:
            print(f"<{args.path}> {ealist}")
    else:
        raise ValueError("Invalid input path type")

    return 0


if __name__ == "__main__":
    import sys

    parser = ArgumentParser()
    parser.add_argument(
        "path", type=is_file_or_dir, help="Input file or directory to scan for Extended Attributes (EA)"
    )
    parser.add_argument("-r", "--recursive", action="store_true", help="Scan recursively")
    parser.add_argument("--follow-links", action="store_true", help="Follow file and directory links during scanning")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    args = parser.parse_args()

    if args.recursive and not args.path.is_dir():
        parser.error("Can only use --recursive with directory paths")

    sys.exit(main(args))
