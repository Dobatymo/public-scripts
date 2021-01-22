from __future__ import generator_stop

from genutility.file import iterfilelike
from genutility.iter import iter_equal


def compare_content(a, b, size, a_seek=0, b_seek=0):
	with open(a, "rb") as fa, open(b, "rb") as fb:
		fa.seek(a_seek)
		fb.seek(b_seek)
		return iter_equal(iterfilelike(fa, size), iterfilelike(fb, size))

if __name__ == "__main__":
	from argparse import ArgumentParser

	parser = ArgumentParser()
	parser.add_argument("file1")
	parser.add_argument("file2")
	parser.add_argument("seek1", type=int, default=0)
	parser.add_argument("seek2", type=int, default=0)
	parser.add_argument("maxsize", type=int)
	args = parser.parse_args()

	print(compare_content(args.file1, args.file2, args.maxsize, args.seek1, args.seek2))
