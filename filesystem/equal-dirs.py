from __future__ import generator_stop

from argparse import ArgumentParser
from sys import exit

from genutility.args import is_dir
from genutility.filesystem import equal_dirs

parser = ArgumentParser(description="Check if two directories are equal (contain the same files)")
parser.add_argument("directory", type=is_dir, nargs=2, help="Directories to compare")
args = parser.parse_args()

result = equal_dirs(*args.directory)
if result is True:
    print("The directories are equal")
    exit(0)
else:
    print("{} different for {} and {}".format(*result))
    exit(1)
