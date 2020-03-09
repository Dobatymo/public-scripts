from __future__ import absolute_import, division, print_function, unicode_literals

from future.utils import listvalues, viewitems

from genutility.numpy import shannon_entropy
from genutility.math import discrete_distribution
from genutility.file import file_byte_reader

def file_entropy(filename):
	d, s = discrete_distribution((b[0] for b in file_byte_reader(filename, 1024, 1)))
	p = {k:v/s for k, v in viewitems(d)}
	return shannon_entropy(listvalues(p), 256)

if __name__ == "__main__":
	from argparse import ArgumentParser
	parser = ArgumentParser()
	parser.add_argument("path")
	args = parser.parse_args()

	print(file_entropy(args.path))
