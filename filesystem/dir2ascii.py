from __future__ import unicode_literals

import os, os.path

from unidecode import unidecode
from genutility.filesystem import scandir_rec

def convert_filenames_to_ascii(path, follow_symlinks=False, rec=False):
	# type: (PathType, bool, bool) -> None
	""" convert all files in `path` to a ascii representation using unidecode """

	for entry in scandir_rec(path, files=True, dirs=False, rec=rec, follow_symlinks=follow_symlinks):
		filepath = entry.path
		base, name = os.path.split(filepath)
		os.rename(filepath, os.path.join(base, unidecode(name)))

if __name__ == "__main__":

	from argparse import ArgumentParser
	from genutility.args import is_dir

	parser = ArgumentParser()
	parser.add_argument("path", type=is_dir)
	parser.add_argument("-r", "--recursive", action="store_true")
	parser.add_argument("-s", "--follow-symlinks", action="store_true")
	args = parser.parse_args()

	convert_filenames_to_ascii(args.path, args.follow_symlinks, args.recursive)
