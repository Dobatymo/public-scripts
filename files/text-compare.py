from __future__ import generator_stop

from argparse import ArgumentParser

from genutility.file import textfile_equal

parser = ArgumentParser(description="Compare two text files for equality")
parser.add_argument("file1")
parser.add_argument("file2")
args.parse_args()

if textfile_equal(args.file1, args.file2):
	print("Same files")
	exit(0)
else:
	print("Different files")
	exit(1)
