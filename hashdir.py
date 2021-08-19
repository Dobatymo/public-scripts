from __future__ import generator_stop

import hashlib
import os.path
import re
from os import fspath
from pathlib import Path
from typing import IO, TYPE_CHECKING, Iterable, List, Optional, Sequence

from genutility.file import StdoutFile
from genutility.filesystem import PathType, scandir_rec
from genutility.hash import hash_file
from genutility.iter import CachedIterable

if TYPE_CHECKING:
	from genutility.hash import HashCls, Hashobj

class DirHasher:

	def __init__(self, paths, hashes, toppath=None):
		# type: (Sequence[str], Iterable[bytes], Optional[str]) -> None

		self.paths = paths
		self.hashes = hashes
		self._toppath = toppath

	@property
	def toppath(self):
		# type: () -> str

		if self._toppath is None:
			self._toppath = os.path.commonprefix(self.paths)

		return self._toppath

	@classmethod
	def from_fs(cls, dirpath, hashcls=hashlib.sha1):
		# type: (PathType, HashCls) -> DirHasher

		""" Creates a DirHasher instance for a filesystem folder.
		"""

		paths = [entry.path for entry in sorted(scandir_rec(dirpath, files=True, dirs=False), key=lambda x: x.path)]
		hashes = (hash_file(path, hashcls).digest() for path in paths)

		return cls(paths, CachedIterable(hashes), fspath(dirpath))

	@classmethod
	def from_file(cls, filepath):
		# type: (PathType, ) -> DirHasher

		""" Reads a file ins md5sum or sha1sum format and creates a DirHasher instance.
		"""

		hashes = []  # type: List[bytes]
		paths = []  # type: List[str]

		with open(filepath, "r", encoding="utf-8") as fr:
			for line in fr:
				m = re.match(r"([0-9a-fA-F]+) [ \*](.*)", line.rstrip())
				if not m:
					raise ValueError("Invalid file")

				hash, path = m.groups()
				filehash = bytes(bytearray.fromhex(hash))
				hashes.append(filehash)
				paths.append(path)

		return cls(paths, hashes, None)

	@staticmethod
	def format_line(hexdigest, path, end="\n"):
		return f"{hexdigest} *{path}{end}"

	def to_stream(self, stream, include_total=True, hashcls=hashlib.sha1, include_names=True):
		# type: (IO[str], bool, HashCls, bool) -> None

		for filehash, path in zip(self.hashes, self.paths):
			stream.write(self.format_line(filehash.hex(), path))

		if include_total:
			m = self.total(hashcls, include_names)
			line = self.format_line(m.hexdigest(), self.toppath)
			stream.write(line)

	def total_line(self, hashcls=hashlib.sha1, include_names=True):
		# type: (HashCls, bool) -> str

		m = self.total(hashcls, include_names)
		line = self.format_line(m.hexdigest(), self.toppath, end="")
		return line

	def total(self, hashcls=hashlib.sha1, include_names=True):
		# type: (HashCls, bool) -> Hashobj

		if isinstance(hashcls, str):
			m = hashlib.new(hashcls)
		else:
			m = hashcls()

		for filehash, path in zip(self.hashes, self.paths):
			if include_names:
				m.update(os.path.basename(path).encode("utf-8"))
			m.update(filehash)

		return m

if __name__ == "__main__":

	from argparse import ArgumentParser

	parser = ArgumentParser(description="calculate hash of all files in directory combined")
	parser.add_argument("path", type=Path, help="input directory")
	parser.add_argument("--no-names", action="store_true", help="Don't include filenames in hash calculation")
	parser.add_argument("--input", choices=("fs", "file"), default="fs")
	parser.add_argument("--out", help="Optional out file. If not specified it's printed to stdout.")
	parser.add_argument("--algorithm", choices=("sha1", "md5"), default="sha1", help="Hashing algorithm for file contents.")
	args = parser.parse_args()

	if args.input == "fs":
		if not args.path.is_dir():
			parser.error("path has to be a directory")

		with StdoutFile(args.out, "xt") as fw:
			hasher = DirHasher.from_fs(args.path, args.algorithm)
			hasher.to_stream(fw, include_total=True, include_names=not args.no_names)

	elif args.input == "file":
		if not args.path.is_file():
			parser.error("path has to be a file")

		hasher = DirHasher.from_file(args.path)
		line = hasher.total_line(include_names=not args.no_names)
		print(line)
