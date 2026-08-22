# /// script
# requires-python = ">=3.8"
# dependencies = [
#   "enzyme>=0.5.2",
#   "genutility[mediainfo]>=0.0.114",
#   "langcodes>=3.3,<3.5",
#   "language-data>=1.1,<1.2; python_version < '3.9'",
#   "language-data>=1.3; python_version >= '3.9'",
# ]
# ///

import logging
import math
from argparse import ArgumentParser, Namespace, RawDescriptionHelpFormatter
from functools import partial
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple, cast

import enzyme
import langcodes
from genutility.filesystem import MyDirEntry, scandir_rec
from genutility.mediainfo import MediaInfoHelper

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
MATROSKA_EXTENSIONS = {".mk3d", ".mka", ".mks", ".mkv", ".webm"}
VideoCheck = Callable[[Path], Optional[str]]


EPILOG = r"""
Modes
-----
audio_language
  Find files with at least one audio track in any requested language. Repeat
  --language to combine languages with OR. Names and ISO/IETF tags are accepted;
  for example, Chinese/zh/chi/zh-TW all select the Chinese base language.

minimum_settings
  Find x264 videos that do not meet the documented PTP minimum settings for
  SD, 720p, or 1080p-and-higher video. Files without x264 settings are reported
  as metadata errors instead of being silently classified.

vfr
  Find videos whose MediaInfo frame-rate mode is VFR (variable frame rate).

portrait
  Find videos whose displayed height is larger than their displayed width.
  Quarter-turn rotation metadata is applied by default, so both encoded
  1080x1920 and encoded 1920x1080 rotated 90 or 270 degrees are matches.

disabled_tracks
  Find Matroska-family containers with video, audio, or subtitle tracks whose
  FlagEnabled value is false. This mode parses EBML in-process with Enzyme; it
  does not require mkvmerge or another external program.

inconsistent_lengths
  Find files whose video/audio tracks differ by more than --max-difference
  seconds, or whose subtitles are that much longer than the longest video/audio
  track (default: 10). Shorter subtitles are allowed.

Scan options
------------
--recursive
  Include files in subdirectories. Without this flag, every mode scans only
  files directly inside PATH.

--extension EXT
  Override the selected mode's extension defaults. Repeat the option to allow
  several extensions; a leading dot is optional and matching ignores case.

--language LANGUAGE
  Audio language name or ISO/IETF tag for audio_language. Repeat to search for
  any of several languages (OR); this option is rejected by other modes.

--max-difference SECONDS
  Maximum permitted difference between track durations for inconsistent_lengths.
  The value must be finite and non-negative; the default is 10 seconds.

Examples
--------
  find-video.py audio_language D:\Videos --language Chinese --language Taiwanese
  find-video.py audio_language D:\Archive --recursive --language zh --language nan
  find-video.py portrait D:\Camera
  find-video.py portrait D:\Phone --recursive --extension mp4 --extension mov
  find-video.py disabled_tracks D:\Videos
  find-video.py disabled_tracks D:\Archive --recursive --extension mkv
  find-video.py inconsistent_lengths D:\Videos
  find-video.py inconsistent_lengths D:\Archive --recursive --max-difference 2.5
  find-video.py vfr D:\Videos --recursive --extension mkv --extension mp4
  find-video.py minimum_settings D:\Encodes --recursive
"""


def parse_x264_settings(settings: str) -> Dict[str, str]:
    parsed = {}
    for part in settings.split("/"):
        name, separator, value = part.strip().partition("=")
        if not separator:
            raise ValueError(f"invalid x264 setting: {part!r}")
        parsed[name] = value
    return parsed


def ptp_minimum_settings(settings: Dict[str, str], height: int = 576) -> List[str]:
    """Return PTP minimum-setting names that are missing or not met.

    SD requires ref=9, me_range=24, and bframes=5; 720p requires 8/16/5;
    larger video requires 3/16/3. TESA permits me_range=16 at every size.
    """

    required = {"analyse", "bframes", "cabac", "me", "me_range", "rc", "ref", "subme"}
    missing = sorted(required.difference(settings))
    if missing:
        return [f"missing {name}" for name in missing]

    if height <= 576:
        minimum_ref = 9
        minimum_bframes = 5
        minimum_me_range = 24
    elif height <= 720:
        minimum_ref = 8
        minimum_bframes = 5
        minimum_me_range = 16
    else:
        minimum_ref = 3
        minimum_bframes = 3
        minimum_me_range = 16

    me = settings["me"]
    me_range = int(settings["me_range"])
    checks = {
        "cabac": int(settings["cabac"]) == 1,
        "ref": int(settings["ref"]) >= minimum_ref,
        "analyse": settings["analyse"] in {"0x3:0x113", "0x3:0x133"},
        "subme": int(settings["subme"]) >= 7,
        "me": me in {"umh", "esa", "tesa"} and me_range >= (16 if me == "tesa" else minimum_me_range),
        "bframes": int(settings["bframes"]) >= minimum_bframes,
        "rc": settings["rc"] in {"2pass", "crf"},
    }
    return [name for name, passed in checks.items() if not passed]


def normalize_language(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("language cannot be empty")

    try:
        language = langcodes.Language.get(value) if langcodes.tag_is_valid(value) else langcodes.find(value)
    except (LookupError, ValueError) as e:
        raise ValueError(f"unknown language: {value!r}") from e
    return language.language or "und"


def check_audio_language(path: Path, languages: Iterable[str] = ()) -> Optional[str]:
    requested_languages = set(languages)
    if not requested_languages:
        raise ValueError("no audio languages requested")

    matches = []
    for track in MediaInfoHelper(path).mi.audio_tracks:
        language = track.language
        if not language:
            continue
        try:
            normalized_language = normalize_language(str(language))
        except ValueError:
            continue
        if normalized_language not in requested_languages:
            continue

        track_id = track.track_id if track.track_id is not None else "?"
        description = f"audio #{track_id}"
        details = [str(detail) for detail in (language, track.title) if detail]
        if details:
            description += f" ({', '.join(details)})"
        matches.append(description)

    return "matching audio tracks: " + ", ".join(matches) if matches else None


def check_minimum_settings(path: Path) -> Optional[str]:
    video = MediaInfoHelper(path).video
    settings = video.encoding_settings
    if not settings:
        raise LookupError("video track has no x264 encoding settings")
    if video.height is None:
        raise LookupError("video track has no encoded height")

    not_met = ptp_minimum_settings(parse_x264_settings(settings), int(video.height))
    if not_met:
        return "minimum settings not met: " + ", ".join(not_met)
    return None


def check_vfr(path: Path) -> Optional[str]:
    return "VFR" if MediaInfoHelper(path).video.frame_rate_mode == "VFR" else None


def check_portrait(path: Path) -> Optional[str]:
    video = MediaInfoHelper(path).video
    if video.width is None or video.height is None:
        raise LookupError("video track has no encoded dimensions")

    width = int(video.width)
    height = int(video.height)
    rotation = float(getattr(video, "rotation", None) or 0)
    quarter_turns = round(rotation / 90)
    if not math.isclose(rotation, quarter_turns * 90, abs_tol=0.01):
        raise ValueError(f"unsupported non-right-angle rotation: {rotation:g} degrees")

    if quarter_turns % 2:
        display_width, display_height = height, width
    else:
        display_width, display_height = width, height

    if display_height <= display_width:
        return None
    if quarter_turns % 2:
        return (
            f"{display_width}x{display_height} display (encoded {width}x{height}, rotation {rotation % 360:g} degrees)"
        )
    return f"{display_width}x{display_height}"


def check_disabled_tracks(path: Path) -> Optional[str]:
    try:
        with path.open("rb") as file:
            mkv = enzyme.MKV(file)
    except (enzyme.MalformedMKVError, KeyError) as e:
        raise ValueError(f"invalid Matroska metadata: {e}") from e

    disabled = []
    track_groups = (
        ("video", mkv.video_tracks),
        ("audio", mkv.audio_tracks),
        ("subtitle", mkv.subtitle_tracks),
    )
    for track_type, tracks in track_groups:
        for track in tracks:
            if track.enabled:
                continue

            description = f"{track_type} #{track.number}"
            details = [str(detail) for detail in (track.language, track.name) if detail]
            if details:
                description += f" ({', '.join(details)})"
            disabled.append(description)

    return "disabled tracks: " + ", ".join(disabled) if disabled else None


def check_inconsistent_lengths(path: Path, max_difference: float = 10.0) -> Optional[str]:
    media_tracks = []
    subtitle_tracks = []
    for track in MediaInfoHelper(path).mi.tracks:
        if track.track_type not in {"Video", "Audio", "Text"} or track.duration is None:
            continue

        duration = float(track.duration) / 1000.0
        if not math.isfinite(duration) or duration < 0:
            raise ValueError(f"invalid {track.track_type.casefold()} track duration: {track.duration!r}")
        track_id = track.track_id if track.track_id is not None else "?"
        track_type = "subtitle" if track.track_type == "Text" else track.track_type.casefold()
        timed_track = (duration, f"{track_type} #{track_id}")
        if track.track_type == "Text":
            subtitle_tracks.append(timed_track)
        else:
            media_tracks.append(timed_track)

    if len(media_tracks) >= 2:
        shortest = min(media_tracks)
        longest = max(media_tracks)
        difference = longest[0] - shortest[0]
        if difference > max_difference:
            return (
                f"track lengths differ by {difference:g} seconds: "
                f"{shortest[1]}={shortest[0]:g}s, {longest[1]}={longest[0]:g}s"
            )

    if media_tracks and subtitle_tracks:
        longest_media = max(media_tracks)
        longest_subtitle = max(subtitle_tracks)
        difference = longest_subtitle[0] - longest_media[0]
        if difference > max_difference:
            return (
                f"subtitle track is longer by {difference:g} seconds: "
                f"{longest_media[1]}={longest_media[0]:g}s, "
                f"{longest_subtitle[1]}={longest_subtitle[0]:g}s"
            )

    return None


MODES: Dict[str, VideoCheck] = {
    "audio_language": check_audio_language,
    "disabled_tracks": check_disabled_tracks,
    "inconsistent_lengths": check_inconsistent_lengths,
    "minimum_settings": check_minimum_settings,
    "portrait": check_portrait,
    "vfr": check_vfr,
}


def normalize_extensions(extensions: Iterable[str]) -> Set[str]:
    return {
        extension.casefold() if extension.startswith(".") else f".{extension.casefold()}" for extension in extensions
    }


def find_videos(
    path: Path,
    check: VideoCheck,
    extensions: Optional[Iterable[str]] = None,
    recursive: bool = False,
) -> Tuple[int, int]:
    normalized_extensions = normalize_extensions(VIDEO_EXTENSIONS if extensions is None else extensions)
    matches = 0
    errors = 0

    for entry in scandir_rec(path, dirs=False, rec=recursive, relative=True):
        entry = cast(MyDirEntry, entry)
        if Path(entry.name).suffix.casefold() not in normalized_extensions:
            continue

        display_path = path / entry.relpath
        try:
            result = check(Path(entry.path))
        except LookupError as e:
            logger.warning("Cannot inspect %s: %s", display_path, e)
            errors += 1
        except (OSError, RuntimeError, ValueError):
            logger.exception("Cannot inspect %s", display_path)
            errors += 1
        else:
            if result is not None:
                print(f"{display_path}: {result}")
                matches += 1

    return matches, errors


def build_parser() -> ArgumentParser:
    parser = ArgumentParser(
        description="Find media files matching one metadata-based condition.",
        epilog=EPILOG,
        formatter_class=RawDescriptionHelpFormatter,
    )
    parser.add_argument("mode", choices=sorted(MODES), help="Condition to search for; see mode descriptions below")
    parser.add_argument("path", type=Path, help="Directory to scan")
    parser.add_argument(
        "-r",
        "--recursive",
        action="store_true",
        help="Include files in subdirectories; applies to every mode",
    )
    parser.add_argument(
        "--extension",
        action="append",
        dest="extensions",
        metavar="EXT",
        help="Media-file extension to inspect, with or without a leading dot; repeat to override the mode defaults",
    )
    parser.add_argument(
        "-l",
        "--language",
        action="append",
        dest="languages",
        type=normalize_language,
        metavar="LANGUAGE",
        help="Audio language name or ISO/IETF tag; repeat for OR matching; only valid with audio_language",
    )
    parser.add_argument(
        "--max-difference",
        type=float,
        metavar="SECONDS",
        help="Maximum track-duration difference; default 10 seconds; only valid with inconsistent_lengths",
    )
    return parser


def validate_args(parser: ArgumentParser, args: Namespace) -> None:
    if not args.path.is_dir():
        parser.error(f"path is not a directory: {args.path}")
    if args.mode == "audio_language" and not args.languages:
        parser.error("audio_language requires at least one --language")
    if args.mode != "audio_language" and args.languages:
        parser.error("--language is only valid with audio_language")
    if args.mode == "inconsistent_lengths":
        if args.max_difference is not None and (not math.isfinite(args.max_difference) or args.max_difference < 0):
            parser.error("--max-difference must be finite and non-negative")
    elif args.max_difference is not None:
        parser.error("--max-difference is only valid with inconsistent_lengths")


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    validate_args(parser, args)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    logging.getLogger("enzyme").setLevel(logging.CRITICAL)

    default_extensions = MATROSKA_EXTENSIONS if args.mode == "disabled_tracks" else VIDEO_EXTENSIONS
    check = MODES[args.mode]
    if args.mode == "audio_language":
        check = partial(check_audio_language, languages=set(args.languages))
    elif args.mode == "inconsistent_lengths":
        check = partial(
            check_inconsistent_lengths,
            max_difference=args.max_difference if args.max_difference is not None else 10.0,
        )
    _, errors = find_videos(
        args.path,
        check,
        args.extensions or default_extensions,
        recursive=args.recursive,
    )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
