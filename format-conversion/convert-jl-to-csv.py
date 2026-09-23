from genutility.json import jl_to_csv

if __name__ == "__main__":
    from argparse import ArgumentParser
    from operator import itemgetter

    from genutility.args import is_file, is_file_or_new_nofollow

    parser = ArgumentParser()
    parser.add_argument("field", nargs="+")
    parser.add_argument("--jl", type=is_file, required=True)
    parser.add_argument("--csv", type=is_file_or_new_nofollow, required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    output_exists = args.csv.exists()
    if output_exists and args.csv.samefile(args.jl):
        parser.error("--jl and --csv refer to the same file")
    if output_exists and not args.overwrite:
        parser.error(f"{args.csv} already exists; use --overwrite to replace it")

    getkeys = itemgetter(*args.field)
    mode = "wt" if args.overwrite else "xt"

    jl_to_csv(args.jl, args.csv, getkeys, mode)
