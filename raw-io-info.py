import logging
from argparse import ArgumentDefaultsHelpFormatter, ArgumentParser
from ctypes import byref, create_unicode_buffer
from ctypes.wintypes import DWORD
from enum import IntEnum, IntFlag
from shutil import disk_usage

from cwinsdk.um import WinBase, winnt
from cwinsdk.um.fileapi import GetDriveTypeW, GetVolumeInformationW
from genutility.win.device import (
    Drive,
    Volume,
    find_volume_mount_points,
    find_volumes,
    get_drive_size_from_geometry,
    get_logical_drives,
    get_volume_name,
    get_volume_path_names,
    is_logical_drive,
    is_logical_drive_arg,
    is_volume_guid_path,
    is_volume_guid_path_arg,
    query_dos_devices,
)


class FileSystemFlags(IntFlag):
    CASE_SENSITIVE_SEARCH = winnt.FILE_CASE_SENSITIVE_SEARCH
    CASE_PRESERVED_NAMES = winnt.FILE_CASE_PRESERVED_NAMES
    UNICODE_ON_DISK = winnt.FILE_UNICODE_ON_DISK
    PERSISTENT_ACLS = winnt.FILE_PERSISTENT_ACLS
    FILE_COMPRESSION = winnt.FILE_FILE_COMPRESSION
    VOLUME_QUOTAS = winnt.FILE_VOLUME_QUOTAS
    SUPPORTS_SPARSE_FILES = winnt.FILE_SUPPORTS_SPARSE_FILES
    SUPPORTS_REPARSE_POINTS = winnt.FILE_SUPPORTS_REPARSE_POINTS
    SUPPORTS_REMOTE_STORAGE = winnt.FILE_SUPPORTS_REMOTE_STORAGE
    RETURNS_CLEANUP_RESULT_INFO = winnt.FILE_RETURNS_CLEANUP_RESULT_INFO
    SUPPORTS_POSIX_UNLINK_RENAME = winnt.FILE_SUPPORTS_POSIX_UNLINK_RENAME
    VOLUME_IS_COMPRESSED = winnt.FILE_VOLUME_IS_COMPRESSED
    SUPPORTS_OBJECT_IDS = winnt.FILE_SUPPORTS_OBJECT_IDS
    SUPPORTS_ENCRYPTION = winnt.FILE_SUPPORTS_ENCRYPTION
    NAMED_STREAMS = winnt.FILE_NAMED_STREAMS
    READ_ONLY_VOLUME = winnt.FILE_READ_ONLY_VOLUME
    SEQUENTIAL_WRITE_ONCE = winnt.FILE_SEQUENTIAL_WRITE_ONCE
    SUPPORTS_TRANSACTIONS = winnt.FILE_SUPPORTS_TRANSACTIONS
    SUPPORTS_HARD_LINKS = winnt.FILE_SUPPORTS_HARD_LINKS
    SUPPORTS_EXTENDED_ATTRIBUTES = winnt.FILE_SUPPORTS_EXTENDED_ATTRIBUTES
    SUPPORTS_OPEN_BY_FILE_ID = winnt.FILE_SUPPORTS_OPEN_BY_FILE_ID
    SUPPORTS_USN_JOURNAL = winnt.FILE_SUPPORTS_USN_JOURNAL
    SUPPORTS_INTEGRITY_STREAMS = winnt.FILE_SUPPORTS_INTEGRITY_STREAMS
    SUPPORTS_BLOCK_REFCOUNTING = winnt.FILE_SUPPORTS_BLOCK_REFCOUNTING
    SUPPORTS_SPARSE_VDL = winnt.FILE_SUPPORTS_SPARSE_VDL
    DAX_VOLUME = winnt.FILE_DAX_VOLUME
    SUPPORTS_GHOSTING = winnt.FILE_SUPPORTS_GHOSTING


GPT_ATTRIBUTE_PLATFORM_REQUIRED = 0x0000000000000001
GPT_BASIC_DATA_ATTRIBUTE_NO_DRIVE_LETTER = 0x8000000000000000
GPT_BASIC_DATA_ATTRIBUTE_HIDDEN = 0x4000000000000000
GPT_BASIC_DATA_ATTRIBUTE_SHADOW_COPY = 0x2000000000000000
GPT_BASIC_DATA_ATTRIBUTE_READ_ONLY = 0x1000000000000000


class GptAttributeFlags(IntFlag):
    PLATFORM_REQUIRED = GPT_ATTRIBUTE_PLATFORM_REQUIRED
    NO_DRIVE_LETTER = GPT_BASIC_DATA_ATTRIBUTE_NO_DRIVE_LETTER
    HIDDEN = GPT_BASIC_DATA_ATTRIBUTE_HIDDEN
    SHADOW_COPY = GPT_BASIC_DATA_ATTRIBUTE_SHADOW_COPY
    READ_ONLY = GPT_BASIC_DATA_ATTRIBUTE_READ_ONLY


def volume_info(path: str) -> dict:
    assert is_logical_drive(path)

    VolumeNameSize = 1024
    VolumeNameBuffer = create_unicode_buffer(VolumeNameSize)
    VolumeSerialNumber = DWORD()
    MaximumComponentLength = DWORD()
    dwFileSystemFlags = DWORD()

    FileSystemNameSize = 1024
    FileSystemNameBuffer = create_unicode_buffer(FileSystemNameSize)

    GetVolumeInformationW(
        path,
        VolumeNameBuffer,
        VolumeNameSize,
        byref(VolumeSerialNumber),
        byref(MaximumComponentLength),
        byref(dwFileSystemFlags),
        FileSystemNameBuffer,
        FileSystemNameSize,
    )

    return {
        "VolumeName": VolumeNameBuffer.value,
        "VolumeSerialNumber": VolumeSerialNumber.value,
        "MaximumComponentLength": MaximumComponentLength.value,
        "FileSystemFlags": FileSystemFlags(dwFileSystemFlags.value),
        "FileSystemName": FileSystemNameBuffer.value,
    }


def print_partition_info(p: dict) -> None:
    p_style = p["PartitionStyle"]
    if p_style == "MBR":
        print(
            " #{} {} Boot={} Type={!r} Service={} Start={} Length={}".format(
                p["PartitionNumber"],
                p["PartitionStyle"],
                p["Mbr"]["BootIndicator"],
                p["Mbr"]["PartitionType"],
                p["IsServicePartition"],
                p["StartingOffset"],
                p["PartitionLength"],
            )
        )
    elif p_style == "GPT":
        print(
            " #{} {} Type={!r} Service={} Start={} Length={} Name={!r} Attributes={!r}".format(
                p["PartitionNumber"],
                p["PartitionStyle"],
                p["Gpt"]["PartitionType"],
                p["IsServicePartition"],
                p["StartingOffset"],
                p["PartitionLength"],
                p["Gpt"]["Name"],
                GptAttributeFlags(p["Gpt"]["Attributes"]),
            )
        )
    else:
        print(" Raw disk")


def print_drive_layout(drive_layout: dict) -> None:
    print("drive_layout:")

    for p_style, layout in drive_layout.items():
        print(f" Partition style: {p_style}")
        if p_style == "MBR":
            print(f" Signature={layout['Signature']:X} CheckSum={layout['CheckSum']:X}")
            for p in layout["PartitionEntry"]:
                print_partition_info(p)
        elif p_style == "GPT":
            print(f" DiskId={layout['DiskId']!r}")
            for p in layout["PartitionEntry"]:
                print_partition_info(p)


def print_volume(vol: Volume):
    print(f"length: {vol.length_info()} (same as partition_info)")
    try:
        print("AlignmentProperty:", vol.get_alignment())
    except OSError as e:
        if e.winerror == 1:
            print("AlignmentProperty: unsupported")
        else:
            raise
    print("partition_info:")
    print_partition_info(vol.partition_info())
    try:
        print_drive_layout(vol.drive_layout())
    except OSError as e:
        if e.winerror == 1:
            print("drive_layout: unsupported")
        else:
            raise
    print("get_device_number:", vol.get_device_number())


def print_volume_by_guid_path(volume_guid_path) -> None:
    assert is_volume_guid_path(volume_guid_path)

    print(f"# Volume GUID path: {volume_guid_path}")
    try:
        mount_points = list(find_volume_mount_points(volume_guid_path))
        print(f"volume_mount_points: {mount_points}")
    except OSError as e:
        print(f"volume_mount_points: {e}")

    try:
        volume_path_names = get_volume_path_names(volume_guid_path)
        print(f"volume_path_names: {volume_path_names}")
    except OSError as e:
        print(f"volume_path_names: {e}")

    try:
        vol = Volume.from_volume_guid_path(volume_guid_path)
    except OSError as e:
        print(f"Volume.from_volume_guid_path: {e}")
    else:
        with vol:
            print_volume(vol)


def show_volumes() -> None:
    for volume_guid_path in find_volumes():
        print_volume_by_guid_path(volume_guid_path)
        print()


def query_volume_by_guid_path(volume_guid_path: str) -> None:
    print_volume_by_guid_path(volume_guid_path)


def query_volume_by_harddisk_volume_index(harddisk_volume_index: int) -> None:
    print(f"# Harddisk volume index: {harddisk_volume_index}")

    try:
        vol = Volume.from_harddisk_volume_index(harddisk_volume_index)
    except OSError as e:
        print(f"Volume.from_harddisk_volume_index: {e}")
    else:
        with vol:
            print_volume(vol)


class DriveTypeEnum(IntEnum):
    UNKNOWN = WinBase.DRIVE_UNKNOWN
    NO_ROOT_DIR = WinBase.DRIVE_NO_ROOT_DIR
    REMOVABLE = WinBase.DRIVE_REMOVABLE
    FIXED = WinBase.DRIVE_FIXED
    REMOTE = WinBase.DRIVE_REMOTE
    CDROM = WinBase.DRIVE_CDROM
    RAMDISK = WinBase.DRIVE_RAMDISK


def print_volume_by_logical_drive(logical_drive) -> None:
    print(f"# Logical drive: {logical_drive}")

    handle_size = disk_usage(logical_drive).total
    print(f"size: {handle_size} (normal handle size / windows explorer)")

    volume_guid_path = get_volume_name(logical_drive)
    print(f"Volume GUID path: {volume_guid_path}")

    drive_type = DriveTypeEnum(GetDriveTypeW(logical_drive))
    print(f"Drive type: {drive_type.name}")

    try:
        vol = Volume.from_logical_drive(logical_drive)
    except OSError as e:
        print(f"Volume.from_logical_drive: {e}")
    else:
        with vol:
            print_volume(vol)

    print(volume_info(logical_drive))


def print_drive(drive: Drive):
    geo = drive.drive_geometry()
    print(f"size: {get_drive_size_from_geometry(geo)} (incorrect)")
    print(f"length: {drive.length_info()}")
    try:
        print(f"AlignmentProperty: {drive.get_alignment()}")
    except OSError as e:
        if e.winerror == 1:
            print("AlignmentProperty: unsupported")
        else:
            raise
    print(f"capacity: {drive.get_capacity()}")
    print(f"geometry: {geo}")
    print_drive_layout(drive.drive_layout())
    try:
        print(f"seek_penalty: {drive.get_seek_penalty()}")
    except OSError as e:
        print(f"seek_penalty: {e}")
    print(f"adapter: {drive.get_adapter()}")
    try:
        print(f"temperature: {drive.get_temperature()}")
    except OSError as e:
        print(f"temperature: {e}")


def print_physical_drive_by_index(driveidx) -> None:
    print(f"# Physical drive index: {driveidx}")

    try:
        drive = Drive.from_physical_drive_index(driveidx)
    except OSError as e:
        if e.winerror == 2:
            raise
        else:
            print(f"Drive.from_physical_drive_index: {e}")
    else:
        with drive:
            print_drive(drive)


def query_volume_by_logical_drive(logical_drive: str) -> None:
    print_volume_by_logical_drive(logical_drive)


def query_physical_drive_by_index(driveidx: int) -> None:
    print_physical_drive_by_index(driveidx)


def query_physical_drive_by_hardware(path: str) -> None:
    print(f"# Hardware path: {path}")

    try:
        drive = Drive.from_hardware_path(path)
    except OSError as e:
        if e.winerror == 2:
            raise
        else:
            print(f"Drive.from_hardware_path: {e}")
    else:
        with drive:
            print_drive(drive)


def show_logical_drives() -> None:
    for logical_drive in get_logical_drives():
        try:
            print_volume_by_logical_drive(logical_drive)
        except PermissionError as e:
            if e.winerror == 21:  # Das Gerät ist nicht bereit
                print(f"ERROR ACCESSING DRIVE. WINERROR: {e.winerror}")
            else:
                raise
        print()


def show_physical_drives() -> None:
    for driveidx in range(99):
        try:
            print_physical_drive_by_index(driveidx)
            print()
        except FileNotFoundError:
            break


if __name__ == "__main__":
    parser = ArgumentParser(formatter_class=ArgumentDefaultsHelpFormatter)
    group1 = parser.add_mutually_exclusive_group(required=True)
    group1.add_argument("--show-logical-drives", action="store_true")
    group1.add_argument("--show-volumes", action="store_true")
    group1.add_argument("--query-volume-by-logical-drive", type=is_logical_drive_arg, metavar="LOGICAL_DRIVE")
    group1.add_argument("--query-volume-by-guid-path", type=is_volume_guid_path_arg, metavar="VOLUME_GUID_PATH")
    group1.add_argument("--query-volume-by-harddisk-volume-index", type=int, metavar="HARDDISK_VOLUME_INDEX")
    group1.add_argument("--show-physical-drives", action="store_true")
    group1.add_argument("--query-physical-drive-by-index", type=int, metavar="PHYSICAL_DRIVE_INDEX")
    group1.add_argument("--query-physical-drive-by-hardware", type=str, metavar="PHYSICAL_DRIVE_INDEX")
    group1.add_argument("--query-dos-devices", action="store_true")
    group1.add_argument("--query-dos-device", metavar="PATH")
    args = parser.parse_args()

    try:
        if args.show_logical_drives:
            show_logical_drives()
        elif args.show_volumes:
            show_volumes()
        elif args.query_volume_by_logical_drive:
            query_volume_by_logical_drive(args.query_volume_by_logical_drive)
        elif args.query_volume_by_guid_path:
            query_volume_by_guid_path(args.query_volume_by_guid_path)
        elif args.query_volume_by_harddisk_volume_index is not None:
            query_volume_by_harddisk_volume_index(args.query_volume_by_harddisk_volume_index)
        elif args.show_physical_drives:
            show_physical_drives()
        elif args.query_physical_drive_by_index is not None:
            query_physical_drive_by_index(args.query_physical_drive_by_index)
        elif args.query_physical_drive_by_hardware is not None:
            query_physical_drive_by_hardware(args.query_physical_drive_by_hardware)

        elif args.query_dos_devices:
            print(query_dos_devices())
        elif args.query_dos_device:
            print(query_dos_devices(args.query_dos_device))

    except PermissionError:
        logging.exception("Admin permissions probably needed")
