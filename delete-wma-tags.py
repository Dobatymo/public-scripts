from __future__ import generator_stop

from typing import Iterable

from genutility.filesystem import scandir_rec
from genutility.stdio import waitcontinue
from mutagen.asf import ASF


def delete_tags_from_wma(path: str, tags: Iterable[str]) -> bool:

	asf = ASF(path)
	modified = False

	if asf.tags is not None:
		for tag in tags:
			try:
				asf.tags.pop(tag)
				modified = True
			except KeyError:
				pass
		if modified:
			asf.save()

	return modified

if __name__ == "__main__":

	from argparse import ArgumentParser

	from genutility.args import is_dir
	from genutility.ops import logical_xor

	parser = ArgumentParser()
	parser.add_argument("path", type=is_dir)
	parser.add_argument("--tags", nargs="*")
	parser.add_argument("--napster", action="store_true")
	args = parser.parse_args()

	if not logical_xor(args.napster, args.tags):
		parser.error("Either --napster or --tags must be specified")

	if args.napster:
		tags = ["WM/NapsterHeader"]
	else:
		tags = args.tags

	for entry in scandir_rec(args.path, dirs=False, files=True):
		if entry.path.endswith(".wma"):
			try:
				if delete_tags_from_wma(entry.path, tags):
					print("Deleted tags from {}".format(entry.path))
			except Exception as e:
				waitcontinue(entry.path, exception=e)
