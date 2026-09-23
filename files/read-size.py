from argparse import ArgumentParser

from genutility.args import is_file, new_path, non_negative_int

parser = ArgumentParser()
parser.add_argument("inpath", type=is_file, help="Input file")
parser.add_argument("outpath", type=new_path, help="Output file")
parser.add_argument("size", type=non_negative_int, help="Bytes to read from input file")
args = parser.parse_args()

with args.inpath.open("rb") as fr, args.outpath.open("xb") as fw:
    fw.write(fr.read(args.size))  # buffered
