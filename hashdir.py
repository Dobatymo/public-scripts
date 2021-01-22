from __future__ import generator_stop

from os import fspath

from genutility.hash import hash_dir_str

if __name__ == "__main__":

	from argparse import ArgumentParser

	from genutility.args import is_dir
	parser = ArgumentParser(description="calculate hash of all files in directory combined")
	parser.add_argument("path", type=is_dir, help="input directory")
	parser.add_argument("--no-names", action="store_true", help="Don't include filenames in hash calculation")
	args = parser.parse_args()

	for line in hash_dir_str(fspath(args.path), include_names=not args.no_names):
		print(line)
