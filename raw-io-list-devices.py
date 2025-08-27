# /// script
# requires-python = ">=3.8"
# dependencies = [
#     "ctypes-windows-sdk>=0.0.16",
#     "genutility",
# ]
# ///
from argparse import ArgumentParser
from pprint import pprint
from typing import Iterator

from cwinsdk.shared.guiddef import GUID
from cwinsdk.um import winioctl
from genutility.win.device import Drive, enum_device_paths, enum_disks


def enum_volumes() -> Iterator[dict]:
    device_infos, device_paths = enum_device_paths(interface_class=winioctl.GUID_DEVINTERFACE_VOLUME)
    for device_path in device_paths:
        out = {"DevicePath": device_path}
        with Drive.from_raw_path(device_path, "") as drive:
            dn = drive.get_device_number()
            out.update(dn)
            yield out


def enum_partition() -> Iterator[dict]:
    device_infos, device_paths = enum_device_paths(interface_class=winioctl.GUID_DEVINTERFACE_PARTITION)
    for device_path in device_paths:
        out = {"DevicePath": device_path}
        with Drive.from_raw_path(device_path, "") as drive:
            dn = drive.get_device_number()
            out.update(dn)
            yield out


def main() -> None:
    typemap = {"disk": enum_disks, "volume": enum_volumes, "partition": enum_partition}

    parser = ArgumentParser()
    group1 = parser.add_mutually_exclusive_group(required=False)
    group1.add_argument("--device-type", choices=typemap.keys(), help="Device type")
    group1.add_argument("--interface-class-guid", type=GUID.from_str, help="Device interface class GUID")
    group1.add_argument("--setup-class-guid", type=GUID.from_str, help="Device setup class GUID")
    group2 = parser.add_mutually_exclusive_group(required=False)
    group2.add_argument(
        "--enumerator",
        help='An identifier (ID) of a Plug and Play (PnP) enumerator. This ID can either be the value\'s globally unique identifier (GUID) or symbolic name. For example, "PCI" can be used to specify the PCI PnP value. Other examples of symbolic names for PnP values include "USB", "PCMCIA", and "SCSI".',
    )
    group2.add_argument(
        "--device-instance-id",
        help="A PnP device instance ID. When specifying a PnP device instance ID, DIGCF_DEVICEINTERFACE must be set in the Flags parameter.",
    )
    args = parser.parse_args()

    if args.device_type is not None:
        print("--- drives ---")
        for d in typemap[args.device_type]():
            pprint(d)

    elif args.setup_class_guid is not None:
        print(f"--- device setup class guid: {args.setup_class_guid} ---")
        device_infos, device_paths = enum_device_paths(setup_class=args.setup_class_guid, enumerator=args.enumerator)
        for device_info in device_infos:
            print(device_info)
        for device_path in device_paths:
            print(device_path)
    elif args.interface_class_guid is not None:
        if args.enumerator is not None:
            parser.error("--enumerator can only be used with setup-class-guid or no argument")
        print(f"--- device interface class guid: {args.interface_class_guid} ---")
        device_infos, device_paths = enum_device_paths(
            interface_class=args.interface_class_guid, device_instance_id=args.device_instance_id
        )
        for device_info in device_infos:
            print(device_info)
        for device_path in device_paths:
            print(device_path)

    else:
        print("--- all ---")
        device_infos, device_paths = enum_device_paths(enumerator=args.enumerator)
        for device_info in device_infos:
            print(device_info)
        for device_path in device_paths:
            print(device_path)


if __name__ == "__main__":
    main()
