from __future__ import unicode_literals

from argparse import ArgumentParser

from genutility.filesystem import convert_filenames_to_ascii
from genutility.args import is_dir

parser = ArgumentParser()
parser.add_argument("path", type=is_dir)
args = parser.parse_args()

convert_filenames_to_ascii(args.path)
