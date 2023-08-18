from __future__ import generator_stop

import logging

from genutility.filesystem import iter_links


def print_links(path) -> None:
    for p in iter_links(path):
        print(p)


if __name__ == "__main__":
    from argparse import ArgumentParser

    logging.basicConfig(level=logging.DEBUG)

    DEFAULT_IGNORE = [".!ut", ".!qB"]

    parser = ArgumentParser()
    parser.add_argument("path", help="Input path to search recursively.")
    args = parser.parse_args()

    print("Symlinks or junctions")
    print_links(args.path)
