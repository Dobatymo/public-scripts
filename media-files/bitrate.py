# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "genutility",
#     "pymediainfo",
#     "humanfriendly",
#     "rich",
# ]
# ///
import logging
import os
import sys
from argparse import ArgumentDefaultsHelpFormatter, ArgumentParser
from pathlib import Path
from typing import Dict, Iterator, Optional, Sequence, Tuple

from genutility.filesystem import fileextensions
from genutility.rich import Progress
from humanfriendly import format_size, parse_size
from pymediainfo import MediaInfo
from rich.logging import RichHandler
from rich.progress import Progress as RichProgress


def get_bitrate(path: Path) -> Optional[int]:
    media_info = MediaInfo.parse(os.fspath(path))
    try:
        video_tracks = media_info.video_tracks[0]
    except IndexError:
        logging.debug("%s doesn't have a video track", path)
        return None

    bitrate = video_tracks.bit_rate

    if bitrate is None:
        logging.debug("%s doesn't have a bitrate", path)
        return None

    return bitrate


def on_error(e: OSError) -> None:
    logging.debug("Failed to walk %s: %s", e.filename, e)


def count_files_recursive(root: Path, suffixes: Sequence[str]) -> Iterator[Tuple[Path, int, int, int]]:
    """
    Yield (dirpath, total_file_count) for each directory under root,
    where total_file_count includes files in all subdirectories.
    """
    counts: Dict[Path, Tuple[int, int, int]] = {}

    for dirpath, dirnames, filenames in root.walk(top_down=False, on_error=on_error):
        num_files = 0
        total_size = 0
        total_bitrate = 0

        for filename in filenames:
            path = dirpath / filename
            if path.suffix not in suffixes:
                continue

            try:
                bitrate = get_bitrate(path)
            except FileNotFoundError:
                logging.debug("%s not found", path)
                continue

            if bitrate is None:
                continue

            num_files += 1
            total_size += path.stat().st_size
            total_bitrate += bitrate

        # add totals from all subdirectories
        for dirname in dirnames:
            path = dirpath / dirname
            try:
                num_files_, total_size_, total_bitrate_ = counts[path]
            except KeyError:
                logging.debug("Ignoring subdir %s", path)
                continue
            num_files += num_files_
            total_size += total_size_
            total_bitrate += total_bitrate_

        counts[dirpath] = (num_files, total_size, total_bitrate)
        yield dirpath, num_files, total_size, total_bitrate


def main() -> None:
    description = """
Recursively analyze video files in a directory tree and report per-directory
statistics about file counts, total sizes, and average bitrates.

This script helps you quickly identify directories that meet certain thresholds
of video content—for example, a minimum number of files, a minimum total size,
or a minimum average bitrate. It can be useful for tasks such as:

- Checking whether a media archive contains sufficiently high-quality encodes.
- Finding directories with too few or too small video files.
- Filtering out collections of low-bitrate (poor quality) videos.
- Summarizing disk usage and average quality of large media folders.

Only recognized video file extensions are processed, and bitrate information is
extracted from media metadata (via `pymediainfo`). Results are printed for each
directory that passes the given thresholds.
"""

    parser = ArgumentParser(description=description, formatter_class=ArgumentDefaultsHelpFormatter)
    parser.add_argument("path", nargs="+", type=Path, help="Root directory to scan recursively for video files.")
    parser.add_argument(
        "--min-files",
        type=int,
        default=1,
        metavar="N",
        help="Minimum number of video files required in a directory (including subdirectories) for it to be reported",
    )
    parser.add_argument(
        "--min-total-size",
        type=parse_size,
        default=0,
        metavar="N",
        help="Minimum cumulative file size required in a directory (across all included video files) for it to be reported. Accepts human-friendly values like '500MB' or '2GiB'.",
    )
    parser.add_argument(
        "--min-mean-bitrate",
        type=parse_size,
        default=0,
        metavar="N",
        help="Minimum average bitrate (across all included video files in a directory) required for it to be reported. Accepts human-friendly values like '1Mbps' or '800k'.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging for debugging. Shows details when video metadata or bitrates cannot be read.",
    )
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG, handlers=[RichHandler()])
    else:
        logging.basicConfig(level=logging.INFO, handlers=[RichHandler()])

    suffixes = [f".{ext}" for ext in fileextensions.video]

    try:
        with RichProgress() as p:
            progress = Progress(p)
            for basepath in progress.track(args.path):
                for path, num_files, total_size, total_bitrate in progress.track(
                    count_files_recursive(basepath, suffixes), description=os.fspath(basepath), transient=True
                ):
                    if num_files > args.min_files and total_size > args.min_total_size:
                        mean_bitrate = total_bitrate / num_files
                        if mean_bitrate > args.min_mean_bitrate:
                            progress.print(
                                f"{path} ({num_files}): total size={format_size(total_size, binary=True)}, mean bitrate={format_size(mean_bitrate, binary=True)}"
                            )
    except KeyboardInterrupt:
        print("interrupted")
        sys.exit(1)


if __name__ == "__main__":
    main()
