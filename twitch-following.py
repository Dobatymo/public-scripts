# /// script
# requires-python = ">=3.8"
# dependencies = [
#     "genutility[datetime,twitch]",
# ]
# ///
import logging
import sys
import time
import winsound
from argparse import ArgumentParser
from urllib.error import HTTPError, URLError

from genutility.datetime import now
from genutility.twitch import TwitchAPI


def main():
    parser = ArgumentParser()
    parser.add_argument("username")
    parser.add_argument("clientid")
    parser.add_argument("--interval", type=int, default=120)
    args = parser.parse_args()

    try:
        watcher = TwitchAPI(args.clientid, username=args.username).watcher()
    except HTTPError as e:
        if e.status == 401:
            print("Unauthorized. Maybe the username or clientid is invalid.")
            sys.exit(1)
        raise

    def notify_started(user_id, name, title):
        print("{} started streaming '{}' at {}".format(name, title, now().isoformat(" ")))
        winsound.Beep(880, 1000)  # frequency, duration

    def notify_stopped(user_id, name):
        print("{} stopped streaming at {}".format(name, now().isoformat(" ")))

    while True:
        try:
            watcher.watch(notify_started, notify_stopped)
        except URLError:
            logging.warning("Internet error")
        except ValueError:
            logging.warning("Wrong data")

        time.sleep(args.interval)


if __name__ == "__main__":
    main()
