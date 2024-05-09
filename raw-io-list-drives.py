from ctypes import byref, sizeof
from ctypes.wintypes import DWORD
from typing import Iterator

from cwinsdk import struct2dict
from cwinsdk.shared.winerror import ERROR_INSUFFICIENT_BUFFER, ERROR_NO_MORE_ITEMS
from cwinsdk.um import winnt
from cwinsdk.um.fileapi import OPEN_EXISTING, CreateFileW
from cwinsdk.um.handleapi import CloseHandle
from cwinsdk.um.setupapi import (
    _SP_DEVICE_INTERFACE_DETAIL_DATA_W,
    DIGCF_DEVICEINTERFACE,
    DIGCF_PRESENT,
    SP_DEVICE_INTERFACE_DATA,
    SP_DEVICE_INTERFACE_DETAIL_DATA_W,
    SetupDiDestroyDeviceInfoList,
    SetupDiEnumDeviceInterfaces,
    SetupDiGetClassDevsW,
    SetupDiGetDeviceInterfaceDetailW,
)
from cwinsdk.um.winioctl import GUID_DEVINTERFACE_DISK, IOCTL_STORAGE_GET_DEVICE_NUMBER, STORAGE_DEVICE_NUMBER
from genutility.win.device import EMPTY_BUFFER, MyDeviceIoControl


def enum_drives() -> Iterator[dict]:
    hDevInfo = SetupDiGetClassDevsW(byref(GUID_DEVINTERFACE_DISK), None, None, DIGCF_PRESENT | DIGCF_DEVICEINTERFACE)

    try:
        requiredSize = DWORD()
        MemberIndex = 0

        DeviceInterfaceData = SP_DEVICE_INTERFACE_DATA()
        DeviceInterfaceData.cbSize = sizeof(SP_DEVICE_INTERFACE_DATA)

        while True:
            try:
                SetupDiEnumDeviceInterfaces(
                    hDevInfo, None, byref(GUID_DEVINTERFACE_DISK), MemberIndex, byref(DeviceInterfaceData)
                )
            except OSError as e:
                if e.winerror == ERROR_NO_MORE_ITEMS:
                    break
                raise
            assert DeviceInterfaceData.InterfaceClassGuid == GUID_DEVINTERFACE_DISK
            MemberIndex += 1

            try:
                SetupDiGetDeviceInterfaceDetailW(
                    hDevInfo, byref(DeviceInterfaceData), None, 0, byref(requiredSize), None
                )
            except OSError as e:
                if e.winerror == ERROR_INSUFFICIENT_BUFFER:
                    pass
                else:
                    raise

            size = requiredSize.value - sizeof(SP_DEVICE_INTERFACE_DETAIL_DATA_W)
            DeviceInterfaceDetailData = _SP_DEVICE_INTERFACE_DETAIL_DATA_W(size)()
            DeviceInterfaceDetailData.cbSize = sizeof(SP_DEVICE_INTERFACE_DETAIL_DATA_W)

            SetupDiGetDeviceInterfaceDetailW(
                hDevInfo, byref(DeviceInterfaceData), byref(DeviceInterfaceDetailData), requiredSize, None, None
            )

            out = {"DevicePath": DeviceInterfaceDetailData.DevicePath}

            hDisk = CreateFileW(
                DeviceInterfaceDetailData.DevicePath,
                0,  # no access needed
                winnt.FILE_SHARE_READ | winnt.FILE_SHARE_WRITE,
                None,
                OPEN_EXISTING,
                winnt.FILE_ATTRIBUTE_NORMAL,
                None,
            )
            try:
                InBuffer = EMPTY_BUFFER()
                OutBuffer = STORAGE_DEVICE_NUMBER()
                MyDeviceIoControl(hDisk, IOCTL_STORAGE_GET_DEVICE_NUMBER, InBuffer, OutBuffer)
                out.update(struct2dict(OutBuffer))
                yield out
            finally:
                CloseHandle(hDisk)

    finally:
        SetupDiDestroyDeviceInfoList(hDevInfo)


if __name__ == "__main__":
    from pprint import pprint

    for d in enum_drives():
        pprint(d)
