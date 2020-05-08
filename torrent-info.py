import logging, json, sys
from pathlib import Path
from argparse import ArgumentParser
from pprint import pprint

from genutility.args import is_file
from genutility.file import PathOrTextIO
from genutility.json import BuiltinEncoder
from genutility.torrent import read_torrent, torrent_info_hash

def todict(obj):

	if isinstance(obj, dict):
		return {k: todict(v) for k, v in obj.items()}
	if isinstance(obj, list):
		return [todict(v) for v in obj]
	else:
		return obj

def main():
	parser = ArgumentParser(description="Show torrent file information")
	parser.add_argument("path", type=is_file, help="Path to torrent file")
	parser.add_argument("--width", type=int, default=80, help="Terminal width")
	parser.add_argument("--compact", action="store_true", help="Compact output")
	parser.add_argument("--json", action="store_true", help="Use JSON format instead of pretty-print.")
	parser.add_argument("--out", type=Path, help="Out path. If not specified, output will be printed to screen.")
	parser.add_argument("--infohash", action="store_true", help="Calculate torrent info hash instead of showing torrent contents.")
	args = parser.parse_args()

	td = read_torrent(args.path)

	if args.infohash:
		if args.compact or args.json:
			parser.error("Cannot use --compact or --json with --infohash")

		with PathOrTextIO(args.out or sys.stdout, "wt") as fw:
			infohash = torrent_info_hash(td["info"])
			fw.write(infohash)

	else:
		with PathOrTextIO(args.out or sys.stdout, "wt") as fw:

			if args.json:
				return json.dump(td, fw, ensure_ascii=False, indent="\t", sort_keys=False, cls=BuiltinEncoder)

			else:
				del td["info"]["pieces"]

				try:
					for file in td["info"]["files"]:
						file["path"] = "/".join(file["path"])
				except KeyError:
					pass

				pprint(todict(td), fw, width=args.width, compact=args.compact) # , sort_dicts=False

if __name__ == "__main__":
	main()
