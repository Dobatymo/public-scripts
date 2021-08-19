from __future__ import generator_stop

if __name__ == "__main__":

	from argparse import ArgumentParser

	from genutility.file import equal_files

	parser = ArgumentParser(description="Compare two text files for equality")
	parser.add_argument("file1")
	parser.add_argument("file2")
	args = parser.parse_args()

	if equal_files(args.file1, args.file2, mode="rt"):
		print("Same files")
		exit(0)
	else:
		print("Different files")
		exit(1)
