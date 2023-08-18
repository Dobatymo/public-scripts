import logging
from argparse import ArgumentParser
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory

from genutility.os import realpath


def relink_base(basepath: Path, newbasepath: Path) -> None:
    # permissions test
    try:
        with NamedTemporaryFile() as tf:
            with TemporaryDirectory() as tmpdirname:
                (Path(tmpdirname) / "permissions-test.link").symlink_to(tf.name)
    except OSError as e:  # [WinError 1314]
        logging.error("Missing symlink permissions (OSError: %s)", e)
        return

    for path in basepath.iterdir():
        if path.is_symlink():
            linkpath = Path(realpath(path))
            if not linkpath.exists():
                newpath = newbasepath / linkpath.name
                if newpath.exists():
                    logging.warning("Invalid symlink: %s->%s (replacing with ->%s)", path, linkpath, newpath)
                    path.unlink()
                    path.symlink_to(newpath)
                else:
                    logging.warning("Invalid symlink: %s->%s (could not find replacement)", path, linkpath)


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("basepath", type=Path, help="Path with symlinks")
    parser.add_argument("newbasepath", type=Path, help="New base path to use for symlinks")
    args = parser.parse_args()

    relink_base(args.basepath, args.newbasepath)
