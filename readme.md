# Single scripts repository

Each file represents a single purpose application.

## files/file-part-compare

Check if the content of two binary files is the same. Different offsets into the files can be used.

## files/open-copy

Creates a temporary copy of a file and opens it with the default application for .tmp files. Was created to open .lnk files on Windows.

## files/random-file

Creates a file with random binary data.

## files/read-size

Reads a certain amount of bytes from the beginning of a file and writes it to a second file. Was created preview the content of very large text files.

## files/text-compare

Simply tests if two files are equal when read in text mode (ie. ignoring different types of newlines).

## bzip2

Basically a copy of the popular bzip2 application written in Python. Primarily created for Windows where bzip2 is not available by default.

## create-video-sheet

Creates video sheets for video files. A video sheet is an image with multiple thumbnails extracted from a video. Optional includes some file metainformation. Useful to give a preview of a video.

## find-duplicates

Find duplicates files based on binary content or semantic fingerprinting.
`pip install numpy genutility[metrics,fingerprinting] pillow metrohash-python tqdm`

## make-torrent

Creates a torrent file from a file or directory. Support various piece sizes and also the source tag used on some private trackers.

## merge-dirs

Merge the contents of two directories into one. Kind of replicates the behaviour of the Windows Explorer which is not possible with `mv` on Linux.

## video-grabs-screenshots

Grabs fullsize screenshots from video files.
