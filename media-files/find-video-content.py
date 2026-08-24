# /// script
# requires-python = ">=3.8"
# dependencies = [
#   "genutility[args,filesystem]>=0.0.122",
#   "numpy>=1.24",
#   "opencv-python>=4.8",
# ]
# ///

import logging
import math
import os
from argparse import ArgumentParser, Namespace, RawDescriptionHelpFormatter
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Deque, Iterable, Iterator, List, Optional, Set, Tuple, cast

import cv2
import numpy as np
from genutility.args import int_at_least, positive_float
from genutility.filesystem import MyDirEntry, scandir_rec

logger = logging.getLogger(__name__)

VIDEO_EXTENSIONS = {
    ".3gp",
    ".avi",
    ".flv",
    ".m2ts",
    ".m4v",
    ".mkv",
    ".mov",
    ".mp4",
    ".mpeg",
    ".mpg",
    ".mts",
    ".ts",
    ".webm",
    ".wmv",
}
MODES = {"watermark_moving", "watermark_partial", "watermark_persistent"}
DEFAULT_MINIMUM_DURATION = 10.0
EDGE_PRESENCE = 0.9
MAX_CANDIDATES = 20

BBox = Tuple[int, int, int, int]
TimedFrame = Tuple[float, np.ndarray]

EPILOG = r"""
This script searches decoded picture content. Watermark detection is heuristic:
it finds compact, edge-rich graphics that remain temporally consistent without
requiring a reference image. It does not detect invisible watermarks, and static
scene graphics or independently moving objects can produce false positives.

Modes
-----
watermark_persistent
  Find a fixed overlay candidate whose edges recur at the same screen position
  throughout the sampled file. This is intended for permanent channel logos,
  timestamps, or text marks.

  Examples:
    find-video-content.py watermark_persistent D:\Videos
    find-video-content.py watermark_persistent D:\Archive --recursive --sample-every 2

watermark_partial
  Find a fixed overlay candidate present continuously for at least
  --minimum-duration seconds (default: 10). The overlay may appear or disappear
  elsewhere in the file.

  Examples:
    find-video-content.py watermark_partial D:\Videos --minimum-duration 30
    find-video-content.py watermark_partial D:\Clips --extension mp4 --sample-every 0.5

watermark_moving
  Find a compact edge pattern which retains a similar appearance while moving
  across the screen for at least --minimum-duration seconds (default: 10).

  Examples:
    find-video-content.py watermark_moving D:\Videos
    find-video-content.py watermark_moving D:\Archive --recursive --minimum-duration 5

Common options
--------------
--recursive
  Include files in subdirectories. Without it, every mode scans only PATH.

--extension EXT
  Override the common video extensions. Repeat for several extensions; a leading
  dot is optional and matching ignores case.

--sample-every SECONDS
  Analyze one frame at this interval (default: 1). Smaller values improve
  short-duration and motion detection but increase decoding time.

--analysis-width PIXELS
  Downscale wider frames before analysis (default: 640). Larger values retain
  smaller overlays but cost more CPU and memory.
"""


@dataclass
class MotionTrack:
    start_time: float
    last_time: float
    first_center: Tuple[float, float]
    max_movement: float
    descriptor: np.ndarray
    box: BBox
    hits: int = 1


def normalize_extensions(extensions: Iterable[str]) -> Set[str]:
    return {
        extension.casefold() if extension.startswith(".") else f".{extension.casefold()}" for extension in extensions
    }


def prepare_frame(frame: np.ndarray, analysis_width: int) -> np.ndarray:
    height, width = frame.shape[:2]
    if width > analysis_width:
        height = max(1, round(height * analysis_width / width))
        frame = cv2.resize(frame, (analysis_width, height), interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)


def iter_sampled_frames(path: Path, sample_every: float, analysis_width: int) -> Iterator[TimedFrame]:
    capture = cv2.VideoCapture(os.fspath(path))
    if not capture.isOpened():
        capture.release()
        raise OSError("OpenCV could not open the video")

    try:
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        frame_count = float(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        if not math.isfinite(fps) or fps <= 0 or not math.isfinite(frame_count) or frame_count <= 0:
            raise LookupError("video frame rate or frame count is unavailable")

        end_time = max(0.0, (frame_count - 1) / fps)
        sample_time = 0.0
        last_time = -math.inf
        while sample_time <= end_time:
            capture.set(cv2.CAP_PROP_POS_MSEC, sample_time * 1000.0)
            ok, frame = capture.read()
            if not ok or frame is None:
                raise OSError(f"OpenCV could not decode a frame at {sample_time:g} seconds")
            yield sample_time, prepare_frame(frame, analysis_width)
            last_time = sample_time
            sample_time += sample_every

        if end_time - last_time > sample_every / 4:
            capture.set(cv2.CAP_PROP_POS_MSEC, end_time * 1000.0)
            ok, frame = capture.read()
            if ok and frame is not None:
                yield end_time, prepare_frame(frame, analysis_width)
    finally:
        capture.release()


def edge_mask(gray: np.ndarray) -> np.ndarray:
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    median = float(np.median(blurred))
    low = max(20, round(median * 0.66))
    high = max(low + 1, min(255, round(median * 1.33)))
    edges = cv2.Canny(blurred, low, high)
    dilated = np.asarray(cv2.dilate(edges, np.ones((3, 3), dtype=np.uint8)))
    return np.asarray(dilated != 0, dtype=np.uint8)


def candidate_boxes(mask: np.ndarray) -> List[BBox]:
    height, width = mask.shape
    kernel_width = max(3, round(width * 0.012))
    kernel_height = max(3, round(height * 0.012))
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_width, kernel_height))
    merged = cv2.morphologyEx(mask * 255, cv2.MORPH_CLOSE, kernel, iterations=2)
    _count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(merged, connectivity=8)
    stats = np.asarray(stats)

    scored = []
    frame_area = height * width
    for x, y, box_width, box_height, _component_area in stats[1:]:
        box_area = int(box_width * box_height)
        if box_width < 8 or box_height < 6 or box_area < 48:
            continue
        if box_width > width * 0.6 or box_height > height * 0.4 or box_area > frame_area * 0.2:
            continue

        edge_pixels = int(np.count_nonzero(mask[y : y + box_height, x : x + box_width]))
        if edge_pixels < max(12, round(box_area * 0.015)):
            continue
        scored.append((edge_pixels / math.sqrt(box_area), (int(x), int(y), int(box_width), int(box_height))))

    scored.sort(reverse=True)
    return [box for _score, box in scored[:MAX_CANDIDATES]]


def static_candidate(
    edge_counts: np.ndarray,
    gray_sum: np.ndarray,
    gray_square_sum: np.ndarray,
    samples: int,
) -> Optional[Tuple[BBox, float]]:
    if samples < 3:
        return None

    required = max(2, math.ceil(samples * EDGE_PRESENCE))
    stable = np.asarray(edge_counts >= required, dtype=np.uint8)
    boxes = candidate_boxes(stable)
    if not boxes:
        return None

    variance = np.maximum(gray_square_sum / samples - np.square(gray_sum / samples), 0)
    frame_area = stable.shape[0] * stable.shape[1]
    for box in boxes:
        x, y, width, height = box
        stable_pixels = stable[y : y + height, x : x + width] != 0
        stable_density = float(np.mean(stable_pixels))
        low_variance = float(np.mean(variance[y : y + height, x : x + width] < 36))
        if low_variance < 0.25 and not (stable_density >= 0.3 and width * height >= frame_area * 0.002):
            continue
        persistence = float(np.mean(edge_counts[y : y + height, x : x + width][stable_pixels]) / samples)
        return box, persistence
    return None


def candidate_supported(
    edge_counts: np.ndarray,
    samples: int,
    box: BBox,
    edge_masks: Iterable[np.ndarray],
) -> bool:
    x, y, width, height = box
    required = max(2, math.ceil(samples * EDGE_PRESENCE))
    template = edge_counts[y : y + height, x : x + width] >= required
    template_pixels = int(np.count_nonzero(template))
    if template_pixels == 0:
        return False
    return all(
        np.count_nonzero(edges[y : y + height, x : x + width][template]) / template_pixels >= 0.5
        for edges in edge_masks
    )


def format_box(box: BBox, shape: Tuple[int, int]) -> str:
    x, y, width, height = box
    frame_height, frame_width = shape
    return (
        f"x={x / frame_width:.1%}, y={y / frame_height:.1%}, w={width / frame_width:.1%}, h={height / frame_height:.1%}"
    )


def detect_persistent(frames: Iterable[TimedFrame]) -> Optional[str]:
    edge_counts: Optional[np.ndarray] = None
    gray_sum: Optional[np.ndarray] = None
    gray_square_sum: Optional[np.ndarray] = None
    samples = 0
    shape: Optional[Tuple[int, int]] = None
    first_edges = None
    last_edges = None
    for _time, gray in frames:
        edges = edge_mask(gray)
        if edge_counts is None:
            edge_counts = np.zeros_like(edges, dtype=np.uint32)
            gray_sum = np.zeros_like(gray, dtype=np.float64)
            gray_square_sum = np.zeros_like(gray, dtype=np.float64)
            shape = (int(edges.shape[0]), int(edges.shape[1]))
            first_edges = edges
        elif edges.shape != shape:
            raise ValueError("video dimensions changed during analysis")
        edge_counts += edges
        assert gray_sum is not None and gray_square_sum is not None
        gray_sum += gray
        gray_square_sum += np.square(gray, dtype=np.float64)
        last_edges = edges
        samples += 1

    if (
        edge_counts is None
        or gray_sum is None
        or gray_square_sum is None
        or shape is None
        or first_edges is None
        or last_edges is None
    ):
        return None
    candidate = static_candidate(edge_counts, gray_sum, gray_square_sum, samples)
    if candidate is None:
        return None
    box, persistence = candidate
    if not candidate_supported(edge_counts, samples, box, (first_edges, last_edges)):
        return None
    return f"persistent overlay candidate at {format_box(box, shape)}; edge persistence {persistence:.0%}"


def detect_partial(frames: Iterable[TimedFrame], minimum_duration: float) -> Optional[str]:
    window: Deque[Tuple[float, np.ndarray, np.ndarray]] = deque()
    edge_counts: Optional[np.ndarray] = None
    gray_sum: Optional[np.ndarray] = None
    gray_square_sum: Optional[np.ndarray] = None
    shape: Optional[Tuple[int, int]] = None

    for time, gray in frames:
        edges = edge_mask(gray)
        if edge_counts is None:
            edge_counts = np.zeros_like(edges, dtype=np.int32)
            gray_sum = np.zeros_like(gray, dtype=np.float64)
            gray_square_sum = np.zeros_like(gray, dtype=np.float64)
            shape = (int(edges.shape[0]), int(edges.shape[1]))
        elif edges.shape != shape:
            raise ValueError("video dimensions changed during analysis")

        assert gray_sum is not None and gray_square_sum is not None
        window.append((time, edges, gray))
        edge_counts += edges
        gray_sum += gray
        gray_square_sum += np.square(gray, dtype=np.float64)
        while len(window) > 1 and window[1][0] <= time - minimum_duration:
            _old_time, old_edges, old_gray = window.popleft()
            edge_counts -= old_edges
            gray_sum -= old_gray
            gray_square_sum -= np.square(old_gray, dtype=np.float64)

        if time - window[0][0] < minimum_duration:
            continue
        assert edge_counts is not None
        candidate = static_candidate(edge_counts, gray_sum, gray_square_sum, len(window))
        if candidate is not None and shape is not None:
            box, persistence = candidate
            if not candidate_supported(edge_counts, len(window), box, (edges for _time, edges, _gray in window)):
                continue
            return (
                f"partial overlay candidate at {format_box(box, shape)} from {window[0][0]:g}s to {time:g}s; "
                f"edge persistence {persistence:.0%}"
            )

    return None


def box_center(box: BBox) -> Tuple[float, float]:
    x, y, width, height = box
    return x + width / 2, y + height / 2


def moving_candidates(gray: np.ndarray) -> List[Tuple[BBox, np.ndarray]]:
    edges = edge_mask(gray)
    candidates = []
    for box in candidate_boxes(edges):
        x, y, width, height = box
        patch = edges[y : y + height, x : x + width].astype(np.float32)
        descriptor = cv2.resize(patch, (32, 32), interpolation=cv2.INTER_AREA).ravel()
        descriptor -= descriptor.mean()
        norm = float(np.linalg.norm(descriptor))
        if norm > 0:
            candidates.append((box, descriptor / norm))
    return candidates


def detect_moving(frames: Iterable[TimedFrame], minimum_duration: float, sample_every: float) -> Optional[str]:
    tracks: List[MotionTrack] = []

    for time, gray in frames:
        candidates = moving_candidates(gray)
        used: Set[int] = set()
        updated_tracks = []
        frame_diagonal = math.hypot(gray.shape[1], gray.shape[0])

        for track in tracks:
            best_index = None
            best_similarity = 0.65
            old_area = track.box[2] * track.box[3]
            for index, (box, descriptor) in enumerate(candidates):
                if index in used:
                    continue
                new_area = box[2] * box[3]
                area_ratio = new_area / old_area
                if not 0.5 <= area_ratio <= 2.0:
                    continue
                similarity = float(np.dot(track.descriptor, descriptor))
                if similarity > best_similarity:
                    best_similarity = similarity
                    best_index = index

            if best_index is None:
                if time - track.last_time <= sample_every * 2.1:
                    updated_tracks.append(track)
                continue

            used.add(best_index)
            box, descriptor = candidates[best_index]
            center = box_center(box)
            track.last_time = time
            track.max_movement = max(track.max_movement, math.dist(track.first_center, center))
            track.descriptor = descriptor
            track.box = box
            track.hits += 1
            updated_tracks.append(track)

            duration = track.last_time - track.start_time
            expected_hits = duration / sample_every + 1
            if (
                duration >= minimum_duration
                and track.hits >= max(3, math.ceil(expected_hits * 0.6))
                and track.max_movement >= frame_diagonal * 0.05
            ):
                return (
                    f"moving overlay candidate tracked from {track.start_time:g}s to {track.last_time:g}s; "
                    f"moved {track.max_movement / frame_diagonal:.1%} of the frame diagonal"
                )

        for index, (box, descriptor) in enumerate(candidates):
            if index not in used:
                center = box_center(box)
                updated_tracks.append(MotionTrack(time, time, center, 0.0, descriptor, box))
        tracks = updated_tracks

    return None


def analyze_video(
    path: Path,
    mode: str,
    sample_every: float = 1.0,
    minimum_duration: float = DEFAULT_MINIMUM_DURATION,
    analysis_width: int = 640,
) -> Optional[str]:
    frames = iter_sampled_frames(path, sample_every, analysis_width)
    if mode == "watermark_persistent":
        return detect_persistent(frames)
    if mode == "watermark_partial":
        return detect_partial(frames, minimum_duration)
    if mode == "watermark_moving":
        return detect_moving(frames, minimum_duration, sample_every)
    raise ValueError(f"unknown mode: {mode}")


def scan_videos(
    root: Path,
    mode: str,
    extensions: Iterable[str],
    recursive: bool,
    sample_every: float,
    minimum_duration: float,
    analysis_width: int,
) -> Tuple[int, int]:
    normalized_extensions = normalize_extensions(extensions)
    matches = 0
    errors = 0

    for entry in scandir_rec(root, dirs=False, rec=recursive, relative=True):
        entry = cast(MyDirEntry, entry)
        if Path(entry.name).suffix.casefold() not in normalized_extensions:
            continue

        display_path = root / entry.relpath
        logger.info("Analyzing %s", display_path)
        try:
            result = analyze_video(Path(entry.path), mode, sample_every, minimum_duration, analysis_width)
        except LookupError as e:
            logger.warning("Cannot inspect %s: %s", display_path, e)
            errors += 1
        except (cv2.error, OSError, RuntimeError, ValueError):
            logger.exception("Cannot inspect %s", display_path)
            errors += 1
        else:
            if result is not None:
                print(f"{display_path}: {result}")
                matches += 1

    return matches, errors


def build_parser() -> ArgumentParser:
    parser = ArgumentParser(
        description="Find visible watermark-like overlays in decoded video content.",
        epilog=EPILOG,
        formatter_class=RawDescriptionHelpFormatter,
    )
    parser.add_argument("mode", choices=sorted(MODES), help="Watermark behavior to search for; see details below")
    parser.add_argument("path", type=Path, help="Directory to scan")
    parser.add_argument("-r", "--recursive", action="store_true", help="Include files in subdirectories")
    parser.add_argument(
        "--extension",
        action="append",
        dest="extensions",
        metavar="EXT",
        help="Video extension to inspect; repeat to override the defaults",
    )
    parser.add_argument(
        "--sample-every",
        type=positive_float,
        default=1.0,
        metavar="SECONDS",
        help="Seconds between analyzed frames (default: 1)",
    )
    parser.add_argument(
        "--minimum-duration",
        type=positive_float,
        metavar="SECONDS",
        help="Required continuous duration for partial/moving modes (default: 10)",
    )
    parser.add_argument(
        "--analysis-width",
        type=int_at_least(64),
        default=640,
        metavar="PIXELS",
        help="Downscale wider frames before analysis (default: 640)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Show each file as it is analyzed")
    return parser


def validate_args(parser: ArgumentParser, args: Namespace) -> None:
    if not args.path.is_dir():
        parser.error(f"path is not a directory: {args.path}")
    if args.mode == "watermark_persistent" and args.minimum_duration is not None:
        parser.error("--minimum-duration is only valid with watermark_partial or watermark_moving")
    minimum_duration = args.minimum_duration or DEFAULT_MINIMUM_DURATION
    if args.mode != "watermark_persistent" and minimum_duration < args.sample_every * 2:
        parser.error("--minimum-duration must span at least two --sample-every intervals")


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    validate_args(parser, args)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s: %(message)s")

    minimum_duration = args.minimum_duration or DEFAULT_MINIMUM_DURATION
    _matches, errors = scan_videos(
        args.path,
        args.mode,
        args.extensions or VIDEO_EXTENSIONS,
        args.recursive,
        args.sample_every,
        minimum_duration,
        args.analysis_width,
    )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
