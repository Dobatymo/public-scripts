import logging
from collections import defaultdict
from hashlib import sha1
from pathlib import Path
from typing import Callable, Collection, Dict, Iterable, Iterator, List, Mapping, Optional, Tuple

from genutility.exceptions import ParseError, Skip
from genutility.fileformats.jfif import hash_raw_jpeg
from genutility.fileformats.png import hash_raw_png
from genutility.filesystem import PathType, entrysuffix, fileextensions, scandir_rec
from genutility.fingerprinting import phash_blockmean
from genutility.hash import hash_file
from genutility.metrics import hamming_distance
from genutility.rich import Progress
from metrictrees.bktree import BKTree
from metrohash import MetroHash128
from PIL import Image
from rich.highlighter import NullHighlighter
from rich.logging import RichHandler
from rich.progress import Progress as RichProgress


def metrohash(path: str) -> bytes:
    return hash_file(path, MetroHash128).digest()


def nometahash(path: str) -> bytes:
    lowerpath = path.lower()

    if lowerpath.endswith(".jpg") or lowerpath.endswith(".jpeg"):
        hashobj = sha1()
        try:
            hash_raw_jpeg(path, hashobj)
        except ParseError as e:
            logging.info("Skipping invalid JPEG file %s: %s", path, e)
            raise Skip()
        except EOFError:
            logging.info("Skipping truncated JPEG file %s", path)
            raise Skip()

        return hashobj.digest()

    elif lowerpath.endswith(".png"):
        hashobj = sha1()
        try:
            hash_raw_png(path, hashobj)
        except ParseError as e:
            logging.info("Skipping invalid PNG file %s: %s", path, e)
            raise Skip()
        except EOFError:
            logging.info("Skipping truncated PNG file %s", path)
            raise Skip()

        return hashobj.digest()

    else:
        raise Skip()


IGNORE_DIRNAMES = {".git"}


def iter_size_path(dirs: Iterable[PathType], include_symlinks: bool = False) -> Iterator[Tuple[int, str]]:
    for dir in dirs:
        for entry in scandir_rec(dir, dirs=True, others=True, follow_symlinks=False, allow_skip=True):
            if entry.is_dir() and entry.name in IGNORE_DIRNAMES:
                logging.debug("Skipped: %s", entry.path)
                entry.follow = False
            elif entry.is_symlink():
                if include_symlinks:
                    filesize = entry.stat().st_size
                    yield filesize, entry.path
            elif entry.is_file():
                filesize = entry.stat().st_size
                yield filesize, entry.path


def image_hash_tree(
    dirs: Iterable[PathType], exts: Optional[Collection] = None, progressfunc=None
) -> Tuple[BKTree, Dict[bytes, List[str]]]:
    tree = BKTree(hamming_distance)
    map = defaultdict(list)
    exts = exts or fileextensions.images

    for dir in dirs:
        for entry in scandir_rec(dir, dirs=True, follow_symlinks=False, allow_skip=True):
            if entry.is_dir() and entry.name in IGNORE_DIRNAMES:
                logging.debug("Skipped: %s", entry.path)
                entry.follow = False
            elif entry.is_file():
                ext = entrysuffix(entry)[1:].lower()

                if ext in exts:
                    try:
                        img = Image.open(entry.path, "r")
                        img.load()  # force load so OSError can be caught here
                    except OSError:
                        logging.warning("Cannot open image: %s", entry.path)
                    else:
                        hash = phash_blockmean(img)
                        tree.add(hash)
                        map[hash].append(entry.path)
                    if progressfunc:
                        progressfunc((entry.path, hash))

    return tree, map


def iter_size_hashes(
    dups: Mapping[int, List[str]], hashfunc: Callable[[str], bytes]
) -> Iterator[Tuple[int, Dict[bytes, List[str]]]]:
    for size, group in dups.items():
        hashes = defaultdict(list)

        for path in group:
            try:
                hashbytes = hashfunc(path)
            except PermissionError:
                logging.warning("Permission denied: %s", path)
            except FileNotFoundError:
                logging.warning("File not found: %s", path)
            else:
                hashes[hashbytes].append(path)

        yield size, hashes


def write_dupegroups_dupeguru(outpath: Path, groups: Dict[Tuple[int, bytes], List[str]]):
    from itertools import combinations
    from xml.etree import ElementTree as ET

    root = ET.Element("results")

    for (size, hashbytes), paths in groups.items():
        group = ET.SubElement(root, "group")

        for filepath in paths:
            ET.SubElement(group, "file", path=filepath, words="", is_ref="n", marked="n")

        for i, j in combinations(range(len(paths)), 2):
            ET.SubElement(group, "match", first=str(i), second=str(j), percentage="100")

    tree = ET.ElementTree(root)
    tree.write(outpath, encoding="utf-8")


def dupegroups(
    dirs: Iterable[PathType], hashfunc: Callable[[str], bytes], progress: Progress, include_symlinks: bool
) -> Dict[Tuple[int, bytes], List[str]]:
    dups = defaultdict(list)

    total = 0
    for size, path in progress.track(iter_size_path(dirs, include_symlinks), description="Collecting files..."):
        dups[size].append(path)
        total += 1
    logging.info("Found %s size groups in %d files", len(dups), total)

    logging.info("Filtering files based on size")
    dups = {k: v for k, v in dups.items() if len(v) > 1}
    logging.info("Found %s duplicate size groups", len(dups))

    logging.info("Calculating hash groups")
    for size, hashes in progress.track(iter_size_hashes(dups, hashfunc), total=len(dups)):
        dups[size] = hashes

    logging.info("Filtering files based on hashes")
    newdups: Dict[Tuple[int, bytes], List[str]] = {}
    for size, sizegroup in dups.items():
        for hashbytes, hashgroup in sizegroup.items():
            if len(hashgroup) > 1:
                newdups[(size, hashbytes)] = hashgroup

    return newdups


def dupegroups_no_size(
    dirs: Iterable[PathType], hashfunc: Callable[[str], bytes], progress: Progress, include_symlinks: bool = False
) -> Dict[bytes, List[Tuple[str, int]]]:
    dups = defaultdict(list)

    total = 0
    for size, path in progress.track(iter_size_path(dirs, include_symlinks), description="Calculating hash groups..."):
        total += 1
        try:
            hash = hashfunc(path)
        except PermissionError:
            logging.warning("Permission denied: %s", path)
        except FileNotFoundError:
            logging.warning("File not found: %s", path)
        except Skip:
            pass
        else:
            dups[hash].append((path, size))

    logging.info("Found %s hash groupsin %d files", len(dups), total)

    logging.info("Filtering files based on hash")
    dups = {k: v for k, v in dups.items() if len(v) > 1}
    logging.info("Found %s duplicate hash groups", len(dups))

    return dups


if __name__ == "__main__":
    import csv
    from argparse import ArgumentParser

    from genutility.args import is_dir
    from genutility.file import StdoutFile

    hashfuncs = {"metrohash": metrohash, "no-meta-sha1": nometahash}

    parser = ArgumentParser()
    parser.add_argument("directories", type=is_dir, nargs="+", help="Directory to search")
    parser.add_argument("-o", "--out", help="Optional output file path. If not given it will be printed to stdout.")
    parser.add_argument("-v", "--verbose", action="store_true", help="Print debug information")
    parser.add_argument(
        "--no-size", action="store_true", help="Hash files without excluding matches by size first. Don't use."
    )
    parser.add_argument(
        "--include-symlinks",
        action="store_true",
        help="DANGER! Include symlinks in the analysis. This will return duplicate groups even if there is only one actual file in the group. Deleting the wrong file will remove the whole group.",
    )
    parser.add_argument("--hashfunc", default="metrohash", choices=hashfuncs.keys(), help="Hash function")
    parser.add_argument(
        "--dupeguru", metavar="PATH", type=Path, help="Output the results in Dupeguru format to this path."
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--exact", action="store_true", help="Use exact matches to find duplicates")
    group.add_argument(
        "--images", action="store_true", help="Use `Block Mean Value Based Image Perceptual Hashing` to find duplicates"
    )
    group.add_argument("--audio", action="store_true")

    args = parser.parse_args()

    handler = RichHandler(log_time_format="%Y-%m-%d %H-%M-%S%Z", highlighter=NullHighlighter())
    FORMAT = "%(message)s"

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG, format=FORMAT, handlers=[handler])
    else:
        logging.basicConfig(level=logging.INFO, format=FORMAT, handlers=[handler])

    with RichProgress(disable=not args.verbose) as progress:
        p = Progress(progress)

        if args.exact:
            hashfunc = hashfuncs[args.hashfunc]

            if args.no_size:
                groups = dupegroups_no_size(args.directories, hashfunc, p, args.include_symlinks)

                with StdoutFile(args.out, "xt", encoding="utf-8", newline="") as csvfile:
                    csvwriter = csv.writer(csvfile)
                    csvwriter.writerow(["hash", "path", "size"])
                    for hash, paths_sizes in groups.items():
                        for path, size in paths_sizes:
                            csvwriter.writerow([hash.hex(), path, size])

            else:
                groups = dupegroups(args.directories, hashfunc, p, args.include_symlinks)

                if args.dupeguru:
                    write_dupegroups_dupeguru(args.dupeguru, groups)

                with StdoutFile(args.out, "xt", encoding="utf-8", newline="") as csvfile:
                    csvwriter = csv.writer(csvfile)
                    csvwriter.writerow(["size", "hash", "path"])
                    for (size, hash), paths in groups.items():
                        for path in paths:
                            csvwriter.writerow([size, hash.hex(), path])

        elif args.images:
            with StdoutFile(args.out, "xt", encoding="utf-8") as fw:
                with p.task(description="Hashing images...") as task:
                    tree, map = image_hash_tree(args.directories, progressfunc=lambda _: task.advance(1))

                for d in range(100):
                    groups = []

                    for hashgroup in tree.find_by_distance(d):
                        files = []
                        for hash in hashgroup:
                            files.extend(map[hash])
                        groups.append(files)

                    if groups:
                        fw.write(f"Distance: {d}\n")
                        for hashgroup in groups:
                            fw.write(f"{hashgroup}\n")

                        fw.write("---\n")

        elif args.audio:
            parser.error("Duplicate search for audio is no implemented yet")
