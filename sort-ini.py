# /// script
# requires-python = ">=3.8"
# dependencies = [
#     "genutility[args,config]>=0.0.123",
# ]
# ///
from argparse import ArgumentParser

from genutility.args import is_file, new_path
from genutility.config import sort_config


def main() -> None:
    parser = ArgumentParser()
    parser.add_argument("inpath", type=is_file)
    parser.add_argument("outpath", type=new_path)
    args = parser.parse_args()
    args.outpath.parent.mkdir(parents=True, exist_ok=True)

    sort_config(args.inpath, args.outpath)


if __name__ == "__main__":
    main()
