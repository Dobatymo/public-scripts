from __future__ import absolute_import, division, print_function, unicode_literals

from future.utils import iteritems
import os.path
from collections import defaultdict

import numpy as np
from metrohash import MetroHash128
from PIL import Image

from genutility.hash import hash_file
from genutility.filesystem import scandir_rec, fileextensions
from genutility.metrics import hamming_distance
from genutility.iter import progress
from genutility.metrictree import BKTree
from genutility.fingerprinting import phash_blockmean

def metrohash(path):
	return hash_file(path, MetroHash128).digest()

IGNORE_DIRNAMES = {".git"}

def iter_size_path(dirs):
	for dir in dirs:
		for entry in scandir_rec(dir, dirs=True, follow_symlinks=False, allow_skip=True):
			if entry.is_dir() and entry.name in IGNORE_DIRNAMES:
				print("Skipped:", entry.path)
				entry.follow = False
			elif entry.is_file():
				filesize = entry.stat().st_size
				yield filesize, entry.path

def image_hash_tree(dirs, exts=None):
	tree = BKTree(hamming_distance)
	map = defaultdict(list)
	exts = exts or fileextensions.images

	for dir in dirs:
		for entry in scandir_rec(dir, dirs=True, follow_symlinks=False, allow_skip=True):
			if entry.is_dir() and entry.name in IGNORE_DIRNAMES:
				print("Skipped:", entry.path)
				entry.follow = False
			elif entry.is_file():
				_, ext = os.path.splitext(entry.name)
				if "." + ext.lower() in exts:
					try:
						img = Image.open(entry.path)
					except OSError:
						print("Cannot open:", entry.path)
					else:
						hash = phash_blockmean(img)
						tree.add(hash)
						map[hash].append(entry.path)
	return tree, map

def iter_size_hashes(dups):
	for size, group in iteritems(dups):
		hashes = defaultdict(list)
		for path in group:
			try:
				hash = metrohash(path)
			except PermissionError:
				print("Permission denied:", path)
			except FileNotFoundError:
				print("File not found:", path)
			else:
				hashes[hash].append(path)
		yield size, hashes

def dupgroups(dirs):
	dups = defaultdict(list)

	print("Collecting files")
	for size, path in progress(iter_size_path(dirs)):
		dups[size].append(path)

	print("Filtering files based on size")
	dups = {k: v for k, v in iteritems(dups) if len(v) > 1}

	print("Calculating hash groups")
	for size, hashes in progress(iter_size_hashes(dups), length=len(dups)):
		dups[size] = hashes

	print("Filtering files based on hashes")
	newdups = {}
	for size, sizegroup in iteritems(dups):
		for hash, hashgroup in iteritems(sizegroup):
			if len(hashgroup) > 1:
				newdups[(size, hash)] = hashgroup

	return newdups

if __name__ == "__main__":

	import csv
	from io import open
	from argparse import ArgumentParser

	parser = ArgumentParser()
	parser.add_argument("directories", nargs="+", help="Directory to search")
	args = parser.parse_args()

	'''
	with open("dups.csv", "x", encoding="utf-8", newline="") as csvfile:
		csvwriter = csv.writer(csvfile)
		csvwriter.writerow(["size", "hash", "path"])
		for (size, hash), paths in iteritems(dupgroups(dirs)):
			for path in paths:
				csvwriter.writerow([size, hash.hex(), path])
	'''

	tree, map = image_hash_tree(args.directories)
	for d in range(100):
		print(d)
		for group in tree.find_by_distance(d):
			files = []
			for hash in group:
				files.extend(map[hash])
			print(files)
		print("---")
