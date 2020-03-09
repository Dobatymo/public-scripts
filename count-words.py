from __future__ import print_function, unicode_literals

from genutility.nltk import count_words_in_file

from argparse import ArgumentParser
parser = ArgumentParser()
parser.add_argument("path")
parser.add_argument("--raw", action="store_true")
args = parser.parse_args()

num = count_words_in_file(args.path)
if args.raw:
	print(num)
else:
	print("{} contains {} words".format(args.path, num))
