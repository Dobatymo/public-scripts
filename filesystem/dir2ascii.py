import os
import os.path

from genutility.filesystem import PathType, scandir_rec
from unidecode import unidecode


def convert_filenames_to_ascii(path: PathType, follow_symlinks: bool = False, rec: bool = False) -> None:
    """convert all files in `path` to a ascii representation using unidecode"""

    for entry in scandir_rec(path, files=True, dirs=False, rec=rec, follow_symlinks=follow_symlinks):
        filepath = entry.path
        base, name = os.path.split(filepath)
        os.rename(filepath, os.path.join(base, unidecode(name)))


if __name__ == "__main__":
    from argparse import ArgumentParser

    from genutility.args import is_dir

    parser = ArgumentParser()
    parser.add_argument("path", type=is_dir)
    parser.add_argument("-r", "--recursive", action="store_true")
    parser.add_argument("-s", "--follow-symlinks", action="store_true")
    args = parser.parse_args()

    convert_filenames_to_ascii(args.path, args.follow_symlinks, args.recursive)
