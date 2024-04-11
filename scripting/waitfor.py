import logging
import sys
from time import sleep

import requests
from genutility.iter import range_count
from requests.exceptions import ConnectionError, HTTPError, InvalidURL, MissingSchema, Timeout, URLRequired

__version__ = "0.1"

if __name__ == "__main__":
    from argparse import ArgumentParser

    parser = ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--seconds", metavar="N", type=float, help="Wait for N seconds")
    group.add_argument("--url", type=str, help="Wait until connection to URL succeeds")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose output")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument(
        "--max-tries",
        metavar="N",
        type=int,
        default=None,
        help="Specifies the maximum number of attempts. Infinite if not given.",
    )
    parser.add_argument(
        "--status-codes",
        metavar="N",
        nargs="+",
        type=int,
        default=set(),
        help="Acceptable HTTP status codes. If not specified all status codes are accepted.",
    )
    parser.add_argument("--ssl-insecure", action="store_true", help="Don't verify SSL certificates.")
    args = parser.parse_args()

    status_codes = set(args.status_codes)

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    if args.seconds:
        sleep(args.seconds)

    elif args.url:
        for i in range_count(0, args.max_tries):
            try:
                r = requests.head(args.url, allow_redirects=False, verify=not args.ssl_insecure)
                r.raise_for_status()
                sys.exit(0)
            except (ConnectionError, Timeout) as e:
                logging.info("[%d] Connection failed: %s", i, e)
            except HTTPError as e:
                if not status_codes or e.response.status_code in status_codes:
                    sys.exit(0)
                logging.info("[%d] Invalid HTTP status code: %s", i, e.response.status_code)
            except (URLRequired, MissingSchema, InvalidURL) as e:
                parser.error(str(e))

            sleep(1)

        sys.exit(1)
