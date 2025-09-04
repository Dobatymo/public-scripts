# /// script
# requires-python = ">=3.8"
# dependencies = [
#     "matplotlib",
#     "scikit-image",
#     "tqdm",
#     "numpy",
#     "more-itertools",
#     "genutility[json,tqdm,videofile]>=0.0.119",
#     "opencv-python",
# ]
#
# [tool.uv.sources]
# genutility = { path = "../public-libs/genutility" }
# ///
import json
import logging
import os
import sys
from argparse import ArgumentDefaultsHelpFormatter, ArgumentParser, Namespace
from collections import defaultdict
from concurrent.futures import Future, ProcessPoolExecutor
from functools import partial
from itertools import chain, compress, count, islice, zip_longest
from math import ceil
from multiprocessing import freeze_support
from pathlib import Path
from shutil import get_terminal_size
from typing import Any, Collection, Dict, Iterable, Iterator, List, Optional, Set, Tuple, TypeVar, Union

import cv2
import matplotlib.pyplot as plt
import numpy as np
from genutility.json import json_lines
from genutility.numpy import fill_gaps, remove_spikes
from genutility.tqdm import Progress, TqdmMultiprocessing, TqdmProcess
from genutility.videofile import CvVideo
from skimage.metrics import mean_squared_error, peak_signal_noise_ratio, structural_similarity

logger = logging.getLogger(__name__)

T = TypeVar("T")


def sum_abs_diff(image1: np.ndarray, image2: np.ndarray) -> int:
    return np.sum(np.abs(image1 - image2)).item()


metric_funcs = {
    "mse": mean_squared_error,
    "ssim": partial(structural_similarity, gradient=False, full=False),
    "psnr": peak_signal_noise_ratio,
    "sad": sum_abs_diff,
}


def ssim_diff(im1: np.ndarray, im2: np.ndarray) -> np.ndarray:
    _mssim, S = structural_similarity(im1, im2, gradient=False, full=True)
    assert len(S.shape) == 2, f"S.shape == {S.shape} (len(S.shape) != 2)"
    return (S * 256).astype(np.uint8)


visual_diff_funcs = {
    "absdiff": cv2.absdiff,
    "ssim": ssim_diff,
}

agg_funcs = {
    "mse": np.max,
    "ssim": np.min,
    "psnr": np.min,
    "sad": np.max,
}

colorspace = {
    "mse": "gray",
    "ssim": "gray",
    "psnr": "gray",
    "sad": "color",
}

colorspace_diff = {
    "absdiff": "color",
    "ssim": "gray",
}


def denoise(arr: np.ndarray) -> None:
    remove_spikes(arr)
    fill_gaps(arr)


def process_img(
    image1: np.ndarray,
    image2: np.ndarray,
    metric: str,
    size1: Optional[Tuple[int, int]] = None,
    size2: Optional[Tuple[int, int]] = None,
) -> float:
    func = metric_funcs[metric]
    cs = colorspace[metric]

    if cs == "gray":
        image1 = cv2.cvtColor(image1, cv2.COLOR_BGR2GRAY)
        image2 = cv2.cvtColor(image2, cv2.COLOR_BGR2GRAY)
    elif cs == "color":
        pass
    else:
        raise ValueError(f"Invalid color space: {cs}")

    if size1 is not None:
        image1 = cv2.resize(image1, size1, interpolation=cv2.INTER_CUBIC)
    if size2 is not None:
        image2 = cv2.resize(image2, size2, interpolation=cv2.INTER_CUBIC)

    return func(image1, image2)


def process_img_diff(
    image1: np.ndarray,
    image2: np.ndarray,
    diff: str,
    size1: Optional[Tuple[int, int]] = None,
    size2: Optional[Tuple[int, int]] = None,
) -> np.ndarray:
    func = visual_diff_funcs[diff]
    cs = colorspace_diff[diff]

    if cs == "gray":
        image1 = cv2.cvtColor(image1, cv2.COLOR_BGR2GRAY)
        image2 = cv2.cvtColor(image2, cv2.COLOR_BGR2GRAY)
    elif cs == "color":
        pass
    else:
        raise ValueError(f"Invalid color space: {cs}")

    if size1 is not None:
        image1 = cv2.resize(image1, size1, interpolation=cv2.INTER_CUBIC)
    if size2 is not None:
        image2 = cv2.resize(image2, size2, interpolation=cv2.INTER_CUBIC)

    out = func(image1, image2)

    if cs == "gray":
        out = cv2.cvtColor(out, cv2.COLOR_GRAY2BGR)
    elif cs == "color":
        pass
    else:
        raise ValueError(f"Invalid color space: {cs}")

    return out


def limit_desc(desc: str, reserved: int = 40) -> str:
    terminal_width = get_terminal_size().columns
    max_length = terminal_width - reserved

    if len(desc) > max_length:
        return desc[: max_length - 3] + "..."
    else:
        return desc


def skip_every_nth(iterable: Iterable[T], n: int) -> Iterator[T]:
    """Skip every n-th element in iterable"""

    if n == 0:
        return iterable

    selector = (i % n != 0 for i in count(1))
    return compress(iterable, selector)


assert list(skip_every_nth(range(10), 0)) == list(range(10))
assert list(skip_every_nth(range(10), 1)) == []
assert list(skip_every_nth(range(10), 2)) == [0, 2, 4, 6, 8]
assert list(skip_every_nth(range(10), 3)) == [0, 1, 3, 4, 6, 7, 9]


def log_ignored(ignored1: int, ignored2: int) -> None:
    if ignored1 == 0 and ignored2 == 0:
        pass  # all good
    elif (ignored1 > 0 and ignored2 == 0) or (ignored1 == 0 and ignored2 > 0):
        logger.warning(
            "Ignored %d frames at the end of video 1 and ignored %d frames at the end of video 2", ignored1, ignored2
        )
    else:
        raise RuntimeError(f"ignored1 = {ignored1}, ignored2 = {ignored2}")


def process(
    path1: Path,
    path2: Path,
    metric: str,
    progress: TqdmProcess,
    limit: Optional[int] = None,
    size1: Optional[Tuple[int, int]] = None,
    size2: Optional[Tuple[int, int]] = None,
    skip_nth_1: int = 0,
    skip_nth_2: int = 0,
    skip1: int = 0,
    skip2: int = 0,
) -> Tuple[List[float], List[float], List[float]]:
    scores: List[float] = []
    times1: List[float] = []
    times2: List[float] = []

    ignored1 = 0
    ignored2 = 0

    with CvVideo(path1) as a, CvVideo(path2) as b:
        a = skip_every_nth(islice(a.iterall(native=True), skip1, None), skip_nth_1)
        b = skip_every_nth(islice(b.iterall(native=True), skip2, None), skip_nth_2)
        it = zip_longest(a, b)

        for time_image_1, time_image_2 in progress.track(
            islice(it, limit), desc=limit_desc(path1.name), total=limit, mininterval=0.5
        ):
            if time_image_1 is None:
                ignored2 += 1
                continue
            if time_image_2 is None:
                ignored1 += 1
                continue

            (time1, image1), (time2, image2) = time_image_1, time_image_2
            if skip1 == 0 and skip2 == 0:
                assert abs(time1 - time2) < 0.000001, f"Frame time difference too large: {time1} vs {time2}"
            score = process_img(image1, image2, metric, size1, size2)
            scores.append(score)
            times1.append(time1)
            times2.append(time2)

    log_ignored(ignored1, ignored2)

    return scores, times1, times2


def process_paths(
    pairs: Collection[Tuple[Path, Path]],
    metric: str,
    limit: Optional[int],
    out: Union[str, os.PathLike],
    workers: Optional[int],
    size1: Optional[Tuple[int, int]] = None,
    size2: Optional[Tuple[int, int]] = None,
    skip_nth_1: int = 0,
    skip_nth_2: int = 0,
    skip1: int = 0,
    skip2: int = 0,
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
                    future = executor.submit(
                        process,
                        path1,
                        path2,
                        metric,
                        progress,
                        limit,
                        size1,
                        size2,
                        skip_nth_1,
                        skip_nth_2,
                        skip1,
                        skip2,
                    )
                    futures[future] = {
                        "metric": metric,
                        "path1": os.fspath(path1),
                        "path2": os.fspath(path2),
                        "limit": limit,
                        "size1": size1,
                        "size2": size2,
                        "skip_nth_1": skip_nth_1,
                        "skip_nth_2": skip_nth_2,
                        "skip1": skip1,
                        "skip2": skip2,
                    }

                for future, meta in futures.items():
                    try:
                        scores, times1, times2 = future.result()
                        meta["scores"] = scores
                        meta["times1"] = times1
                        meta["times2"] = times2
                        meta["error"] = None
                    except Exception as e:
                        logger.error("Failed to compare %s and %s: %s", path1, path2, e)
                        meta["scores"] = None
                        meta["times1"] = None
                        meta["times2"] = None
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

    if len(args.path_pairs) % 2 != 0:
        raise ValueError("Path pairs list length must be even")

    for i in range(0, len(args.path_pairs), 2):
        p1 = args.path_pairs[i]
        p2 = args.path_pairs[i + 1]

        if not p1.is_file() or not p2.is_file():
            raise ValueError("Path pairs must be files")

        pairs.append((p1, p2))

    process_paths(
        pairs,
        args.metric,
        args.limit,
        args.out,
        args.workers,
        args.size1,
        args.size2,
        args.skip_nth_1,
        args.skip_nth_2,
        args.skip1,
        args.skip2,
    )
    return 0


def export_differences_video(
    path1: Path,
    path2: Path,
    frame_mask: np.ndarray,
    outpath: Path,
    diff: str,
    fps: float = 30.0,
    size1: Optional[Tuple[int, int]] = None,
    size2: Optional[Tuple[int, int]] = None,
    skip_nth_1: int = 0,
    skip_nth_2: int = 0,
    skip1: int = 0,
    skip2: int = 0,
) -> None:
    import av

    progress = Progress()
    with CvVideo(path1) as a, CvVideo(path2) as b:
        a = skip_every_nth(islice(a.iterall(native=True), skip1, None), skip_nth_1)
        b = skip_every_nth(islice(b.iterall(native=True), skip2, None), skip_nth_2)
        it = compress(
            zip_longest(a, b),
            progress.track(frame_mask, description="Reading frames"),
        )
        total = np.count_nonzero(frame_mask)

        (time1, image1), (time2, image2) = next(it)
        it = chain([((time1, image1), (time2, image2))], it)

        height, width, channels = image1.shape
        assert channels == 3, f"channels == {channels} (!= 3)"

        container = av.open(os.fspath(outpath), mode="w")

        stream = container.add_stream("libx264", rate=fps)
        stream.width = width
        stream.height = height
        stream.pix_fmt = "yuv420p"
        stream.options = {"crf": "23", "preset": "fast"}

        ignored1 = 0
        ignored2 = 0

        for time_image_1, time_image_2 in progress.track(it, total=total, description="Encoding", mininterval=0.5):
            if time_image_1 is None:
                ignored2 += 1
                continue
            if time_image_2 is None:
                ignored1 += 1
                continue

            (_time1, image1), (_time2, image2) = time_image_1, time_image_2

            img_diff = process_img_diff(image1, image2, diff, size1, size2)

            frame = av.VideoFrame.from_ndarray(img_diff, format="bgr24")
            frame.pts = None  # let encoder assign timestamps

            for packet in stream.encode(frame):
                container.mux(packet)

        # Flush encoder
        for packet in stream.encode():
            container.mux(packet)

        container.close()

    log_ignored(ignored1, ignored2)


def action_analyze(args: Namespace) -> int:
    try:
        with json_lines.from_path(args.path, "rt") as fr:
            for obj in fr:
                path1 = Path(obj["path1"])
                path2 = Path(obj["path2"])

                if obj["error"] is not None:
                    print(f"{path1.name}: {obj['error']}")
                    continue

                metric = obj["metric"]
                scores = np.array(obj["scores"])
                seconds = np.array(obj["times1"])
                size1 = obj.get("size1", None)
                size2 = obj.get("size2", None)
                skip_nth_1 = obj.get("skip_nth_1", 0)
                skip_nth_2 = obj.get("skip_nth_2", 0)
                skip1 = obj.get("skip1", 0)
                skip2 = obj.get("skip2", 0)

                func = agg_funcs[metric]
                funcname = func.__name__
                agg_val = func(scores)

                title = f"{path1.name}: {funcname}({metric})={agg_val}"
                print(title)

                if args.export:
                    if args.threshold is None:
                        threshold = scores.mean()
                    else:
                        threshold = args.threshold

                    if func == np.min:
                        matches = scores < threshold
                    elif func == np.max:
                        matches = scores > threshold
                    else:
                        assert False, f"Unsupported function {func}"

                    denoise(matches)
                    export_differences_video(
                        path1,
                        path2,
                        matches,
                        args.export,
                        args.diff,
                        args.fps,
                        size1,
                        size2,
                        skip_nth_1,
                        skip_nth_2,
                        skip1,
                        skip2,
                    )

                if args.plot:
                    if args.x_axis == "frames":
                        x_axis = np.arange(len(scores))
                        x_label = "frames"
                    elif args.x_axis == "seconds":
                        x_axis = seconds
                        x_label = "seconds"
                    else:
                        assert False, f"Unsupported x-axis {args.x_axis}"

                    plt.title(title)
                    plt.xlabel(x_label)
                    plt.ylabel(metric)
                    plt.scatter(x_axis, scores, s=10, c="red")
                    plt.show()
    except json.JSONDecodeError:
        logger.error("Failed to parse JSON Lines file")
        return 1

    return 0


def simplify(image: np.ndarray, size=(128, 128)) -> np.ndarray:
    image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    if image.shape[0] <= size[0] and image.shape[1] <= size[1]:
        return image

    image = cv2.resize(image, size, interpolation=cv2.INTER_AREA)

    return image


from skimage.transform import resize


def make_descriptor(image: np.ndarray, size=(128, 128)) -> np.ndarray:
    image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    image = resize(image, size, anti_aliasing=True, preserve_range=True)
    v = image.astype(np.float32).ravel()
    v -= v.mean()
    std = v.std() + 1e-8
    v /= std
    return v


def diagonal_means(arr: np.ndarray) -> np.ndarray:
    n, m = arr.shape
    offsets = np.arange(-(n - 1), m)
    means = []
    for k in offsets:
        means.append(np.nanmean(np.diag(arr, k=k)))
    return offsets, np.array(means)


from numpy.fft import irfft, rfft


def _dot_correlation(A: np.ndarray, B: np.ndarray) -> np.ndarray:
    l1, d = A.shape
    l2, d2 = B.shape
    if d != d2:
        raise ValueError(f"dim mismatch: A.shape={A.shape}, B.shape={B.shape}")

    n = l1 + l2 - 1
    nfft = 1 << (n - 1).bit_length()  # next power of two

    # cross term via FFT: sum_k correlate(A[:,k], B[:,k]) for all lags
    corr = np.zeros(n, dtype=np.float64)
    for k in range(d):
        Ak = A[:, k]
        Bk = B[:, k]
        Ak_pad = np.pad(Ak, (0, nfft - l1))
        Bk_rev_pad = np.pad(Bk[::-1], (0, nfft - l2))  # reverse for correlation via convolution
        conv = irfft(rfft(Ak_pad) * rfft(Bk_rev_pad), nfft)[:n]
        corr += conv

    lags = np.arange(-(l2 - 1), l1)  # shape (n,)
    return corr, lags


def dot_prod_correlation(A: np.ndarray, B: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """
    A: (l1, d), B: (l2, d) descriptor sequences.
    Returns lags array and correlation scores for all lags in [-(l2-1) .. (l1-1)].
    """
    corr, lags = _dot_correlation(A, B)

    return lags, corr


def mse_correlation(A: np.ndarray, B: np.ndarray, *, per_element: bool = True) -> Tuple[np.ndarray, np.ndarray]:
    """
    Compute MSE between two vector sequences A (l1,d) and B (l2,d) for all integer lags c,
    where we compare A[i] with B[i+c] over the overlapping index range.

    Returns:
      lags: np.ndarray of shape (l1 + l2 - 1,), values from -(l2-1) .. (l1-1)
      mse:  np.ndarray of same shape, MSE at each lag (inf where no overlap)
      best_c: int, lag with minimal MSE

    Notes:
      - If per_element=True, MSE divides by (overlap_len * d).
        If False, you get mean per frame (SSD / overlap_len).
      - Works for any real-valued A,B. Uses real FFT for speed.
      - Complexity: O(d * n log n) with n = l1 + l2 - 1.
    """
    corr, lags = _dot_correlation(A, B)
    l1 = A.shape[0]
    l2 = B.shape[0]
    d = A.shape[1]

    # squared-norm sums over overlaps via prefix sums
    a2 = np.einsum("ij,ij->i", A, A)  # (l1,)
    b2 = np.einsum("ij,ij->i", B, B)  # (l2,)
    pa = np.concatenate(([0.0], np.cumsum(a2)))  # (l1+1,)
    pb = np.concatenate(([0.0], np.cumsum(b2)))  # (l2+1,)

    # vectorized overlap bounds for each lag
    i0 = np.maximum(0, -lags)
    i1 = np.minimum(l1, l2 - lags)
    overlap = (i1 - i0).astype(np.int64)  # number of frame pairs at each lag

    # sums of ||A||^2 over [i0, i1)
    sum_a2 = pa[i1] - pa[i0]
    # sums of ||B||^2 over [j0, j1) with j = i + c
    j0 = i0 + lags
    j1 = i1 + lags
    sum_b2 = pb[j1] - pb[j0]

    # cross term (dot-product correlation) aligned with lag indices
    cross = corr[lags + (l2 - 1)]

    # Sum of Squared Differences: SSD(c) = sum_a2 + sum_b2 - 2 * cross
    ssd = sum_a2 + sum_b2 - 2.0 * cross

    # turn into MSE
    denom = overlap.astype(float)
    if per_element:
        denom *= d  # mean per scalar element
    with np.errstate(divide="ignore", invalid="ignore"):
        mse = ssd / denom
    mse[overlap <= 0] = np.inf  # no overlap → undefined; mark as inf

    return lags, mse


def find_lag(
    path1: Path, path2: Path, metric: str, size: int, skip_nth_1: int, skip_nth_2: int, progress: Progress
) -> Tuple[np.ndarray, np.ndarray]:
    metric_func = metric_funcs[metric]
    agg_func = agg_funcs[metric]

    images_a = []
    with CvVideo(path1) as vid:
        for _time, image in islice(skip_every_nth(vid.iterall(native=True), skip_nth_1), size):
            img_processed = simplify(image)
            images_a.append(img_processed)

    images_b = []
    with CvVideo(path2) as vid:
        for _time, image in islice(skip_every_nth(vid.iterall(native=True), skip_nth_2), size):
            img_processed = simplify(image)
            images_b.append(img_processed)

    images_a = np.array(images_a)
    images_b = np.array(images_b)

    if metric == "mse":
        F = images_a.reshape((images_a.shape[0], -1))  # (l1, d)
        G = images_b.reshape((images_b.shape[0], -1))  # (l2, d)

        offsets, scores = mse_correlation(F, G)
        idx = np.argsort(scores)
    else:
        out = np.full((size, size), np.nan, dtype=np.float32)

        for i in progress.track(range(0, size), transient=False):
            for j in range(0, size):
                out[i, j] = metric_func(images_a[i], images_b[j])
            # batch mode is actually slower
            # out[i] = batch_structural_similarity(np.broadcast_to(images_a[None, i, ...], images_b.shape), images_b)

        offsets, scores = diagonal_means(out)
        idx = np.argsort(scores)

    if agg_func == np.min:
        idx = idx[::-1]
    elif agg_func == np.max:
        idx = idx
    else:
        assert False, f"Unsupported function {agg_func}"

    return offsets[idx], scores[idx]


def action_find_lag(args: Namespace) -> int:
    progress = Progress()
    offsets, means = find_lag(
        args.path1, args.path2, args.metric, args.size, args.skip_nth_1, args.skip_nth_2, progress
    )
    print(offsets[:100], means[:100])
    return 0


def main() -> None:
    DEFAULT_WORKERS = ceil((os.cpu_count() or 1) / 2)
    DEFAULT_METRIC = "mse"
    DEFAULT_XAXIS = "seconds"
    DEFAULT_VISUAL_DIFF = "absdiff"

    parser = ArgumentParser(formatter_class=ArgumentDefaultsHelpFormatter)
    parser.add_argument("-v", "--verbose", action="store_true", help="Show debug information")
    subparsers = parser.add_subparsers(dest="action", required=True)

    parser_a = subparsers.add_parser("compare", help="Calculate video quality metric")
    parser_a.set_defaults(func=action_compare)
    parser_a.add_argument("path1", type=Path, help="First input directory or file")
    parser_a.add_argument("path2", type=Path, help="Second input directory or file")
    parser_a.add_argument(
        "--path-pairs",
        type=Path,
        nargs="+",
        default=[],
        help="Additional individual files to compare. The first file will be compared to the second, the third to the forth, and so on.",
    )
    parser_a.add_argument("--size1", nargs=2, metavar=("W", "H"), type=int, help="Resize first video to width x height")
    parser_a.add_argument("--size2", nargs=2, metavar=("W", "H"), type=int, help="Resize first video to width x height")
    parser_a.add_argument("--skip-nth-1", metavar="N", default=0, type=int, help="Skip every n-th frame")
    parser_a.add_argument("--skip-nth-2", metavar="N", default=0, type=int, help="Skip every n-th frame")
    parser_a.add_argument("--skip1", metavar="N", default=0, type=int, help="Skip first N frames")
    parser_a.add_argument("--skip2", metavar="N", default=0, type=int, help="Skip first N frames")
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
    parser_b.add_argument(
        "--x-axis",
        choices=("frames", "seconds"),
        default=DEFAULT_XAXIS,
        help="Weither to show number of seconds or number of frames in x-axis of the plot",
    )
    parser_b.add_argument("--threshold", type=float, help="Metric threshold to use to export video differences")
    parser_b.add_argument("--fps", type=float, help="FPS of exported video. Manually read from source file.")
    parser_b.add_argument(
        "--export", type=Path, help="When given, a video showing the different frames is exported to this path"
    )
    parser_b.add_argument(
        "--diff",
        default=DEFAULT_VISUAL_DIFF,
        choices=visual_diff_funcs.keys(),
        help="Image difference function",
    )

    parser_c = subparsers.add_parser(
        "find-lag",
        help="Find the offset in frames between two video files. Only works if there is a constant offset throughout",
    )
    parser_c.set_defaults(func=action_find_lag)
    parser_c.add_argument("path1", type=Path, help="First input file")
    parser_c.add_argument("path2", type=Path, help="Second input file")
    parser_c.add_argument(
        "--size",
        type=int,
        default=100,
        help="Number of frames to check for alignment. Must be larger than the alignment offset.",
    )
    parser_c.add_argument(
        "--metric",
        default=DEFAULT_METRIC,
        choices=metric_funcs.keys(),
        help="Image quality metric",
    )
    parser_c.add_argument("--skip-nth-1", metavar="N", default=0, type=int, help="Skip every n-th frame")
    parser_c.add_argument("--skip-nth-2", metavar="N", default=0, type=int, help="Skip every n-th frame")

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
