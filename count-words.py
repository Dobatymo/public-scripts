from argparse import ArgumentParser

from genutility.args import is_file
from genutility.nltk import count_words_in_file

parser = ArgumentParser()
parser.add_argument("path", type=is_file)
parser.add_argument("--raw", action="store_true")
args = parser.parse_args()

num = count_words_in_file(args.path)
if args.raw:
    print(num)
else:
    print(f"{args.path} contains {num} words")
