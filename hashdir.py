import hashlib
import logging
import os.path
import re
import sys
from argparse import ArgumentDefaultsHelpFormatter, ArgumentParser, Namespace
from os import fspath
from pathlib import Path
from typing import IO, Iterable, Iterator, List, Optional, Sequence, Type, TypedDict

from genutility.callbacks import Progress as NullProgress
from genutility.file import StdoutFile
from genutility.filesystem import PathType, scandir_error_log_warning, scandir_rec
from genutility.hash import HashCls, Hashobj, HashobjCRC, hash_file
from genutility.iter import CachedIterable
from genutility.rich import MarkdownHighlighter, Progress, StdoutFileNoStyle
from rich.logging import RichHandler
from rich.progress import BarColumn, MofNCompleteColumn
from rich.progress import Progress as RichProgress
from rich.progress import TextColumn, TimeElapsedColumn
from typing_extensions import NotRequired, Self

logger = logging.getLogger(__name__)


class Meta(TypedDict):
    hash: Optional[bytes]
    size: NotRequired[int]


def get_hash_name(hashcls: HashCls) -> str:
    if isinstance(hashcls, str):
        return hashcls
    elif callable(hashcls):
        return hashcls.__name__
    else:
        raise TypeError(type(hashcls))


def _metas(paths: List[str], hashcls: HashCls, dirpath: PathType) -> Iterator[Meta]:
    for path in paths:
        abspath = os.path.join(dirpath, path)
        try:
            size = os.path.getsize(abspath)
        except FileNotFoundError:
            logger.warning("File disappeared: `%s`", path)
            continue
        except OSError as e:
            if e.filename:
                logger.error("Failed to read size: %s", e)
            else:
                logger.error("Failed to read size of `%s`: %s", path, e)
            continue

        try:
            hashbytes = hash_file(abspath, hashcls).digest()
        except OSError as e:
            if e.filename:
                logger.error("Failed to hash file: %s", e)
            else:
                logger.error("Failed to hash `%s`: %s", path, e)
            hashbytes = None
        yield {"hash": hashbytes, "size": size}


def _paths(dirpath: PathType, progress: Optional[NullProgress] = None) -> List[str]:
    def sortkey(relpath: str) -> List[str]:
        return relpath.split("/")

    it = scandir_rec(dirpath, files=True, dirs=False, relative=True, errorfunc=scandir_error_log_warning)

    if os.name == "nt":
        normit = (entry.relpath.replace("\\", "/") for entry in it)
    else:
        normit = (entry.relpath for entry in it)

    if progress is None:
        return sorted(normit, key=sortkey)
    else:
        return sorted(progress.track(normit), key=sortkey)


class Formatter:
    def __init__(self, stream: IO[str]) -> None:
        self.stream = stream

    def header(self, hashname: str) -> None:
        pass

    def format_line(self, hexdigest: str, path: str, size: int) -> None:
        raise NotImplementedError

    def format_total(self, hexdigest: str, path: str, size: int) -> None:
        self.format_line(hexdigest, path, size)


class Hashsum(Formatter):
    def format_line(self, hexdigest: str, path: str, size: int) -> None:
        self.stream.write(f"{hexdigest} *{path}\n")


class Hashdeep(Formatter):
    def header(self, hashname: str) -> None:
        self.stream.write("%%%% HASHDEEP-1.0\n")
        self.stream.write(f"%%%% size,{hashname},filename\n")

    def format_line(self, hexdigest: str, path: str, size: int) -> None:
        self.stream.write(f"{size},{hexdigest},{path}\n")

    def format_total(self, hexdigest: str, path: str, size: int) -> None:
        self.stream.write(f"% {size},{hexdigest},{path}\n")


class SFV(Formatter):
    def format_line(self, hexdigest: str, path: str, size: int) -> None:
        self.stream.write(f"{path} {hexdigest}\n")


class DirHasher:
    formatter = {
        "hashsum": Hashsum,
        "hashdeep": Hashdeep,
        "sfv": SFV,
    }

    def __init__(
        self,
        paths: Sequence[str],
        metas: Iterable[Meta],
        hashname: str,
        toppath: Optional[str] = None,
        progress: Optional[NullProgress] = None,
    ) -> None:
        self.paths = paths
        self.metas = metas
        self.hashname = hashname
        self._toppath = toppath
        self.progress = progress or NullProgress()

    @classmethod
    def get_formatter_class(cls, fformat: str) -> Type[Formatter]:
        try:
            return cls.formatter[fformat]
        except KeyError:
            raise ValueError(f"Invalid fformat: {fformat}")

    @property
    def toppath(self) -> str:
        if self._toppath is None:
            self._toppath = os.path.commonprefix(self.paths)

        return self._toppath

    @classmethod
    def from_fs(
        cls, dirpath: PathType, hashcls: HashCls = hashlib.sha1, progress: Optional[NullProgress] = None
    ) -> Self:
        """Creates a DirHasher instance for a filesystem folder."""

        paths = _paths(dirpath, progress)
        metas = _metas(paths, hashcls, dirpath)

        return cls(paths, CachedIterable(metas), get_hash_name(hashcls), fspath(dirpath), progress)

    @classmethod
    def from_file(cls, filepath: Path) -> Self:
        """Reads a file in md5sum or sha1sum format and creates a DirHasher instance."""

        metas: List[Meta] = []
        paths: List[str] = []

        if filepath.suffix == ".md5":
            hashname = "md5"
        elif filepath.suffix == ".sha1":
            hashname = "sha1"
        elif filepath.suffix == ".sha256":
            hashname = "sha256"
        else:
            raise ValueError(f"Unsupported hash file: {filepath.suffix}")

        with open(filepath, encoding="utf-8") as fr:
            for i, line in enumerate(fr, 1):
                m = re.match(r"^([0-9a-fA-F]+) [ \*](.*)$", line.rstrip())
                if not m:
                    msg = f"Invalid file (error in line {i}"
                    raise ValueError(msg)

                hashhex, path = m.groups()
                hashbytes = bytes(bytearray.fromhex(hashhex))
                metas.append({"hash": hashbytes})
                paths.append(path)

        return cls(paths, metas, hashname, None)

    def to_stream(
        self,
        stream: IO[str],
        include_total: bool = True,
        fformat: str = "hashsum",
        hashcls: HashCls = hashlib.sha1,
        include_names: bool = True,
    ) -> None:
        # see https://github.com/jessek/hashdeep/blob/master/FILEFORMAT

        formatter = self.get_formatter_class(fformat)(stream)
        formatter.header(self.hashname)

        for meta, path in self.progress.track(zip(self.metas, self.paths), total=len(self.paths)):
            if meta["hash"] is None:  # reading file might have failed during hash calculation
                continue

            formatter.format_line(meta["hash"].hex(), path, meta["size"])

        if include_total:
            self.total_line(formatter, hashcls, include_names)

    def total_line(
        self,
        formatter: Formatter,
        hashcls: HashCls = hashlib.sha1,
        include_names: bool = True,
    ) -> None:
        total_hexdigest = self.total(hashcls, include_names).hexdigest()
        total_size = sum(meta["size"] for meta in self.metas)
        formatter.format_total(total_hexdigest, self.toppath, total_size)

    def total(self, hashcls: HashCls = hashlib.sha1, include_names: bool = True) -> Hashobj:
        if isinstance(hashcls, str):
            m = hashlib.new(hashcls)
        else:
            m = hashcls()

        for meta, path in zip(self.metas, self.paths):
            if meta["hash"] is None:
                continue

            if include_names:
                m.update(os.path.basename(path).encode("utf-8"))
            m.update(meta["hash"])

        return m


def main(args: Namespace) -> int:
    handler = RichHandler(log_time_format="%Y-%m-%d %H-%M-%S%Z", highlighter=MarkdownHighlighter())
    FORMAT = "%(message)s"

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG, format=FORMAT, handlers=[handler])
    else:
        logging.basicConfig(level=logging.INFO, format=FORMAT, handlers=[handler])

    if args.format == "sfv" and args.algorithm != "crc32":
        parser.error("SFV format only supports crc32")

    if args.format == "hashdeep" and args.algorithm not in HASHDEEP_ALGOS:
        parser.error(f"hashdeep format only supports: {HASHDEEP_ALGOS}")

    if args.algorithm == "crc32":
        algorithm: HashCls = HashobjCRC
    else:
        algorithm = args.algorithm

    if args.input == "fs":
        if not args.path.is_dir():
            parser.error("path has to be a directory")

        if args.progress:
            columns = [
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                MofNCompleteColumn(),
                TimeElapsedColumn(),
            ]
            progressctx = RichProgress(*columns)
        else:
            from contextlib import nullcontext

            progressctx = nullcontext()

        with progressctx as p:
            if p is not None:
                progress = Progress(p)
                writer = StdoutFileNoStyle(progress.progress.console, args.out, "xt", encoding="utf-8")
            else:
                progress = None
                writer = StdoutFile(args.out, "xt", encoding="utf-8")

            with writer as fw:
                hasher = DirHasher.from_fs(args.path, algorithm, progress)
                hasher.to_stream(fw, include_total=True, fformat=args.format, include_names=not args.no_names)

    elif args.input == "file":
        if not args.path.is_file():
            parser.error("path has to be a file")

        hasher = DirHasher.from_file(args.path)
        formatter = DirHasher.get_formatter_class("hashsum")(sys.stdout)
        hasher.total_line(formatter, include_names=not args.no_names)

    return 0


if __name__ == "__main__":
    ALGORITHMS = sorted(hashlib.algorithms_available) + ["crc32"]
    HASHDEEP_ALGOS = sorted({"md5", "sha1", "sha256", "whirlpool", "tiger"} & set(ALGORITHMS))
    DEFAULT_ALGO = "sha1"

    parser = ArgumentParser(
        description="calculate hash of all files in directory combined", formatter_class=ArgumentDefaultsHelpFormatter
    )
    parser.add_argument("path", type=Path, help="input file or directory")
    parser.add_argument(
        "--input",
        choices=("fs", "file"),
        default="fs",
        help="fs: create file with hashes from directory path. file: read file with hashes and print summary hash of hashes.",
    )
    parser.add_argument("-o", "--out", help="Optional out file. If not specified it's printed to stdout.")
    parser.add_argument("-p", "--progress", action="store_true", help="Show progress bar")
    parser.add_argument(
        "--format",
        choices=("hashdeep", "sfv", "hashsum"),
        default="hashsum",
        help="Output format. SFV: Simple file verification",
    )
    parser.add_argument(
        "--algorithm", choices=ALGORITHMS, default=DEFAULT_ALGO, help="Hashing algorithm for file contents."
    )
    parser.add_argument("--no-names", action="store_true", help="Don't include filenames in summary hash calculation")
    parser.add_argument("-v", "--verbose", action="store_true", help="Show debug output")
    args = parser.parse_args()

    sys.exit(main(args))
