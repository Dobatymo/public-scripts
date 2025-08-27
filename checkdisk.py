# /// script
# requires-python = ">=3.8"
# dependencies = [
#     "rich",
# ]
# ///
import logging
import os
import re
import subprocess  # nosec
import sys
from argparse import ArgumentParser, Namespace
from locale import getpreferredencoding
from pathlib import Path
from typing import List, NamedTuple

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm


class Line(NamedTuple):
    text: bytes
    end: bytes


class BinaryLineBuffer:
    cp = re.compile(rb"([^\r\n]*)(\r\n|\r|\n)")

    def __init__(self):
        # rewrite using deque[bytes]
        self.buffer = bytearray()

    def append(self, data: bytes) -> None:
        self.buffer.extend(data)

    def get(self, full=False) -> List[Line]:
        matches = list(self.cp.finditer(self.buffer))
        if not matches:
            return []

        if not full and matches[-1].end() == len(self.buffer) and matches[-1].group(2) == b"\r":
            # keep last match since a \n might still follow
            out = [Line._make(m.groups()) for m in matches[:-1]]
            if len(matches) >= 2:
                del self.buffer[: matches[-2].end()]
            return out
        else:
            out = [Line._make(m.groups()) for m in matches]
            if full and matches[-1].end() != len(self.buffer):
                out.append(Line(self.buffer[matches[-1].end() :], b""))
                self.buffer.clear()
            else:
                del self.buffer[: matches[-1].end()]
            return out


def _print_line(cout, cerr, textb: bytes, endb: bytes, encoding: str) -> None:
    assert b"\n" not in textb, textb
    text = textb.decode(encoding)
    end = endb.decode("ascii")
    if end == "\r":
        cerr.print(text, end="\r")
    else:  # \n or \r\n
        cout.print(text, end="\n")


def check_disks(drives: List[str]) -> None:
    cout = Console(soft_wrap=True, markup=False)
    cerr = Console(soft_wrap=True, stderr=True, markup=False)

    for i, drive in enumerate(drives, 1):
        if not re.match("^[a-zA-Z]:$", drive):
            msg = f"Invalid drive letter: {drive}"
            raise ValueError(msg)

        cout.print(Panel(drive))
        cmd = ["chkdsk", drive, "/V"]
        stderr = Path("chkdsk.stderr")
        encoding = getpreferredencoding()

        try:
            with stderr.open("ab") as fw:
                with subprocess.Popen(cmd, bufsize=0, stdout=subprocess.PIPE, stderr=fw) as proc:  # nosec
                    assert proc.stdout is not None  # for mypy
                    lb = BinaryLineBuffer()
                    while True:
                        chunk = proc.stdout.read(1024)
                        if not chunk:
                            for textb, endb in lb.get(full=True):
                                _print_line(cout, cerr, textb, endb, encoding)
                            break
                        lb.append(chunk)
                        for textb, endb in lb.get():
                            _print_line(cout, cerr, textb, endb, encoding)

            assert proc.returncode in (0, 3), proc.returncode
            if proc.returncode != 0:
                logging.error("Checking `%s` returned %s", drive, proc.returncode)

            assert not stderr.read_bytes()

        except Exception:
            logging.exception("Running chkdsk on %s failed", drive)

        if i < len(drives) and args.ask_continue:
            if not Confirm.ask("Continue with next drive?"):
                break


def main(args: Namespace) -> None:
    if args.select is None:
        drives = []

        if not hasattr(os, "listdrives"):  # Python 3.12+
            from genutility.win.device import get_logical_drives

            listdrives = get_logical_drives
        else:
            listdrives = os.listdrives

        for drive in listdrives():
            drive = drive.rstrip("\\")
            if drive not in args.ignore:
                drives.append(drive)
        check_disks(drives)

    else:
        check_disks(args.select)


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("--ask-continue", action="store_true", help="Ask to continue after every checked drive")
    parser.add_argument("--ignore", nargs="+", default=(), help="Drives not to scan, only used if --select is not")
    parser.add_argument(
        "--select", nargs="+", help="Select drive letters to scan, eg. `C: D:`. If not used all drives are scanned."
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    try:
        main(args)
    except KeyboardInterrupt:
        print("", file=sys.stderr)
        logging.warning("Interrupted.")
