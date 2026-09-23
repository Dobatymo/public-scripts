from os import fspath
from pathlib import Path
from typing import FrozenSet

from genutility.args import is_dir, is_dir_nofollow, is_file, is_file_or_new_nofollow, suffix
from genutility.file import read_file


def esc(s):
    return s.replace("\\", "\\\\")


def main(
    inpath: Path,
    tplpath: Path,
    outpath: Path,
    outsuffix: str,
    suffixes: FrozenSet[str] = frozenset(),
    overwrite: bool = False,
) -> None:
    tpl = read_file(tplpath, "rt")
    assert isinstance(tpl, str)  # for mypy

    paths = [path for path in inpath.iterdir() if path.suffix in suffixes]
    output_paths = [outpath / path.with_suffix(outsuffix).name for path in paths]
    for path, output_path in zip(paths, output_paths):
        output_exists = output_path.exists()
        is_file_or_new_nofollow(output_path)
        if output_exists and output_path.samefile(path):
            raise ValueError(f"input and output paths refer to the same file: {path}")
        if output_exists and not overwrite:
            raise FileExistsError(f"{output_path} already exists; use --overwrite to replace it")

    for i, (path, output_path) in enumerate(zip(paths, output_paths)):
        mode = "w" if overwrite else "x"
        with output_path.open(mode) as fw:
            fw.write(
                tpl.format(
                    filename=path.name,
                    efilename=esc(path.name),
                    path=fspath(path),
                    epath=esc(fspath(path)),
                    stem=path.parent / path.stem,  # path without suffix
                    i=i,
                )
            )


if __name__ == "__main__":
    from argparse import ArgumentParser, ArgumentTypeError

    parser = ArgumentParser(
        description="""Create a file for file in directory based on a template file.
The template can contain the following Python format style placeholders: {filename}, {efilename}, {path}, {epath}, {stem}, {i}.
For example this can be used to create one AviSynth script file per video file."""
    )
    parser.add_argument("inpath", type=is_dir, help="Directory with files.")
    parser.add_argument("tplpath", type=is_file, help="Path to template file.")
    parser.add_argument("outsuffix", type=suffix, help="Suffix of created files.")
    parser.add_argument(
        "--outpath", type=is_dir_nofollow, default=Path("."), help="Directory where created files are placed."
    )
    parser.add_argument(
        "--suffixes", metavar="SUFFIX", nargs="+", default=[], type=suffix, help="Extensions to filter for."
    )
    parser.add_argument("--overwrite", action="store_true", help="Overwrite generated files")
    args = parser.parse_args()

    try:
        main(args.inpath, args.tplpath, args.outpath, args.outsuffix, frozenset(args.suffixes), args.overwrite)
    except (ArgumentTypeError, FileExistsError, ValueError) as e:
        parser.error(str(e))
