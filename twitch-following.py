import logging
import time
import winsound
from urllib.error import URLError

from genutility.datetime import now
from genutility.twitch import TwitchAPI

if __name__ == "__main__":
    from argparse import ArgumentParser

    parser = ArgumentParser()
    parser.add_argument("username")
    parser.add_argument("clientid")
    parser.add_argument("--interval", type=int, default=120)
    args = parser.parse_args()

    watcher = TwitchAPI(args.clientid, username=args.username).watcher()

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
