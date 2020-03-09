from __future__ import absolute_import, division, print_function, unicode_literals

import argparse
from io import open
from genutility.file import copen, copyfilelike
from genutility.compat.contextlib import nullcontext

parser = argparse.ArgumentParser()

parser.add_argument("-c", "--stdout", action='store_true', help="Compress or decompress to standard output.")

mode = parser.add_mutually_exclusive_group()
mode.add_argument("-d", "--decompress", action='store_true', help='Force decompression.')
mode.add_argument("-z", "--compress", action='store_true', help='The complement to -d: forces compression, regardless of the invocation name.')
mode.add_argument("-t", "--test", action='store_true', help="Check integrity of the specified file(s), but don't decompress them. This really performs a trial decompression and throws away the result.")

parser.add_argument("-f", "--force", action='store_true')
parser.add_argument("-k", "--keep", action='store_true')

compression = parser.add_mutually_exclusive_group()
compression.add_argument("-1", "--fast", action='store_true')
compression.add_argument("-2", action='store_true')
compression.add_argument("-3", action='store_true')
compression.add_argument("-4", action='store_true')
compression.add_argument("-5", action='store_true')
compression.add_argument("-6", action='store_true')
compression.add_argument("-7", action='store_true')
compression.add_argument("-8", action='store_true')
compression.add_argument("-9", "--best", action='store_true', default=True)

parser.add_argument("path")
args = parser.parse_args()

with open(args.path, "rb") as fr:

	if args.stdout:
		context = nullcontext(sys.stdout.buffer)
	else:
		context = copen(args.path + ".bz2", "wb" if args.force else "xb")

	with context as fw:
		copyfilelike(fr, fw)

#if not args.keep:
#	os.remove(args.path)
