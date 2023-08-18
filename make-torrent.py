from __future__ import generator_stop

import logging
from functools import partial
from math import ceil, log2
from os import fspath
from pathlib import Path
from typing import Callable, Iterable, Optional

import libtorrent

logger = logging.getLogger(__name__)


def create_torrent(
    path: Path,
    trackers: Iterable[str] = (),
    private: bool = True,
    pieces: Optional[int] = None,
    piece_size: Optional[int] = None,
    source: Optional[str] = None,
    predicate: Optional[Callable] = None,
    progress: Optional[Callable] = None,
    min_piece_exp: int = 14,
    max_piece_exp: int = 24,
) -> bytes:
    """
    min_piece_exp: default 16k
    max_piece_exp: default 16M
    """

    if pieces is None and piece_size is None:
        pieces = 1000
    elif pieces is None and piece_size is not None:
        if piece_size % 16384 != 0:
            raise ValueError("piece_size must be a multiple of 16384")
    elif pieces is not None and piece_size is None:
        pass
    else:
        raise ValueError("Cannot specify `pieces` and `piece_size`")

    fs = libtorrent.file_storage()

    path = path.resolve()

    if path.is_file():
        fs.add_file(path.name, path.stat().st_size)

    elif path.is_dir():
        predicate = predicate or (lambda path: True)
        libtorrent.add_files(fs, fspath(path), predicate)
    else:
        assert False, "Path neither file nor directory"

    if pieces:
        piece_size = 2 ** min(max(round(log2(fs.total_size() / pieces)), min_piece_exp), max_piece_exp)
        logger.info("piece_size", piece_size)

    t = libtorrent.create_torrent(fs, piece_size)

    for tracker in trackers:
        t.add_tracker(tracker)
    if private:
        t.set_priv(private)

    if progress:
        libtorrent.set_piece_hashes(
            t,
            fspath(path.parent),
            partial(progress, ceil(fs.total_size() / piece_size)),
        )
    else:
        libtorrent.set_piece_hashes(t, fspath(path.parent))

    d = t.generate()
    if source:
        d[b"info"][b"source"] = source.encode("utf-8")

    return libtorrent.bencode(d)


if __name__ == "__main__":
    from argparse import ArgumentParser

    from genutility.args import existing_path, future_file
    from genutility.rich import Progress
    from rich.progress import Progress as RichProgress

    parser = ArgumentParser(description="Create torrent file")
    parser.add_argument("inpath", type=existing_path, help="Path to files")
    parser.add_argument("outpath", type=future_file, help="Path to put torrent file")
    parser.add_argument(
        "--trackers",
        metavar="TRACKER",
        nargs="*",
        default=[],
        help="List of tracker URLs",
    )
    parser.add_argument("--silent", action="store_true", help="Don't show progress bar")
    parser.add_argument("--private", action="store_true", help="Create private torrent")
    parser.add_argument(
        "--pieces",
        type=int,
        default=None,
        help="Approximate number of pieces in resulting torrent file. 1000 will be used if --piece-size is not given instead..",
    )
    parser.add_argument(
        "--piece-size",
        type=int,
        default=None,
        help="Piece size. If not given, it will be automatically calculated to match --pieces.",
    )
    parser.add_argument("--source", default=None, help="Source value of info dict")
    args = parser.parse_args()

    if args.private and not args.trackers:
        parser.error("Private torrents must have trackers")

    def predicate(x):
        print("Added", x)
        return True

    with RichProgress(disable=args.silent) as progress:
        p = Progress(progress)
        with p.task(description="Creating torrent...") as task:

            def progressfunc(total, i):
                task.update(total=total, completed=i)

            data = create_torrent(
                args.inpath,
                args.trackers,
                args.private,
                args.pieces,
                args.piece_size,
                args.source,
                predicate=predicate,
                progress=progressfunc,
            )

    with open(args.outpath, "wb") as fw:
        fw.write(data)
