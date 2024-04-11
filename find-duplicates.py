import logging
from collections import defaultdict
from hashlib import sha1
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


def iter_size_path(dirs: Iterable[PathType]) -> Iterator[Tuple[int, str]]:
    for dir in dirs:
        for entry in scandir_rec(dir, dirs=True, follow_symlinks=False, allow_skip=True):
            if entry.is_dir() and entry.name in IGNORE_DIRNAMES:
                logging.debug("Skipped: %s", entry.path)
                entry.follow = False
            elif entry.is_file():
                filesize = entry.stat().st_size
                yield filesize, entry.path


def image_hash_tree(
    dirs: Iterable[PathType], exts: Optional[Collection] = None, processfunc=None
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
                    if processfunc:
                        processfunc((entry.path, hash))

    return tree, map


def iter_size_hashes(
    dups: Mapping[int, List[str]], hashfunc: Callable[[str], bytes]
) -> Iterator[Tuple[int, Dict[bytes, List[str]]]]:
    for size, group in dups.items():
        hashes = defaultdict(list)

        for path in group:
            try:
                hash = hashfunc(path)
            except PermissionError:
                logging.warning("Permission denied: %s", path)
            except FileNotFoundError:
                logging.warning("File not found: %s", path)
            else:
                hashes[hash].append(path)

        yield size, hashes


def dupgroups(
    dirs: Iterable[PathType], hashfunc: Callable[[str], bytes], progress: Progress
) -> Dict[Tuple[int, bytes], List[str]]:
    dups = defaultdict(list)

    for size, path in progress.track(iter_size_path(dirs), description="Collecting files..."):
        dups[size].append(path)
    logging.info("Found %s size groups", len(dups))

    logging.info("Filtering files based on size")
    dups = {k: v for k, v in dups.items() if len(v) > 1}
    logging.info("Found %s duplicate size groups", len(dups))

    logging.info("Calculating hash groups")
    for size, hashes in progress.track(iter_size_hashes(dups, hashfunc), total=len(dups)):
        dups[size] = hashes

    logging.info("Filtering files based on hashes")
    newdups: Dict[Tuple[int, bytes], List[str]] = {}
    for size, sizegroup in dups.items():
        for hash, hashgroup in sizegroup.items():
            if len(hashgroup) > 1:
                newdups[(size, hash)] = hashgroup

    return newdups


def dupgroups_no_size(
    dirs: Iterable[PathType], hashfunc: Callable[[str], bytes], progress: Progress
) -> Dict[bytes, List[Tuple[str, int]]]:
    dups = defaultdict(list)

    for size, path in progress.track(iter_size_path(dirs), description="Calculating hash groups..."):
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

    logging.info("Found %s hash groups", len(dups))

    logging.info("Filtering files based on hash")
    dups = {k: v for k, v in dups.items() if len(v) > 1}
    logging.info("Found %s duplicate hash groups", len(dups))

    return dups


if __name__ == "__main__":
    import csv
    from argparse import ArgumentParser

    from genutility.args import is_dir
    from genutility.file import StdoutFile

    hashfuncs = {
        "metrohash": metrohash,
        "no-meta-sha1": nometahash,
    }

    parser = ArgumentParser()
    parser.add_argument("directories", type=is_dir, nargs="+", help="Directory to search")
    parser.add_argument("-o", "--out", help="outputfile")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--no-size", action="store_true")
    parser.add_argument("--hashfunc", default="metrohash", choices=hashfuncs.keys())

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--exact", action="store_true")
    group.add_argument("--images", action="store_true")
    group.add_argument("--audio", action="store_true")

    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    with RichProgress(disable=not args.verbose) as progress:
        p = Progress(progress)

        if args.exact:
            hashfunc = hashfuncs[args.hashfunc]

            if args.no_size:
                groups = dupgroups_no_size(args.directories, hashfunc, p)

                with StdoutFile(args.out, "xt", encoding="utf-8", newline="") as csvfile:
                    csvwriter = csv.writer(csvfile)
                    csvwriter.writerow(["hash", "path", "size"])
                    for hash, paths_sizes in groups.items():
                        for path, size in paths_sizes:
                            csvwriter.writerow([hash.hex(), path, size])

            else:
                groups = dupgroups(args.directories, hashfunc, p)

                with StdoutFile(args.out, "xt", encoding="utf-8", newline="") as csvfile:
                    csvwriter = csv.writer(csvfile)
                    csvwriter.writerow(["size", "hash", "path"])
                    for (size, hash), paths in groups.items():
                        for path in paths:
                            csvwriter.writerow([size, hash.hex(), path])

        elif args.images:
            with StdoutFile(args.out, "xt", encoding="utf-8") as fw:
                with p.task(description="Hashing images...") as task:
                    tree, map = image_hash_tree(args.directories, processfunc=lambda _: task.advance(1))

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
