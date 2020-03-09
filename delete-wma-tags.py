from __future__ import absolute_import, division, print_function, unicode_literals

from mutagen.asf import ASF

from genutility.filesystem import scandir_rec
from genutility.stdio import waitcontinue

def delete_tags_from_wma(path, tags):
	# type: (str, Iterable[str]) -> bool

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

	from genutility.ops import logical_xor
	from argparse import ArgumentParser
	parser = ArgumentParser()
	parser.add_argument("path")
	parser.add_argument("--tags", nargs="*")
	parser.add_argument("--napster", action="store_true")
	args = parser.parse_args()

	assert logical_xor(args.napster, args.tags), "Either --napster or --tags must be specified"

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
