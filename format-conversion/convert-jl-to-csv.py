from __future__ import generator_stop

from genutility.json import jl_to_csv

if __name__ == "__main__":
    from argparse import ArgumentParser
    from operator import itemgetter

    parser = ArgumentParser()
    parser.add_argument("field", nargs="+")
    parser.add_argument("--jl", type=str, required=True)
    parser.add_argument("--csv", type=str, required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    getkeys = itemgetter(*args.field)
    mode = "wt" if args.overwrite else "xt"

    jl_to_csv(args.jl, args.csv, getkeys, mode)
