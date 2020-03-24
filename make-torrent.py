import logging
from math import log2, ceil
from functools import partial

import libtorrent

from genutility.compat.os import fspath

logger = logging.getLogger(__name__)

def create_torrent(path, trackers=(), private=True, pieces=None, piece_size=None, predicate=None, progress=None, min_piece_exp=14, max_piece_exp=24):
	# type: (Path, Iterable[str], Optional[int], Optional[int], int, Optional[Callable], Optional[Callable], int, int) -> bytes

	if pieces is None and piece_size is None:
		pieces = 1000
	elif pieces is None and piece_size is not None:
		assert piece_size % 16384 == 0
	elif pieces is not None and piece_size is None:
		pass
	else:
		assert False, "Cannot specify `pieces` and `piece_size`"

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
		piece_size = 2**min(max(round(log2(fs.total_size() / pieces)), min_piece_exp), max_piece_exp)
		logger.info("piece_size", piece_size)

	t = libtorrent.create_torrent(fs, piece_size)

	for tracker in trackers:
		t.add_tracker(tracker)
	if private:
		t.set_priv(private)

	libtorrent.set_piece_hashes(t, fspath(path.parent), partial(progress, ceil(fs.total_size() / piece_size)))

	return libtorrent.bencode(t.generate())

if __name__ == "__main__":

	from argparse import ArgumentParser
	from genutility.args import existing_path, future_file

	parser = ArgumentParser(description="Create torrent file")
	parser.add_argument("inpath", type=existing_path, help="Path to files")
	parser.add_argument("outpath", type=future_file, help="Path to put torrent file")
	parser.add_argument("--trackers", metavar="TRACKER", nargs="*", help="List of tracker URLs")
	parser.add_argument("--silent", action="store_true", help="Don't show progress bar")
	parser.add_argument("--private", action="store_true", help="Create private torrent")
	parser.add_argument("--pieces", type=int, default=None, help="Approximate number of pieces in resulting torrent file. 1000 will be used if --piece-size is not given instead..")
	parser.add_argument("--piece-size", type=int, default=None, help="Piece size. If not given, it will be automatically calculated to match --pieces.")
	args = parser.parse_args()

	if args.private and not args.trackers:
		parser.error("Private torrents must have trackers")

	def predicate(x):
		print("Added", x)
		return True

	from tqdm import tqdm

	pbar = tqdm(disable=args.silent)
	def progress(total, i):
		pbar.total = total
		pbar.update()

	try:
		data = create_torrent(args.inpath, args.trackers, args.private, args.pieces, args.piece_size, predicate=predicate, progress=progress)
	finally:
		pbar.close()

	with open(args.outpath, "wb") as fw:
		fw.write(data)
