from argparse import ArgumentParser

parser = ArgumentParser()
parser.add_argument("inpath", help="Input file")
parser.add_argument("outpath", help="Output file")
parser.add_argument("size", type=int, help="Bytes to read from input file")
args = parser.parse_args()

with open(args.outpath, "wb") as fw, open(args.inpath, "rb") as fr:
    fw.write(fr.read(args.size))  # buffered
