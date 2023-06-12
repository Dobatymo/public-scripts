from __future__ import generator_stop

from genutility.aria import DEFAULT_HOST, DEFAULT_PORT, AriaDownloader

if __name__ == "__main__":
    from argparse import ArgumentDefaultsHelpFormatter, ArgumentParser

    parser = ArgumentParser(
        description="Use an Aria2 RPC server to download files.", formatter_class=ArgumentDefaultsHelpFormatter
    )
    parser.add_argument("uri", help="URI to download")
    parser.add_argument("--host", default=DEFAULT_HOST, help="Aria2 RPC host")
    parser.add_argument("--port", default=DEFAULT_PORT, help="Aria2 RPC port")
    parser.add_argument("--secret", default="", help="Aria2 RPC secret")
    parser.add_argument("--remote-path", default=None, help="Directory of downloaded file")
    parser.add_argument("--remote-filename", default=None, help="Filename of downloaded file")
    parser.add_argument("--wait", action="store_true", help="Wait for download to finish")
    parser.add_argument("--poll", type=float, default=5.0, help="Refresh progress every x seconds")
    args = parser.parse_args()

    aria = AriaDownloader(host=args.host, port=args.port, secret=args.secret, poll=args.poll)
    gid = aria.download(args.uri, args.remote_path, args.remote_filename)
    if args.wait:
        try:
            aria.block_gid(gid)
        except KeyboardInterrupt:
            print("This script was interrupted, but the download is still running.")
