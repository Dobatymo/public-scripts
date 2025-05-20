import json
import logging
import os
import sys
from argparse import ArgumentDefaultsHelpFormatter, ArgumentParser, Namespace
from collections import defaultdict
from concurrent.futures import Future, ProcessPoolExecutor
from functools import partial
from itertools import islice
from math import ceil
from multiprocessing import freeze_support
from pathlib import Path
from shutil import get_terminal_size
from typing import Any, Collection, Dict, List, Optional, Set, Tuple, Union

import cv2
import matplotlib.pyplot as plt
import numpy as np
from genutility.cv import iter_video
from genutility.json import json_lines
from genutility.tqdm import TqdmMultiprocessing, TqdmProcess
from more_itertools import zip_equal
from skimage.metrics import mean_squared_error, peak_signal_noise_ratio, structural_similarity

logger = logging.getLogger(__name__)


def sum_abs_diff(image1: np.ndarray, image2: np.ndarray) -> int:
    return np.sum(np.abs(image1 - image2)).item()


metric_funcs = {
    "mse": mean_squared_error,
    "ssim": partial(structural_similarity, gradient=False, full=False),
    "psnr": peak_signal_noise_ratio,
    "sad": sum_abs_diff,
}

agg_funcs = {
    "mse": max,
    "ssim": min,
    "psnr": min,
    "sad": max,
}

colorspace = {
    "mse": "gray",
    "ssim": "gray",
    "psnr": "gray",
    "sad": "color",
}


def process_img(image1: np.ndarray, image2: np.ndarray, metric: str) -> float:
    func = metric_funcs[metric]
    cs = colorspace[metric]

    if cs == "gray":
        image1 = cv2.cvtColor(image1, cv2.COLOR_BGR2GRAY)
        image2 = cv2.cvtColor(image2, cv2.COLOR_BGR2GRAY)
    elif cs == "color":
        pass
    else:
        raise ValueError(f"Invalid color space: {cs}")

    return func(image1, image2)


def limit_desc(desc: str, reserved: int = 40) -> str:
    terminal_width = get_terminal_size().columns
    max_length = terminal_width - reserved

    if len(desc) > max_length:
        return desc[: max_length - 3] + "..."
    else:
        return desc


def process(
    path1: Path,
    path2: Path,
    metric: str,
    progress: TqdmProcess,
    limit: Optional[int] = None,
) -> List[float]:
    scores: List[float] = []
    it = zip_equal(iter_video(path1), iter_video(path2))

    for image1, image2 in progress.track(islice(it, limit), desc=limit_desc(path1.name), total=limit, mininterval=0.5):
        score = process_img(image1, image2, metric)
        scores.append(score)

    return scores


def process_paths(
    pairs: Collection[Tuple[Path, Path]],
    metric: str,
    limit: Optional[int],
    out: Union[str, os.PathLike],
    workers: Optional[int],
) -> None:
    with json_lines.from_path(out, "wt") as fw:
        if not pairs:
            return

        with TqdmMultiprocessing() as progress:
            with ProcessPoolExecutor(
                max_workers=min(workers, len(pairs)), initializer=progress.initializer, initargs=progress.initargs
            ) as executor:
                futures: Dict[Future, Dict[str, Any]] = {}

                for path1, path2 in pairs:
                    future = executor.submit(process, path1, path2, metric, progress, limit)
                    futures[future] = {
                        "metric": metric,
                        "path1": os.fspath(path1),
                        "path2": os.fspath(path2),
                    }

                for future, meta in futures.items():
                    try:
                        meta["scores"] = future.result()
                        meta["error"] = None
                    except Exception as e:
                        logger.error("Failed to compare %s and %s: %s", path1, path2, e)
                        meta["scores"] = None
                        meta["error"] = str(e)
                    fw.write(meta)
                    fw.flush()


def unique_filenames_by_stem(path: Path) -> Set[str]:
    d = defaultdict(list)
    for p in path.iterdir():
        if p.is_file():
            d[p.stem].append(p.name)
    for k, values in d.items():
        if len(values) > 1:
            raise ValueError(f"Ambigious name stem: {k}")
    return {values[0] for k, values in d.items()}


def intersect_files(path1: Path, filesnames1: Set[str], path2: Path, filesnames2: Set[str]) -> List[Tuple[Path, Path]]:
    intersection = filesnames1 & filesnames2
    if logger.isEnabledFor(logging.DEBUG):
        for filename in sorted(filesnames1 - intersection):
            logger.debug("Skipping %s", path1 / filename)
        for filename in sorted(filesnames2 - intersection):
            logger.debug("Skipping %s", path2 / filename)
    return [((path1 / name), (path2 / name)) for name in sorted(intersection)]


def action_compare(args: Namespace) -> int:
    if args.path1.is_file() and args.path2.is_file():
        pairs = [(args.path1, args.path2)]
    elif args.path1.is_dir() and args.path2.is_dir():
        if args.ignore_ext:
            a = unique_filenames_by_stem(args.path1)
            b = unique_filenames_by_stem(args.path2)
        else:
            a = {p.name for p in args.path1.iterdir() if p.is_file()}
            b = {p.name for p in args.path2.iterdir() if p.is_file()}
        pairs = intersect_files(args.path1, a, args.path2, b)
    else:
        raise ValueError("Cannot compare files and directories")

    process_paths(pairs, args.metric, args.limit, args.out, args.workers)
    return 0


def action_analyze(args: Namespace) -> int:
    try:
        with json_lines.from_path(args.path, "rt") as fr:
            for obj in fr:
                filename = Path(obj["path1"]).name
                metric = obj["metric"]
                scores = obj["scores"]
                func = agg_funcs[metric]
                funcname = func.__name__
                if scores is None:
                    error = obj["error"]
                    print(f"{filename}: {error}")
                    continue

                agg_val = func(scores)

                title = f"{filename}: {funcname}({metric})={agg_val}"
                print(title)

                if args.plot:
                    x_axis = list(range(0, len(scores)))

                    plt.title(title)
                    plt.xlabel("frames")
                    plt.ylabel(metric)
                    plt.scatter(x_axis, scores, s=10, c="red")
                    plt.show()
    except json.JSONDecodeError:
        logger.error("Failed to parse JSON Lines file")
        return 1

    return 0


def main():
    DEFAULT_WORKERS = ceil((os.cpu_count() or 1) / 2)
    DEFAULT_METRIC = "mse"

    parser = ArgumentParser(formatter_class=ArgumentDefaultsHelpFormatter)
    parser.add_argument("-v", "--verbose", action="store_true", help="Show debug information")
    subparsers = parser.add_subparsers(dest="action", required=True)

    parser_a = subparsers.add_parser("compare", help="Calculate video quality metric")
    parser_a.set_defaults(func=action_compare)
    parser_a.add_argument("path1", type=Path, help="First input directory")
    parser_a.add_argument("path2", type=Path, help="Second input directory")
    parser_a.add_argument(
        "-i",
        "--ignore-ext",
        action="store_true",
        help="Ignore file extension when matching files from the two directory paths. Otherwise only files with the same name and extensions are compared.",
    )
    parser_a.add_argument("--limit", metavar="N", default=None, type=int, help="Limit comparison the first N frames")
    parser_a.add_argument(
        "--workers",
        metavar="N",
        type=int,
        default=DEFAULT_WORKERS,
        help="Number of concurrent processes",
    )
    parser_a.add_argument(
        "--metric",
        default=DEFAULT_METRIC,
        choices=metric_funcs.keys(),
        help="Image quality metric",
    )
    parser_a.add_argument("--out", type=Path, required=True, help="JSON Lines output filename")

    parser_b = subparsers.add_parser("analyze", help="Show video quality metric")
    parser_b.set_defaults(func=action_analyze)
    parser_b.add_argument("path", type=Path, help="JSON Lines input file")
    parser_b.add_argument("-p", "--plot", action="store_true", help="Display plot")

    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    try:
        sys.exit(args.func(args))
    except KeyboardInterrupt:
        logger.warning("Interrupted by user. Exiting.")


if __name__ == "__main__":
    freeze_support()
    main()
