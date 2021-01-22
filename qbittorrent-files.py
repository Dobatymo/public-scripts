from __future__ import generator_stop

import logging
import os.path
from os import fspath
from pathlib import Path

from genutility.exceptions import NotFound
from genutility.os import get_appdata_dir
from genutility.torrent import read_torrent, read_torrent_info_dict, torrent_info_hash, write_torrent

logger = logging.getLogger(__name__)

class MoveFileError(OSError):
	pass

class QBittorrentMeta(object):

	__slots__ = ("btpath", "map")

	def __init__(self, path=None):
		# type: (Optional[Path], ) -> None

		self.btpath = path or Path(os.path.expandvars("%LOCALAPPDATA%/qBittorrent/BT_backup"))
		if not self.btpath.exists():
			raise FileNotFoundError(f"QBittorrent torrent directory {self.btpath} doesn't exist")
		if not self.btpath.is_dir():
			raise NotADirectoryError(f"QBittorrent torrent path {self.btpath} not a directory")

		self.map = self.get_single_file_mappings(self.btpath)

	def move_single_file_to(self, src, dst):
		# type: (Path, Path) -> None

		if src.name != dst.name:
			raise ValueError(f"Cannot change file name {src.name} -> {dst.name}. Only directories.")

		if dst.exists():
			raise FileExistsError(f"Cannot move {src} to {dst}. File already exists.")

		info_hash = self.path_to_info_hash(src)
		self.set_fastresume_path(info_hash, fspath(dst.parent))
		src.rename(dst)

	def move_single_file(self, path, dry=True):
		# type: (Path, bool) -> bool

		""" Finds torrent which matches `path` and move file to the correct location.
		"""

		info_hash = self.path_to_info_hash(path)
		torrentpath = self.get_fastresume_path(info_hash)

		if path == torrentpath or dry:
			return False
		else:
			path.rename(torrentpath)
			return True

	def single_file_moved(self, path, dry=True):
		# type: (Path, bool) -> bool

		""" Finds torrent which matches `path` and adjusts fastresume meta data.
		"""

		info_hash = self.path_to_info_hash(path)
		if dry:
			return False
		else:
			return self.set_fastresume_path(info_hash, fspath(path.parent))

	def path_to_info_hash(self, path):
		# type: (Path, ) -> str

		name = path.name
		size = path.stat().st_size

		try:
			return self.map[(name, size)]
		except KeyError:
			raise NotFound(f"Could not find infohash for name={name}, size={size}")

	def read_fastresume_file(self, info_hash):
		# type: (str, ) -> dict

		fastresumepath = self.btpath / f"{info_hash}.fastresume"
		try:
			bb = read_torrent(fastresumepath)
		except FileNotFoundError:
			raise FileNotFoundError(f"Could not find fastresume file: {fastresumepath}")

		if bb["qBt-savePath"] != bb["save_path"]:
			raise AssertionError("Save paths don't match: {bb['qBt-savePath']} vs {bb['save_path']}")

		return bb

	def write_fastresume_file(self, bb, info_hash):
		fastresumepath = self.btpath / f"{info_hash}.fastresume"
		write_torrent(bb, fastresumepath)

	def get_fastresume_path(self, info_hash):
		# type: (str, ) -> str

		bb = self.read_fastresume_file(info_hash)
		return bb["save_path"]

	def set_fastresume_path(self, info_hash, torrentpath):
		# type: (str, str) -> bool

		bb = self.read_fastresume_file(info_hash)

		if bb["save_path"] == torrentpath:
			return False
		else:
			bb["qBt-savePath"] = torrentpath
			bb["save_path"] = torrentpath
			write_fastresume_file(bb, info_hash)
			return True

	@staticmethod
	def get_single_file_mappings(path):
		ret = {} # type: Dict[Tuple[str, int], str]

		for torrentfile in path.glob("*.torrent"):
			info = read_torrent_info_dict(torrentfile)
			info_hash = torrent_info_hash(info)
			if info_hash != torrentfile.stem:
				raise AssertionError(f"Calculated info hash does not match file name: {info_hash} vs {torrentfile.stem}")

			try:
				name = info["name"]
				size = info["length"]
			except KeyError: # not a single file torrent
				logger.info("Skipping %s is it's not a single file torrent", torrentfile)
				continue

			if (name, size) in ret:
				raise AssertionError(f"Duplicate file: {name} ({size})")
			ret[(name, size)] = info_hash

		return ret

def replace_directory(dirpath, from_s, to_s):
	# type: (Path, str, str) -> None

	for filepath in dirpath.glob("*.fastresume"):
		bb = read_torrent(filepath)

		try:
			save_a = bb["qBt-savePath"]
			save_b = bb["save_path"]
			if save_a != save_b:
				raise AssertionError("Save paths not equal")

			bb["qBt-savePath"] = save_a.replace(from_s, to_s)
			bb["save_path"] = save_b.replace(from_s, to_s)
		except KeyError: # magnet link without metadata
			bb["qBt-savePath"] = bb["qBt-savePath"].replace(from_s, to_s)

		write_torrent(bb, filepath)

def match_directory(path, move_files=False, recursive=True):
	# type: (Path, bool, bool) -> None

	""" Scans directory `path` for files known to QBittorrent and either adjusts the
		fastresume files to have the correct path, or moves the files to the path specified
		in the fastresume files.
	"""

	qb = QBittorrentMeta()

	if recursive:
		it = path.rglob("*")
	else:
		it = path.glob("*")

	if move_files:
		for p in it:
			try:
				if qb.move_single_file(p):
					logger.info("Moved file %s", p)
				else:
					logger.info("File %s already in destination", p)
			except NotFound:
				logger.debug("Did not find torrent file for %s", p)
	else:
		for p in it:
			try:
				if qb.single_file_moved(p):
					logger.info("Adjusted torrent path for %s", p)
				else:
					logger.info("Torrent path already correct for %s", p)
			except NotFound:
				logger.debug("Did not find torrent file for %s", p)

if __name__ == "__main__":

	from argparse import ArgumentParser

	from genutility.args import is_dir, is_file

	parser = ArgumentParser(description="Change save locations for files in QBittorrent appdata.\nWarning: Close QBittorrent before running this script.")
	parser.add_argument("--BT_backup", metavar="path", type=is_dir)
	parser.add_argument("--verbose", action="store_true")
	subparsers = parser.add_subparsers(dest="command")
	subparsers.required = True

	parser_a = subparsers.add_parser("replace", help="Replace substring in torrent paths. Useful to move entire directories.")
	parser_a.add_argument("old", help="Path substring to be replaced. For example 'C:'.")
	parser_a.add_argument("new", help="New path substring. For example 'D:'.")

	parser_b = subparsers.add_parser("move", help="Moves a single file on the filesystem while also adjusting the save-path in QBittorrent")
	parser_b.add_argument("src", type=is_file, help="File source path")
	parser_b.add_argument("dst", type=Path, help="File destination path")

	parser_c = subparsers.add_parser("scan", help="Scans a directory for files which belong to torrents known to QBittorrent and either adjusts the save-paths in QBittorrent or moves the files to the save-path location.")
	parser_c.add_argument("path", type=is_dir, help="Path to scan for files from torrents")
	parser_c.add_argument("--move", action="store_true", help="Move files instead of adjusting torrent paths")
	parser_c.add_argument("--recursive", action="store_true", help="Scan recursively")

	args = parser.parse_args()

	if args.verbose:
		logging.basicConfig(level=logging.DEBUG)
	else:
		logging.basicConfig(level=logging.INFO)

	if args.command == "replace":
		replace_directory(args.path, args.old, args.new)
	elif args.command == "move":
		qb = QBittorrentMeta()
		qb.move_single_file_to(args.src, args.dst)
	elif args.command == "scan":
		match_directory(args.path, args.move, args.recursive)
	else:
		parser.error("Invalid command")
