from __future__ import absolute_import, division, print_function, unicode_literals

from genutility.hash import hash_dir_str

if __name__ == "__main__":

	from argparse import ArgumentParser
	from genutility.args import is_dir
	parser = ArgumentParser(description="calculate hash of all files in directory combined")
	parser.add_argument("path", type=is_dir, help="input directory")
	args = parser.parse_args()

	for line in hash_dir_str(args.path, include_names=True):
		print(line)
