from __future__ import generator_stop

from argparse import ArgumentParser

from genutility.file import is_all_byte
from genutility.filesystem import scandir_rec

parser = ArgumentParser()
parser.add_argument("path")
args = parser.parse_args()

for entry in scandir_rec(args.path, dirs=False, files=True):
	if entry.stat().st_size != 0:
		with open(entry.path, "rb") as fr:
			if is_all_byte(fr, b"\x00"):
				print(entry.path)
