import logging
import sys
from time import sleep
from typing import Optional, Set

import requests
from genutility.args import in_range, positive_float, positive_int
from genutility.iter import range_count
from requests.exceptions import ConnectionError, InvalidURL, MissingSchema, Timeout, URLRequired

__version__ = "0.1"


def status_code_accepted(
    status_code: int, accepted_status_codes: Optional[Set[int]], accepted_error_status_codes: Optional[Set[int]]
) -> bool:
    if accepted_status_codes is not None:
        return status_code in accepted_status_codes
    return 100 <= status_code < 400 or (
        accepted_error_status_codes is not None and status_code in accepted_error_status_codes
    )


if __name__ == "__main__":
    from argparse import ArgumentDefaultsHelpFormatter, ArgumentParser

    DEFAULT_TIMEOUT = 600

    parser = ArgumentParser(formatter_class=ArgumentDefaultsHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--seconds", metavar="N", type=positive_float, help="Wait for N seconds")
    group.add_argument("--url", type=str, help="Wait until connection to URL succeeds")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose output")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--timeout", type=positive_float, default=DEFAULT_TIMEOUT, help="Network timeout")
    parser.add_argument(
        "--max-tries",
        metavar="N",
        type=positive_int,
        default=None,
        help="Specifies the maximum number of attempts. Infinite if not given.",
    )
    status_codes = parser.add_mutually_exclusive_group()
    status_codes.add_argument(
        "--accept-status-codes",
        metavar="N",
        nargs="+",
        type=in_range(100, 600),
        help="Accept only these HTTP status codes.",
    )
    status_codes.add_argument(
        "--accept-error-status-codes",
        metavar="N",
        nargs="+",
        type=in_range(100, 600),
        help="Accept all normal HTTP status codes plus these error status codes.",
    )
    parser.add_argument("--ssl-insecure", action="store_true", help="Don't verify SSL certificates.")
    args = parser.parse_args()

    accepted_status_codes = None if args.accept_status_codes is None else set(args.accept_status_codes)
    accepted_error_status_codes = (
        None if args.accept_error_status_codes is None else set(args.accept_error_status_codes)
    )

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    if args.seconds:
        sleep(args.seconds)

    elif args.url:
        for i in range_count(0, args.max_tries):
            try:
                r = requests.head(args.url, allow_redirects=False, verify=not args.ssl_insecure, timeout=args.timeout)
                if status_code_accepted(r.status_code, accepted_status_codes, accepted_error_status_codes):
                    sys.exit(0)
                logging.info("[%d] Invalid HTTP status code: %s", i, r.status_code)
            except (ConnectionError, Timeout) as e:
                logging.info("[%d] Connection failed: %s", i, e)
            except (URLRequired, MissingSchema, InvalidURL) as e:
                parser.error(str(e))

            sleep(1)

        sys.exit(1)
