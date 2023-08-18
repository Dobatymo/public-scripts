from __future__ import generator_stop

from argparse import ArgumentParser
from sys import exit

from genutility.args import is_dir
from genutility.filesystem import equal_dirs_iter

if __name__ == "__main__":
    parser = ArgumentParser(description="Check if two directories are equal (contain the same files)")
    parser.add_argument("directory", type=is_dir, nargs=2, help="Directories to compare")
    args = parser.parse_args()

    for what, path1, path2 in equal_dirs_iter(*args.directory):
        print(f"{what} different for {path1} and {path2}")
        exit(1)
    else:
        print("The directories are equal")
        exit(0)
