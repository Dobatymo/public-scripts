from __future__ import absolute_import, division, print_function, unicode_literals

from argparse import ArgumentParser

parser = ArgumentParser()
parser.add_argument("size", type=int, help="File size in MB")
parser.add_argument("path", type=str)
args = parser.parse_args()

bytes = b"\0"*1024*1024

with open(args.path, "wb") as fw:
	for i in range(args.size):
		fw.write(bytes)
