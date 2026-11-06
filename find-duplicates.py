# /// script
# requires-python = ">=3.8"
# dependencies = [
#     "genutility[args,filesystem,fingerprinting,hash,metrics,rich]>=0.0.123",
#     "metrictrees",
#     "metrohash",
#     "pillow",
#     "rich",
# ]
# ///
import logging
import re
from collections import defaultdict
from hashlib import sha1
from heapq import heapify, heappop, heappush
from itertools import combinations, product
from pathlib import Path
from typing import (
    Callable,
    Collection,
    DefaultDict,
    Dict,
    Iterable,
    Iterator,
    List,
    Mapping,
    Optional,
    Sequence,
    TextIO,
    Tuple,
)

from genutility.exceptions import ParseError, Skip
from genutility.file import Empty
from genutility.fileformats.jfif import hash_raw_jpeg
from genutility.fileformats.png import hash_raw_png
from genutility.filesystem import PathType, entrysuffix, fileextensions, long_path_support, scandir_rec
from genutility.fingerprinting import phash_blockmean
from genutility.hash import hash_file
from genutility.metrics import hamming_distance
from genutility.rich import Progress
from metrictrees.bktree import BKTree
from metrohash import MetroHash128
from PIL import Image
from rich.highlighter import NullHighlighter
from rich.logging import RichHandler
from rich.progress import BarColumn, MofNCompleteColumn, TextColumn, TimeElapsedColumn
from rich.progress import Progress as RichProgress

logger = logging.getLogger(__name__)


def metrohash(path: str) -> bytes:
    return hash_file(path, MetroHash128).digest()


def nometahash(path: str) -> bytes:
    lowerpath = path.lower()

    if lowerpath.endswith((".jpg", ".jpeg")):
        hashobj = sha1()  # noqa: S324
        try:
            hash_raw_jpeg(path, hashobj)
        except ParseError as e:
            logging.info("Skipping invalid JPEG file %s: %s", path, e)
            raise Skip() from e
        except (Empty, EOFError) as e:
            logger.warning("Skipping JPEG file %s: incomplete or empty segment", path)
            raise Skip() from e
        except ValueError as e:
            logger.warning("Skipping JPEG file %s: %s", path, e)
            raise Skip() from e

        return hashobj.digest()

    elif lowerpath.endswith(".png"):
        hashobj = sha1()  # noqa: S324
        try:
            hash_raw_png(path, hashobj)
        except ParseError as e:
            logging.info("Skipping invalid PNG file %s: %s", path, e)
            raise Skip() from e
        except (Empty, EOFError) as e:
            logging.info("Skipping truncated PNG file %s", path)
            raise Skip() from e

        return hashobj.digest()

    else:
        raise Skip()


IGNORE_DIRNAMES = {".git"}
IMAGE_HASH_BITS = 256


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
    dirs: Iterable[PathType],
    exts: Optional[Collection] = None,
    progressfunc: Optional[Callable[[str, Optional[bytes]], None]] = None,
    *,
    original: bool = True,
) -> Tuple[BKTree[bytes], Dict[bytes, List[str]]]:
    tree: BKTree[bytes] = BKTree(hamming_distance)
    paths_by_hash: DefaultDict[bytes, List[str]] = defaultdict(list)
    exts = exts or fileextensions.images

    for dir in dirs:
        for entry in scandir_rec(dir, dirs=True, follow_symlinks=False, allow_skip=True):
            if entry.is_dir() and entry.name in IGNORE_DIRNAMES:
                logging.debug("Skipped: %s", entry.path)
                entry.follow = False
            elif entry.is_file():
                ext = entrysuffix(entry)[1:].lower()

                if ext in exts:
                    hashbytes = None
                    try:
                        img = Image.open(entry.path, "r")
                        img.load()  # force load so OSError can be caught here
                    except OSError:
                        logging.warning("Cannot open image: %s", entry.path)
                    else:
                        hashbytes = phash_blockmean(img, bits=IMAGE_HASH_BITS)
                        if original or hashbytes not in paths_by_hash:
                            tree.add(hashbytes)
                        paths_by_hash[hashbytes].append(entry.path)
                    if progressfunc is not None:
                        progressfunc(entry.path, hashbytes)

    return tree, paths_by_hash


ImageHashPair = Tuple[int, bytes, bytes]


def iter_image_hash_pairs(tree: BKTree[bytes], hashes: Iterable[bytes], max_distance: int) -> Iterator[ImageHashPair]:
    for left in sorted(hashes):
        for distance, right in sorted(tree.find(left, max_distance)):
            if left < right:
                yield distance, left, right


def single_linkage_groups(hashes: Iterable[bytes], pairs: Iterable[ImageHashPair]) -> Iterable[List[bytes]]:
    parents = {value: value for value in hashes}

    def root(value: bytes) -> bytes:
        while parents[value] != value:
            parents[value] = parents[parents[value]]
            value = parents[value]
        return value

    for _, left, right in pairs:
        parents[root(right)] = root(left)

    groups: DefaultDict[bytes, List[bytes]] = defaultdict(list)
    for value in parents:
        groups[root(value)].append(value)
    return groups.values()


def complete_linkage_groups(hashes: Iterable[bytes], pairs: Iterable[ImageHashPair]) -> Iterable[List[bytes]]:
    groups = {value: [value] for value in hashes}
    neighbors: Dict[bytes, Dict[bytes, int]] = {value: {} for value in groups}
    candidates = list(pairs)
    for distance, left, right in candidates:
        neighbors[left][right] = neighbors[right][left] = distance
    heapify(candidates)

    while candidates:
        distance, left, right = heappop(candidates)
        if neighbors.get(left, {}).get(right) != distance:
            continue

        # The surviving key is the smallest hash in the merged cluster.
        groups[left].extend(groups.pop(right))
        left_neighbors = neighbors[left]
        right_neighbors = neighbors.pop(right)
        del left_neighbors[right]
        del right_neighbors[left]
        for other in right_neighbors:
            del neighbors[other][right]

        # Only common neighbors still have every pair within the threshold.
        for other, old_distance in list(left_neighbors.items()):
            if other not in right_neighbors:
                del left_neighbors[other]
                del neighbors[other][left]
            elif right_neighbors[other] > old_distance:
                new_distance = right_neighbors[other]
                left_neighbors[other] = neighbors[other][left] = new_distance
                first, second = sorted((left, other))
                heappush(candidates, (new_distance, first, second))

    return groups.values()


def write_image_matches(
    output: TextIO, tree: BKTree[bytes], paths_by_hash: Mapping[bytes, Sequence[str]], grouping: str, max_distance: int
) -> None:
    pairs = iter_image_hash_pairs(tree, paths_by_hash, max_distance)
    if grouping == "pairs":
        for _, paths in sorted(paths_by_hash.items()):
            output.writelines(f"Distance: 0 {[a, b]}\n" for a, b in combinations(sorted(paths), 2))
        for distance, left, right in pairs:
            path_pairs = product(sorted(paths_by_hash[left]), sorted(paths_by_hash[right]))
            output.writelines(f"Distance: {distance} {[a, b]}\n" for a, b in path_pairs)
        return

    if grouping == "complete-linkage":
        groups = complete_linkage_groups(paths_by_hash, pairs)
    else:
        groups = single_linkage_groups(paths_by_hash, pairs)

    output.write(f"{grouping}, maximum distance threshold: {max_distance}\n")
    for group in sorted(groups, key=min):
        paths = sorted(path for value in group for path in paths_by_hash[value])
        if len(paths) > 1:
            output.write(f"{paths}\n")


def iter_size_hashes(
    dups: Mapping[int, Sequence[str]], hashfunc: Callable[[str], bytes]
) -> Iterator[Tuple[int, Dict[bytes, List[str]]]]:
    for size, group in dups.items():
        hashes: DefaultDict[bytes, List[str]] = defaultdict(list)

        for path in group:
            try:
                hashbytes = hashfunc(path)
            except PermissionError:
                logging.warning("Permission denied: %s", path)
            except FileNotFoundError:
                logging.warning("File not found: %s", path)
            except Skip:
                pass
            else:
                hashes[hashbytes].append(path)

        yield size, hashes


def write_dupegroups_dupeguru(outpath: Path, pathgroups: Iterable[Sequence[str]]):
    from xml.etree import ElementTree as ET

    root = ET.Element("results")
    root.text = "\n"
    root.tail = "\n"

    for paths in pathgroups:
        group = ET.SubElement(root, "group")
        group.text = "\n"
        group.tail = "\n"

        for filepath in paths:
            element = ET.SubElement(group, "file", path=filepath, words="", is_ref="n", marked="n")
            element.tail = "\n"

        for i, j in combinations(range(len(paths)), 2):
            element = ET.SubElement(group, "match", first=str(i), second=str(j), percentage="100")
            element.tail = "\n"

    tree = ET.ElementTree(root)
    with outpath.open("xb") as fw:
        tree.write(fw, encoding="utf-8")


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
    from argparse import SUPPRESS, ArgumentDefaultsHelpFormatter, ArgumentParser

    from genutility.args import in_range, is_dir, new_path
    from genutility.file import StdoutFile

    hashfuncs = {"metrohash": metrohash, "no-meta-sha1": nometahash}

    parser = ArgumentParser(formatter_class=ArgumentDefaultsHelpFormatter)
    parser.add_argument(
        "directories", type=is_dir, nargs="+", help="Directories to search. Input roots must not overlap."
    )
    parser.add_argument(
        "-o",
        "--out",
        metavar="PATH",
        type=new_path,
        help="Optional output file path. If not given it will be printed to stdout.",
    )
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
        "--dupeguru", metavar="PATH", type=new_path, help="Output the results in Dupeguru format to this path."
    )
    parser.add_argument(
        "--regex",
        nargs=2,
        metavar=("PATTERN", "REPL"),
        default=("^(.*)$", r"\1"),
        help="Extract key to group by from filename.",
    )
    parser.add_argument(
        "--image-grouping",
        choices=("original", "complete-linkage", "single-linkage", "pairs"),
        default=SUPPRESS,
        help=(
            "Output for --images. The default, original, reports distances 0 through 99 using tree-based groups "
            "that can overlap, omit matches, or contain pairs farther apart than the heading. "
            "Complete linkage gives disjoint groups "
            "with every pair within range and breaks ties by smallest hash. Single linkage gives disjoint groups "
            "connected by chains of matches, so some members may be farther apart than the threshold. "
            "Pairs lists every matching file pair and can repeat paths."
        ),
    )
    parser.add_argument(
        "--image-max-distance",
        type=in_range(0, IMAGE_HASH_BITS + 1),
        metavar="BITS",
        help=f"Inclusive Hamming distance threshold (0 to {IMAGE_HASH_BITS}). Required for all modes except original.",
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--exact", action="store_true", help="Use exact matches to find duplicates")
    group.add_argument(
        "--images", action="store_true", help="Use `Block Mean Value Based Image Perceptual Hashing` to find duplicates"
    )
    group.add_argument("--audio", action="store_true")
    group.add_argument(
        "--filename",
        action="store_true",
        help="Search for duplicated files by filename. Use --regex extract group key from filename.",
    )

    args = parser.parse_args()
    try:
        roots = [(directory, Path(long_path_support(directory)).resolve(strict=True)) for directory in args.directories]
    except (OSError, RuntimeError) as e:
        parser.error(f"Cannot resolve input directory ({e})")
    for (first, left), (second, right) in combinations(roots, 2):
        if left == right or left in right.parents or right in left.parents:
            parser.error(f"Input directories overlap ({first} and {second})")

    image_grouping = getattr(args, "image_grouping", "original")
    original_images = image_grouping == "original"
    if not args.images and (hasattr(args, "image_grouping") or args.image_max_distance is not None):
        parser.error("Image grouping and distance options require --images")
    if args.images:
        if original_images and args.image_max_distance is not None:
            parser.error("--image-max-distance requires complete-linkage, single-linkage, or pairs")
        if not original_images and args.image_max_distance is None:
            parser.error("--image-max-distance is required for complete-linkage, single-linkage, and pairs")

    handler = RichHandler(log_time_format="%Y-%m-%d %H-%M-%S%Z", highlighter=NullHighlighter())
    FORMAT = "%(message)s"

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG, format=FORMAT, handlers=[handler])
    else:
        logging.basicConfig(level=logging.INFO, format=FORMAT, handlers=[handler])

    columns = [
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
    ]
    with RichProgress(*columns, disable=not args.verbose) as progress:
        p = Progress(progress)

        if args.exact:
            hashfunc = hashfuncs[args.hashfunc]

            if args.no_size:
                groups = dupegroups_no_size(args.directories, hashfunc, p, args.include_symlinks)

                if args.dupeguru:
                    pathgroups = ([path for path, size in paths_sizes] for paths_sizes in groups.values())
                    write_dupegroups_dupeguru(args.dupeguru, pathgroups)

                with StdoutFile(args.out, "xt", encoding="utf-8", newline="") as csvfile:
                    csvwriter = csv.writer(csvfile)
                    csvwriter.writerow(["hash", "path", "size"])
                    for hashbytes, paths_sizes in groups.items():
                        for path, size in paths_sizes:
                            csvwriter.writerow([hashbytes.hex(), path, size])

            else:
                groups = dupegroups(args.directories, hashfunc, p, args.include_symlinks)

                if args.dupeguru:
                    write_dupegroups_dupeguru(args.dupeguru, groups.values())

                with StdoutFile(args.out, "xt", encoding="utf-8", newline="") as csvfile:
                    csvwriter = csv.writer(csvfile)
                    csvwriter.writerow(["size", "hash", "path"])
                    for (size, hashbytes), paths in groups.items():
                        for path in paths:
                            csvwriter.writerow([size, hashbytes.hex(), path])

        elif args.images:
            if args.dupeguru:
                parser.error("--dupeguru not yet supported for --images")

            with StdoutFile(args.out, "xt", encoding="utf-8") as fw:
                with p.task(description="Hashing images...") as task:

                    def progress(_path: str, _hash: Optional[bytes]) -> None:
                        task.advance(1)

                    tree, paths_by_hash = image_hash_tree(
                        args.directories, progressfunc=progress, original=original_images
                    )

                if original_images:
                    for d in range(100):
                        image_groups = [
                            [path for value in hashgroup for path in paths_by_hash[value]]
                            for hashgroup in tree.find_by_distance(d)
                        ]
                        if image_groups:
                            fw.write(f"Distance: {d}\n")
                            for paths in image_groups:
                                fw.write(f"{paths}\n")
                            fw.write("---\n")
                else:
                    write_image_matches(fw, tree, paths_by_hash, image_grouping, args.image_max_distance)

        elif args.audio:
            parser.error("Duplicate search for audio is no implemented yet")

        elif args.filename:
            dups: DefaultDict[str, List[Tuple[str, int]]] = defaultdict(list)

            pattern_, repl = args.regex
            pattern = re.compile(pattern_)

            logging.info("Using pattern `%s` with replacement `%s`", pattern.pattern, repl)

            total = 0
            for size, path in p.track(
                iter_size_path(args.directories, args.include_symlinks), description="Collecting files..."
            ):
                key, nsubs = pattern.subn(repl, Path(path).name)
                if nsubs > 0:
                    dups[key].append((path, size))
                    total += 1
            logging.info("Found %s groups in %d files", len(dups), total)

            dups = {k: v for k, v in dups.items() if len(v) > 1}
            logging.info("Found %s duplicate groups", len(dups))

            if args.dupeguru:
                pathgroups = ([path for path, size in paths_sizes] for paths_sizes in dups.values())
                write_dupegroups_dupeguru(args.dupeguru, pathgroups)

            with StdoutFile(args.out, "xt", encoding="utf-8", newline="") as csvfile:
                csvwriter = csv.writer(csvfile)
                csvwriter.writerow(["key", "path", "size"])
                for key, paths_sizes in dups.items():
                    for path, size in paths_sizes:
                        csvwriter.writerow([key, path, size])
