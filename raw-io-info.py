# /// script
# requires-python = ">=3.8"
# dependencies = [
#     "ctypes-windows-sdk>=0.0.16",
#     "genutility",
#     "rich",
# ]
# ///
import logging
from argparse import ArgumentDefaultsHelpFormatter, ArgumentParser
from ctypes import byref, create_unicode_buffer
from ctypes.wintypes import DWORD
from enum import Enum, Flag, IntEnum, IntFlag
from shutil import disk_usage
from typing import Dict, List, Optional

from cwinsdk.shared import winerror
from cwinsdk.shared.guiddef import GUID
from cwinsdk.um import winbase, winioctl, winnt
from cwinsdk.um.fileapi import GetDriveTypeW, GetVolumeInformationW
from genutility.win.device import (
    DEVICE_TYPE,
    FILESYSTEM_STATISTICS_TYPE,
    Drive,
    Volume,
    enum_cdrom,
    enum_device_paths,
    enum_disks,
    find_volume_mount_points,
    find_volumes,
    get_logical_drives,
    get_volume_name,
    get_volume_path_names,
    is_logical_drive,
    is_logical_drive_arg,
    is_volume_guid_path,
    is_volume_guid_path_arg,
    query_dos_devices,
)
from rich.columns import Columns
from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table


def test_permissions() -> bool:
    path = "\\\\?\\SystemPartition"
    try:
        with Volume.from_raw_path(path, "r"):
            return True
    except PermissionError:
        with Volume.from_raw_path(path, ""):
            return False


is_admin = test_permissions()


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


class GptAttributeFlags(IntFlag):
    PLATFORM_REQUIRED = winioctl.GPT_ATTRIBUTE_PLATFORM_REQUIRED
    NO_BLOCK_IO_PROTOCOL = winioctl.GPT_ATTRIBUTE_NO_BLOCK_IO_PROTOCOL
    LEGACY_BIOS_BOOTABLE = winioctl.GPT_ATTRIBUTE_LEGACY_BIOS_BOOTABLE
    NO_DRIVE_LETTER = winioctl.GPT_BASIC_DATA_ATTRIBUTE_NO_DRIVE_LETTER
    HIDDEN = winioctl.GPT_BASIC_DATA_ATTRIBUTE_HIDDEN
    SHADOW_COPY = winioctl.GPT_BASIC_DATA_ATTRIBUTE_SHADOW_COPY
    READ_ONLY = winioctl.GPT_BASIC_DATA_ATTRIBUTE_READ_ONLY
    OFFLINE = winioctl.GPT_BASIC_DATA_ATTRIBUTE_OFFLINE
    DAX = winioctl.GPT_BASIC_DATA_ATTRIBUTE_DAX
    SERVICE = winioctl.GPT_BASIC_DATA_ATTRIBUTE_SERVICE


class DriveTypeEnum(IntEnum):
    UNKNOWN = winbase.DRIVE_UNKNOWN
    NO_ROOT_DIR = winbase.DRIVE_NO_ROOT_DIR
    REMOVABLE = winbase.DRIVE_REMOVABLE
    FIXED = winbase.DRIVE_FIXED
    REMOTE = winbase.DRIVE_REMOTE
    CDROM = winbase.DRIVE_CDROM
    RAMDISK = winbase.DRIVE_RAMDISK


def volume_info(path: str) -> dict:
    """Returns information about the volume.
    `path` is a logical drive and requires a trailing backslash, eg. `C:\\`
    """

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


def get_partition_size(vol: Volume) -> int:
    return vol.partition_info()["PartitionLength"]


def print_partition_info(p: dict) -> None:
    p_style = p["PartitionStyle"]
    if p_style == "MBR":
        print(
            " #{} {} {} Boot={} Type={!r} Service={} Start={} Length={}".format(
                p["PartitionNumber"],
                p["Mbr"]["PartitionId"],
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
            " #{} {} {} Type={!r} Service={} Start={} Length={} Name={!r} Attributes={!r}".format(
                p["PartitionNumber"],
                p["Gpt"]["PartitionId"],
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


def print_volume_by_guid_path(volume_guid_path) -> None:
    assert is_volume_guid_path(volume_guid_path)

    print(f"# Volume GUID path: {volume_guid_path}")
    try:
        mount_points = list(find_volume_mount_points(volume_guid_path))
        print(f"volume mount points: {mount_points}")
    except OSError as e:
        print(f"volume mount points: {e}")

    try:
        volume_path_names = get_volume_path_names(volume_guid_path)
        print(f"volume path names: {volume_path_names}")
    except OSError as e:
        print(f"volume path_names: {e}")

    drive_type = DriveTypeEnum(GetDriveTypeW(volume_guid_path))
    print(f"Drive type: {drive_type.name}")

    dos_device_paths = query_dos_devices(volume_guid_path[4:-1])  # remove \\?\ and trailing slash
    print(f"DOS device paths: {dos_device_paths}")

    mode = "r" if is_admin else ""
    try:
        vol = Volume.from_volume_guid_path(volume_guid_path, mode)
    except OSError as e:
        print(f"Volume.from_volume_guid_path: {e}")
    else:
        with vol:
            _print_all(vol)


def drive_to_volume_map() -> Dict[str, int]:
    out: Dict[str, int] = {}
    for volume_guid_path in find_volumes():
        try:
            with Volume.from_volume_guid_path(volume_guid_path) as vol:
                out[volume_guid_path] = vol.get_device_number()
        except OSError as e:
            if e.winerror in (21,):
                logging.warning("Couldn't get device number for %s", volume_guid_path)
            else:
                raise
    return out


def show_volumes() -> None:
    for volume_guid_path in find_volumes():
        print_volume_by_guid_path(volume_guid_path)
        print()


def query_volume_by_guid_path(volume_guid_path: str) -> None:
    print_volume_by_guid_path(volume_guid_path)


def query_volume_by_harddisk_volume_index(harddisk_volume_index: int) -> None:
    print(f"# Harddisk volume index: {harddisk_volume_index}")

    mode = "r" if is_admin else ""
    try:
        vol = Volume.from_harddisk_volume_index(harddisk_volume_index, mode)
    except OSError as e:
        print(f"Volume.from_harddisk_volume_index: {e}")
    else:
        with vol:
            _print_all(vol)


def print_volume_by_logical_drive(logical_drive) -> None:
    print(f"# Logical drive: {logical_drive}")

    try:
        handle_size = disk_usage(logical_drive).total
        print(f"size: {handle_size} (normal handle size / windows explorer)")
    except OSError as e:
        if e.winerror == 1005:
            print("Cannot use disk_usage on raw filesystem")
        else:
            raise
    volume_guid_path = get_volume_name(logical_drive)
    print(f"Volume GUID path: {volume_guid_path}")

    dos_device_paths = query_dos_devices(logical_drive[:-1])  # remove trailing slash
    print(f"DOS device paths: {dos_device_paths}")

    drive_type = DriveTypeEnum(GetDriveTypeW(logical_drive))
    print(f"Drive type: {drive_type.name}")

    mode = "r" if is_admin else ""
    try:
        vol = Volume.from_logical_drive(logical_drive, mode)
    except OSError as e:
        print(f"Volume.from_logical_drive: {e}")
    else:
        with vol:
            _print_all(vol)

    try:
        print(volume_info(logical_drive))
    except OSError as e:
        if e.winerror == 1005:
            print("Cannot use volume_info on raw filesystem")
        else:
            raise


def print_disk_by_type_and_index(drive_type: str, drive_index: int) -> None:
    print(f"# {drive_type} index: {drive_index}")

    mode = "r" if is_admin else ""
    try:
        drive = Drive.from_drive_type_and_index(drive_type, drive_index, mode)
    except OSError as e:
        if e.winerror == 2:
            raise
        else:
            print(f"Drive.from_drive_type_and_index: {e}")
    else:
        with drive:
            _print_all(drive)


def query_volume_by_logical_drive(logical_drive: str) -> None:
    print_volume_by_logical_drive(logical_drive)


def query_disk_by_type_and_index(drive_type: str, drive_index: int) -> None:
    print_disk_by_type_and_index(drive_type, drive_index)


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


def show_disks() -> None:
    for d in enum_disks():
        path = d["DevicePath"]
        print(f"# DISK: {path}")
        print_disk_raw(path)
        print()

    for d in enum_cdrom():
        path = d["DevicePath"]
        print(f"# CDROM: {path}")
        print_disk_raw(path)
        print()


def get_filesystem_statistics_json(stats: List[dict]) -> dict:
    total_read_user = sum(cpu["fss"]["UserFileReadBytes"] for cpu in stats)
    total_write_user = sum(cpu["fss"]["UserFileWriteBytes"] for cpu in stats)
    total_read_meta = sum(cpu["fss"]["MetaDataReadBytes"] for cpu in stats)
    total_write_meta = sum(cpu["fss"]["MetaDataWriteBytes"] for cpu in stats)

    fs_type = FILESYSTEM_STATISTICS_TYPE(stats[0]["fss"]["FileSystemType"])

    return {
        "Filesystem": fs_type,
        "Total user read": total_read_user,
        "Total user write": total_write_user,
        "Total meta read": total_read_meta,
        "Total meta write": total_write_meta,
    }


def print_filesystem_statistics(stats: List[dict]) -> None:
    d = get_filesystem_statistics_json(stats)

    print(
        f"""filesystem_statistics:
  type: {d["Filesystem"]}, user read: {d["Total user read"]}, user write: {d["Total user write"]}, meta read: {d["Total meta read"]}, meta write: {d['"Total meta write']} bytes"""
    )


device_type_path_map = {DEVICE_TYPE.DISK: "PhysicalDrive"}  # not DEVICE_TYPE.CD_ROM: "CdRom"


def _get_path(DeviceType: DEVICE_TYPE, DeviceNumber: int) -> Optional[str]:
    if DeviceType in device_type_path_map:
        name = device_type_path_map[DeviceType]
        return f"\\\\?\\{name}{DeviceNumber}"
    return None


def _print_all(handle) -> None:
    assert isinstance(handle, (Drive, Volume))

    device_number = handle.get_device_number()

    if isinstance(handle, Drive):
        path = _get_path(device_number["DeviceType"], device_number["DeviceNumber"])
        if path is not None:
            print("path:", path)

            m = drive_to_volume_map()
            m = {v["PartitionNumber"]: k for k, v in m.items() if v["DeviceNumber"] == device_number["DeviceNumber"]}
            print(m)

            dos_device_paths = query_dos_devices(path[4:])
            print("dos_device_paths:", dos_device_paths)
        else:
            print("Unsupported device type", device_number["DeviceType"])

    print("get_device_number:", device_number)

    if isinstance(handle, Drive):
        print(handle.sqp_adapter())
        try:
            print("sqp_alignment:", handle.sqp_alignment())
        except OSError as e:
            print("sqp_alignment:", e)
        try:
            print("sqp_seek_penalty:", handle.sqp_seek_penalty())
        except OSError as e:
            print("sqp_seek_penalty:", e)
        try:
            print("sqp_trim_enabled:", handle.sqp_trim_enabled())
        except OSError as e:
            print("sqp_trim_enabled:", e)

    try:
        print("partition_info:")
        pi = handle.partition_info()
        print_partition_info(pi)
    except OSError as e:
        print("partition_info:", e)

    if isinstance(handle, Drive):
        try:
            print_drive_layout(handle.drive_layout())
        except OSError as e:
            print("drive_layout:", e)
        try:
            print("sqp_temperature:", handle.sqp_temperature())
        except OSError as e:
            print("sqp_temperature:", e)

    try:
        li = handle.length_info()
        print("length_info:", li)
    except OSError as e:
        print("length_info:", e)

    assert li == pi["PartitionLength"]

    if isinstance(handle, Drive):
        try:
            print("read_capacity:", handle.read_capacity())
        except OSError as e:
            print("read_capacity:", e)
        try:
            print("drive_geometry:", handle.drive_geometry())
        except OSError as e:
            print("drive_geometry:", e)
        try:
            print("sqp_device:", handle.sqp_device())
        except OSError as e:
            print("sqp_device:", e)
        except RuntimeError as e:
            print("sqp_device:", e)

    if isinstance(handle, Volume):
        try:
            print_filesystem_statistics(handle.filesystem_statistics())
        except OSError as e:
            print("filesystem_statistics:", e)

        try:
            print("ntfs_volume_data:", handle.ntfs_volume_data())
        except OSError as e:
            print("ntfs_volume_data:", e)

        try:
            print("is_mounted:", handle.is_mounted())
        except OSError as e:
            print("is_mounted:", e)


def query_volume_raw(path: str) -> None:
    try:
        if path.startswith("\\\\?\\"):
            dos_device_paths = query_dos_devices(path[4:])
        else:
            dos_device_paths = query_dos_devices(path)
        print(f"DOS device paths: {dos_device_paths}")
    except OSError as e:
        if e.winerror in (2, 6):
            pass
        else:
            raise

    mode = "r" if is_admin else ""
    with Volume.from_raw_path(path, mode) as vol:
        _print_all(vol)


def print_disk_raw(path: str) -> None:
    try:
        if path.startswith("\\\\?\\"):
            dos_device_paths = query_dos_devices(path[4:])
        else:
            dos_device_paths = query_dos_devices(path)
        print(f"DOS device paths: {dos_device_paths}")
    except OSError as e:
        if e.winerror in (2, 6):
            pass
        else:
            raise

    mode = "r" if is_admin else ""
    with Drive.from_raw_path(path, mode) as drive:
        _print_all(drive)


def query_disk_raw(path: str) -> None:
    print_disk_raw(path)


def get_json():
    device_infos, device_paths = enum_device_paths(interface_class=winioctl.GUID_DEVINTERFACE_DISK)

    out = []

    mode = "r" if is_admin else ""
    for device_path in device_paths:
        with Drive.from_raw_path(device_path, mode) as drive:
            dn = drive.get_device_number()

            simple_path = _get_path(dn["DeviceType"], dn["DeviceNumber"])
            dos_paths = query_dos_devices(device_path[4:]) + query_dos_devices(simple_path[4:])

            is_writable = drive.is_writable()

            adapter = drive.sqp_adapter()
            adapter.pop("Version")
            adapter.pop("Size")

            try:
                capacity = drive.read_capacity()  # needs read
                capacity.pop("Version")
                capacity.pop("Size")
            except OSError as e:
                logging.debug("read_capacity failed: %s", e)
                capacity = None

            try:
                predict_failure = drive.predict_failure()
            except OSError as e:
                logging.debug("predict_failure failed: %s", e)
                predict_failure = None

            try:
                scsi_address = drive.get_scsi_address()
            except OSError as e:
                logging.debug("get_scsi_address failed: %s", e)
                scsi_address = None

            try:
                smart_version = drive.get_smart_version()
            except OSError as e:
                logging.debug("get_smart_version failed: %s", e)
                smart_version = None

            try:
                cache_information = drive.cache_information()
            except OSError as e:
                logging.debug("cache_information failed: %s", e)
                cache_information = None

            try:
                firmware_get_info = drive.firmware_get_info()
                firmware_get_info.pop("Version")
                firmware_get_info.pop("Size")
            except OSError as e:
                logging.debug("firmware_get_info failed: %s", e)
                firmware_get_info = None

            partition_info = drive.partition_info()
            if capacity is not None:
                assert capacity["DiskLength"] == partition_info["PartitionLength"], partition_info["PartitionLength"]
            try:
                length_info = drive.length_info()
                assert capacity["DiskLength"] == length_info, length_info
            except OSError as e:
                logging.debug("length_info failed: %s", e)

            geometry = drive.drive_geometry()
            try:
                device = drive.sqp_device()
            except RuntimeError:
                device = None

            try:
                temperature = drive.sqp_temperature()
                temperature.pop("Version")
                temperature.pop("Size")
            except OSError as e:
                logging.debug("sqp_temperature failed: %s", e)
                temperature = None

            try:
                alignment = drive.sqp_alignment()
                alignment.pop("Version")
                alignment.pop("Size")
            except OSError as e:
                logging.debug("sqp_alignment failed: %s", e)
                alignment = None
            try:
                seek_penalty = drive.sqp_seek_penalty()
            except OSError as e:
                logging.debug("sqp_seek_penalty failed: %s", e)
                seek_penalty = None
            try:
                trim_enabled = drive.sqp_trim_enabled()
            except OSError as e:
                logging.debug("sqp_trim_enabled failed: %s", e)
                trim_enabled = None

            drive_layout = drive.drive_layout()
            partitions = drive_layout.pop("PartitionEntry")
            for p in partitions:
                vol_guid_path = f"\\\\?\\Volume{{{p['PartitionId']}}}"
                harddisk_path = f"\\\\?\\Harddisk{dn['DeviceNumber']}Partition{p['PartitionNumber']}"

                vol_paths = []
                vol_dos_paths = []
                mount_points = []
                volume_path_names = []

                vol_dos_paths.extend(query_dos_devices(harddisk_path[4:]))
                vol_paths.append(harddisk_path)

                try:
                    vol_dos_paths.extend(query_dos_devices(vol_guid_path[4:]))
                    vol_paths.append(vol_guid_path)
                except FileNotFoundError:
                    pass
                else:
                    try:
                        mount_points = list(find_volume_mount_points(vol_guid_path + "\\"))
                    except OSError:
                        pass
                    try:
                        volume_path_names = get_volume_path_names(vol_guid_path + "\\")
                    except OSError:
                        pass

                p["Paths"] = vol_paths
                p["DOS Paths"] = list(set(vol_dos_paths))
                p["Mount points"] = mount_points
                p["Path names"] = volume_path_names

                with Volume.from_raw_path(harddisk_path, mode) as vol:
                    try:
                        is_mounted = vol.is_mounted()
                    except OSError as e:
                        logging.debug("is_mounted failed: %s", e)
                        is_mounted = None

                    p["Is mounted"] = is_mounted

                    try:
                        is_dirty = vol.is_dirty()
                    except OSError as e:
                        logging.debug("is_dirty failed: %s", e)
                        is_dirty = None

                    p["Is dirty"] = is_dirty

                    try:
                        filesystem_statistics = get_filesystem_statistics_json(vol.filesystem_statistics())
                    except OSError as e:
                        logging.debug("filesystem_statistics failed: %s", e)
                        filesystem_statistics = None

                    p["Filesystem statistics"] = filesystem_statistics

                    if filesystem_statistics and filesystem_statistics["Filesystem"] == FILESYSTEM_STATISTICS_TYPE.NTFS:
                        try:
                            ntfs_volume_data = vol.ntfs_volume_data()
                            ntfs_volume_data["extended"].pop("ByteCount")
                        except OSError as e:
                            logging.debug("ntfs_volume_data failed: %s", e)
                            ntfs_volume_data = None

                        p["NTFS data"] = ntfs_volume_data

            device = {
                "Device Number": dn["DeviceNumber"],
                "Device Path": device_path,
                "Simple Path": simple_path,
                "DOS Paths": dos_paths,
                "Is writable": is_writable,
                "Adapter": adapter,
                "Capacity": capacity,
                "Predict failure": predict_failure,
                "SCSI address": scsi_address,
                "SMART version": smart_version,
                "Cache": cache_information,
                "Firmware": firmware_get_info,
                "Geometry": geometry,
                "SCSI Device": device,
                "Alignment": alignment,
                "Temperature": temperature,
                "Seek penalty": seek_penalty,
                "Trim enabled": trim_enabled,
                "Drive layout": drive_layout,
                "Partitions": partitions,
            }

            out.append(device)

    return sorted(out, key=lambda device: device["Device Number"])


def make_table(d: dict, *, title=None, show_header=True):
    table = Table(title=title, show_header=show_header)

    table.add_column("Key", no_wrap=True)
    table.add_column("Value", no_wrap=False, overflow="fold")

    for k, v in d.items():
        if isinstance(v, str):
            table.add_row(k, v)
        elif isinstance(v, Flag):
            # python 3.8's name is broken, it works correctly on 3.12, no idea inbetween
            _flagname = v.name if v.name else str(v)
            assert _flagname, _flagname
            table.add_row(k, _flagname)
        elif isinstance(v, Enum):
            table.add_row(k, v.name)
        elif isinstance(v, (int, GUID)):
            table.add_row(k, str(v))
        elif v is None:
            table.add_row(k, Markdown("*N/A*"))
        elif isinstance(v, list):
            if v:
                if isinstance(v[0], str):
                    table.add_row(k, Markdown("\n".join(f"- `{i}`" for i in v)))
                elif isinstance(v[0], int):
                    table.add_row(k, Markdown(f"`{bytes(v).hex()}`"))
                elif isinstance(v[0], dict):
                    tables = [make_table(i, show_header=False) for i in v]
                    table.add_row(k, Columns(tables))
                else:
                    raise TypeError(f"Unhandled list item {k} of type {type(v[0])}")
        elif isinstance(v, dict):
            table.add_row(k, make_table(v, show_header=False))
        else:
            raise TypeError(f"Unhandled item {k} of type {type(v)}")

    return table


def show_nice():
    console = Console()

    for i, device in enumerate(get_json()):
        table = make_table(device, title=f"Harddisk #{i}", show_header=False)
        console.print(table)


if __name__ == "__main__":
    parser = ArgumentParser(formatter_class=ArgumentDefaultsHelpFormatter)
    parser.add_argument("-v", "--verbose", action="store_true", help="Output debug information")
    group1 = parser.add_mutually_exclusive_group(required=False)
    group1.add_argument("--show-logical-drives", action="store_true")
    group1.add_argument("--show-volumes", action="store_true")
    group1.add_argument("--query-volume-by-logical-drive", type=is_logical_drive_arg, metavar="LOGICAL_DRIVE")
    group1.add_argument("--query-volume-by-guid-path", type=is_volume_guid_path_arg, metavar="VOLUME_GUID_PATH")
    group1.add_argument("--query-volume-by-harddisk-volume-index", type=int, metavar="HARDDISK_VOLUME_INDEX")
    group1.add_argument("--show-disks", action="store_true")
    group1.add_argument(
        "--query-disk-by-type-and-index", nargs=2, metavar=("TYPE", "INDEX"), help="For example `PHYSICALDRIVE 0`"
    )
    group1.add_argument(
        "--query-volume-raw",
        type=str,
        metavar="PATH",
        help="Doesn't do any format checking on the path and passes it directly to the underlying functions",
    )
    group1.add_argument(
        "--query-disk-raw",
        type=str,
        metavar="PATH",
        help="Doesn't do any format checking on the path and passes it directly to the underlying functions",
    )
    group1.add_argument("--query-dos-devices", action="store_true")
    group1.add_argument("--query-dos-device", metavar="PATH")
    group1.add_argument("--query-dos-devices-storage", action="store_true")
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
        logging.getLogger("markdown_it").setLevel(logging.INFO)
    else:
        logging.basicConfig(level=logging.INFO)

    logging.info("User is admin: %s", is_admin)

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
        elif args.show_disks:
            show_disks()
        elif args.query_disk_by_type_and_index is not None:
            drive_type, drive_index = args.query_disk_by_type_and_index
            query_disk_by_type_and_index(drive_type, drive_index)
        elif args.query_volume_raw is not None:
            query_volume_raw(args.query_volume_raw)
        elif args.query_disk_raw is not None:
            query_disk_raw(args.query_disk_raw)

        elif args.query_dos_devices:
            for dos_device in query_dos_devices():
                print(dos_device)
        elif args.query_dos_devices_storage:
            dev_can_open = []
            dev_can_query = []
            dev_timeout = []
            for dos_device in query_dos_devices():
                print(dos_device)
                try:
                    with Drive.from_raw_path(f"\\\\?\\{dos_device}", timeout_ms=2000) as drive:
                        dev_can_open.append(dos_device)
                        drive.sqp_adapter()
                        dev_can_query.append(dos_device)
                except OSError as e:
                    if e.winerror == winerror.WAIT_TIMEOUT:
                        logging.warning("%s request timed out", dos_device)
                        dev_timeout.append(dos_device)
                    else:
                        logging.error("%s OSError: %s", dos_device, e)
                except RuntimeError as e:
                    logging.error("%s RuntimeError: %s", dos_device, e)

            print("# Can open device")
            for dos_device in dev_can_open:
                print(dos_device)
            print("# Can query device")
            for dos_device in dev_can_query:
                print(dos_device)
            print("# Timeout")
            for dos_device in dev_timeout:
                print(dos_device)

        elif args.query_dos_device:
            for dos_device in query_dos_devices(args.query_dos_device):
                print(dos_device)
        else:
            show_nice()

    except PermissionError:
        logging.exception("Admin permissions probably needed")
