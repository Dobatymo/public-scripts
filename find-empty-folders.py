from __future__ import absolute_import, division, print_function, unicode_literals
from builtins import str

import os, logging
from fnmatch import fnmatch

from genutility.deprecated_filesystem import listdir_rec_adv
from genutility.args import is_dir

def log_error(path, exc):
	logging.warning("Removing %s failed", path)

def enum_empty_dirs(dirpath, pattern="*"):
	for path, dc, fc in listdir_rec_adv(dirpath, dirs=True, files=False):
		if dc == 0 and fc == 0:
			filename = os.path.basename(path)
			if fnmatch(filename, pattern):
				yield path

def find_empty_dirs(dirpath, pattern="*", remove=False, errorfunc=log_error):
	# type(str, str) -> None

	for path in enum_empty_dirs(dirpath, pattern):
		if remove:
			try:
				os.rmdir(path)
				print("Deleted", path)
			except OSError as e:
				errorfunc(path, e)
		else:
			print("Found", path)

if __name__ == "__main__":
	from argparse import ArgumentParser

	parser = ArgumentParser(description="Delete empty folders. Run multiple times.")
	parser.add_argument("directory", type=is_dir, help="Directory to search")
	parser.add_argument("pattern", nargs="?", help="fnmatch pattern", default="*")
	parser.add_argument("--remove", action="store_true", help="Removed matching empty directories")
	args = parser.parse_args()

	find_empty_dirs(args.directory, args.pattern, args.remove)
