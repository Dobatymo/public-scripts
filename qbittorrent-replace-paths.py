from __future__ import absolute_import, division, print_function, unicode_literals

import bencode

def replace_directory(dirpath, from_s, to_s):
	# type: (Path, str, str) -> None

	for filepath in dirpath.glob("*.fastresume"):
		bb = bencode.bread(filepath)
		try:
			save_a = bb["qBt-savePath"]
			save_b = bb["save_path"]
			assert save_a == save_b
			bb["qBt-savePath"] = save_a.replace(from_s, to_s)
			bb["save_path"] = save_b.replace(from_s, to_s)
		except KeyError: # magnet link without metadata
			bb["qBt-savePath"] = bb["qBt-savePath"].replace(from_s, to_s)
		bencode.bwrite(bb, filepath)

if __name__ == "__main__":

	from argparse import ArgumentParser
	from genutility.args import is_dir

	parser = ArgumentParser("Replace save locations for files in QBittorrent appdata.\nWarning: Close QBittorrent before running this script.")
	parser.add_argument("BT_backup", metavar="path", type=is_dir)
	parser.add_argument("old", help="Path substring to be replaced. For example 'C:'.")
	parser.add_argument("new", help="New path substring. For example 'D:'.")
	args = parser.parse_args()

	replace_directory(args.path, args.old, args.new)
