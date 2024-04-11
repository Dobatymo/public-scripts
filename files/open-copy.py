import os.path
import shutil
import subprocess
import tempfile
from argparse import ArgumentParser

from genutility.stdio import errorquit, waitquit

if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("path")
    args = parser.parse_args()

    try:
        tmpName = "open-copy.tmp"
        tmpPath = os.path.join(tempfile.gettempdir(), tempfile.gettempprefix() + tmpName)
        shutil.copy(args.path, tmpPath)
        errorquit(subprocess.call(tmpPath, shell=True))
    except Exception as e:
        waitquit(exception=e)
