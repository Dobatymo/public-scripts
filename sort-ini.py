# /// script
# requires-python = ">=3.8"
# dependencies = [
#     "genutility[args,config]",
# ]
# ///
from argparse import ArgumentParser

from genutility.args import future_file, is_file
from genutility.config import sort_config


def main() -> None:
    parser = ArgumentParser()
    parser.add_argument("inpath", type=is_file)
    parser.add_argument("outpath", type=future_file)
    args = parser.parse_args()

    sort_config(args.inpath, args.outpath)


if __name__ == "__main__":
    main()
