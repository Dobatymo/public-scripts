# /// script
# requires-python = ">=3.8"
# dependencies = [
#     "ctypes-windows-sdk>=0.0.16",
#     "genutility",
#     "rich",
#     "typing-extensions",
# ]
# ///
import logging
from ctypes import POINTER, Structure, cast, pointer, sizeof
from ctypes.wintypes import ULONG, USHORT
from enum import Enum, IntEnum, IntFlag
from functools import partial
from pprint import pprint
from struct import unpack
from typing import Any, Dict, Optional, Tuple, Type, TypeVar

from cwinsdk import struct2dict
from cwinsdk.km.ata import IDE_COMMAND_IDENTIFY, IDE_SMART_READ_ATTRIBUTES, IDENTIFY_DEVICE_DATA
from cwinsdk.shared import nvme
from cwinsdk.shared.minwindef import UCHAR
from cwinsdk.shared.ntdddisk import (
    CAP_SMART_CMD,
    DRIVERSTATUS,
    ENABLE_SMART,
    ID_CMD,
    IDENTIFY_BUFFER_SIZE,
    IDEREGS,
    READ_ATTRIBUTE_BUFFER_SIZE,
    READ_ATTRIBUTES,
    READ_THRESHOLD_BUFFER_SIZE,
    READ_THRESHOLDS,
    SENDCMDINPARAMS,
    SMART_CMD,
    SMART_CYL_HI,
    SMART_CYL_LOW,
)
from cwinsdk.shared.ntddscsi import (
    IOCTL_SCSI_MINIPORT,
    IOCTL_SCSI_PASS_THROUGH,
    IOCTL_SCSI_PASS_THROUGH_DIRECT,
    SCSI_IOCTL_DATA_IN,
    SCSI_PASS_THROUGH,
    SCSI_PASS_THROUGH_DIRECT,
    SRB_IO_CONTROL,
)
from cwinsdk.shared.ntddstor import STORAGE_BUS_TYPE
from cwinsdk.shared.ntdef import PVOID
from cwinsdk.shared.scsi import CDB, IOCTL_SCSI_MINIPORT_IDENTIFY, SCSIOP_ATA_PASSTHROUGH12, SCSIOP_ATA_PASSTHROUGH16
from cwinsdk.um import winioctl
from genutility.win.device import Drive, MyDeviceIoControl, enum_disks, struct_to_array
from rich.console import Console
from rich.table import Table
from typing_extensions import Buffer

DRIVE_HEAD_REG = 0xA0  # where is this from?
T = TypeVar("T", bound=Structure)

assert IDENTIFY_BUFFER_SIZE == sizeof(IDENTIFY_DEVICE_DATA) == 512
assert READ_ATTRIBUTES == IDE_SMART_READ_ATTRIBUTES
assert ID_CMD == IDE_COMMAND_IDENTIFY  # aka ATA_IDENTIFY_DEVICE, ATA_IDENTIFY
assert sizeof(SCSI_PASS_THROUGH_DIRECT) == 56  # 64-bit

# assert sizeof(NVME_COMMAND) == STORAGE_PROTOCOL_COMMAND_LENGTH_NVME

""" docs:
"SMART Attribute Overview", Jim Hatfield, Seagate
T13, "AT Attachment 8 - ATA/ATAPI Command Set (ATA8-ACS)"
"""

smart_attribute_dict = {
    0x01: "Read Error Rate",
    0x02: "Throughput Performance",
    0x03: "Spin-Up Time",
    0x04: "Start/Stop Count",
    0x05: "Reallocated Sectors Count",
    0x06: "Read Channel Margin",
    0x07: "Seek Error Rate",
    0x08: "Seek Time Performance",
    0x09: "Power-On Hours",
    0x0A: "Spin Retry Count",
    0x0B: "Recalibration Retries",
    0x0C: "Power Cycle Count",
    0x0D: "Soft Read Error Rate",
    0x16: "Current Helium Level",
    0xAA: "Available Reserved Space",
    0xAB: "SSD Program Fail Count",
    0xAC: "SSD Erase Fail Count ",
    0xAD: "SSD Wear Leveling Count ",
    0xAE: "Unexpected power loss count",
    0xAF: "Power Loss Protection Failure",
    0xB0: "Erase Fail Count",
    0xB1: "Wear Range Delta",
    0xB2: "Used Reserved Block Count",
    0xB3: "Used Reserved Block Count Total",
    0xB4: "Unused Reserved Block Count Total",
    0xB5: "Program Fail Count Total",
    0xB6: "Erase Fail Count",
    0xB7: "SATA Downshift Error Count",
    0xB8: "End-to-End error",
    0xB9: "Head Stability",
    0xBA: "Induced Op-Vibration Detection",
    0xBB: "Reported Uncorrectable Errors",
    0xBC: "Command Timeout",
    0xBD: "High Fly Writes",
    0xBE: "Temperature Difference",
    0xBF: "G-sense Error Rate",
    0xC0: "Power-off Retract Count",
    0xC1: "Load Cycle Count",
    0xC2: "Temperature",
    0xC3: "Hardware ECC Recovered",
    0xC4: "Reallocation Event Count",
    0xC5: "Current Pending Sector Count",
    0xC6: "(Offline) Uncorrectable Sector Count",
    0xC7: "UltraDMA CRC Error Count",
    0xC8: "Write Error Rate / Multi-Zone Error Rate",
    0xC9: "Soft Read Error Rate",
    0xCA: "Data Address Mark errors",
    0xCB: "Run Out Cancel",
    0xCC: "Soft ECC Correction",
    0xCD: "Thermal Asperity Rate",
    0xCE: "Flying Height",
    0xCF: "Spin High Current",
    0xD0: "Spin Buzz",
    0xD1: "Offline Seek Performance",
    0xD2: "Vibration During Write",
    0xD3: "Vibration During Write",
    0xD4: "Shock During Write",
    0xDC: "Disk Shift",
    0xDD: "G-Sense Error Rate",
    0xDE: "Loaded Hours",
    0xDF: "Load/Unload Retry Count",
    0xE0: "Load Friction",
    0xE1: "Load/Unload Cycle Count",
    0xE2: "Load In-time",
    0xE3: "Torque Amplification Count",
    0xE4: "Power-Off Retract Cycle",
    0xE6: "GMR Head Amplitude (magnetic HDDs), Drive Life Protection Status (SSDs)",
    0xE7: "Life Left (SSDs) or Temperature",
    0xE8: "Endurance Remaining or Available Reserved Space",
    0xE9: "Media Wearout Indicator (SSDs) or Power-On Hours",
    0xEA: "Average erase count AND Maximum Erase Count",
    0xEB: "Good Block Count AND System(Free) Block Count",
    0xF0: "Head Flying Hours",
    0xF1: "Total LBAs Written",
    0xF2: "Total LBAs Read",
    0xF3: "Total LBAs Written Expanded",
    0xF4: "Total LBAs Read Expanded",
    0xF9: "NAND Writes (1GiB)",
    0xFA: "Read Error Retry Rate",
    0xFB: "Minimum Spares Remaining",
    0xFC: "Newly Added Bad Flash Block",
    0xFE: "Free Fall Protection",
}


# copy of SENDCMDINPARAMS with bBuffer removed
class SENDCMDINPARAMS_NOBUF(Structure):
    _pack_ = 1
    _fields_ = [
        ("cBufferSize", ULONG),
        ("irDriveRegs", IDEREGS),
        ("bDriveNumber", UCHAR),  # according to smartmontools not needed anymore
        ("bReserved", UCHAR * 3),
        ("dwReserved", ULONG * 4),
    ]


# copy of SENDCMDOUTPARAMS with bBuffer removed
class SENDCMDOUTPARAMS_NOBUF(Structure):
    _pack_ = 1
    _fields_ = [("cBufferSize", ULONG), ("DriverStatus", DRIVERSTATUS)]


class SMART_ATTRIBUTE_FLAGS(Structure):
    _pack_ = 1
    _fields_ = [("PreFail", USHORT, 1), ("Online", USHORT, 1), ("VendorSpecific", USHORT, 4), ("Reserved", USHORT, 10)]


class _SMART_ATTRIBUTE(Structure):  # GENERAL
    _pack_ = 1
    _fields_ = [
        ("AttributeID", UCHAR),
        ("Flags", SMART_ATTRIBUTE_FLAGS),
        ("Value", UCHAR),
        ("VendorSpecific", UCHAR * 8),
    ]


class SMART_ATTRIBUTE(Structure):  # MICRON
    _pack_ = 1
    _fields_ = [
        ("AttributeID", UCHAR),
        ("Flags", SMART_ATTRIBUTE_FLAGS),
        ("CurrentValue", UCHAR),
        ("WorstValue", UCHAR),
        ("Data", UCHAR * 4),
        ("AttributeSpecific", UCHAR * 2),
        ("Threshold", UCHAR),
    ]


class SMART_THRESHOLD(Structure):
    _pack_ = 1
    _fields_ = [("AttributeID", UCHAR), ("Threshold", UCHAR), ("Reserved", UCHAR * 10)]


class SMART_ATTRIBUTE_TABLE(Structure):  # 362 bytes
    _pack_ = 1
    _fields_ = [("Version", USHORT), ("Attribute", SMART_ATTRIBUTE * 30)]  # Vendor specific


class SMART_THRESHOLD_TABLE(Structure):  # 362 bytes
    _pack_ = 1
    _fields_ = [("Version", USHORT), ("Threshold", SMART_THRESHOLD * 30)]  # Vendor specific


class DEVICE_SMART_DATA_ATTRIBUTES(Structure):
    _pack_ = 1
    _fields_ = [
        ("Attributes", SMART_ATTRIBUTE_TABLE),  # Vendor specific
        ("OfflineDataCollectionStatus", UCHAR),
        ("SelfTestExecutionStatusBytes", UCHAR),
        (
            "OfflineDataCollectionSeconds",
            UCHAR * 2,
        ),  # Total time in seconds to complete off-line data collection activity
        ("VendorSpecific", UCHAR),
        ("OfflineDataCollectionCapability", UCHAR),  # Off-line data collection capability
        ("SmartCapability", UCHAR * 2),
        ("ErrorLoggingCapability", UCHAR),
        ("VendorSpecific", UCHAR),
        ("ShortSelfTestPollingMinutes", UCHAR),  # Short self-test routine recommended polling time (in minutes)
        (
            "ExtendedSelfTestPollingMinutes",
            UCHAR,
        ),  # Extended self-test routine recommended polling time (7:0) in minutes
        (
            "ConveyanceSelfTestPollingMinutes",
            UCHAR,
        ),  # Conveyance self-test routine recommended polling time (in minutes)
        (
            "ExtendedSelfTestPollingMinutes1",
            UCHAR,
        ),  # Extended self-test routine recommended polling time (7:0) in minutes
        (
            "ExtendedSelfTestPollingMinutes2",
            UCHAR,
        ),  # Extended self-test routine recommended polling time (15:8) in minute
        ("Reserved", UCHAR * 9),
        ("VendorSpecific", UCHAR * 125),
        ("Checksum", UCHAR),  # Data structure checksum
    ]


assert sizeof(DEVICE_SMART_DATA_ATTRIBUTES) == 512
assert sizeof(DEVICE_SMART_DATA_ATTRIBUTES) == READ_ATTRIBUTE_BUFFER_SIZE


class DEVICE_SMART_DATA_THRESHOLD(Structure):
    _pack_ = 1
    _fields_ = [("Thresholds", SMART_THRESHOLD_TABLE), ("Others", UCHAR * 150)]  # Data structure checksum


assert sizeof(DEVICE_SMART_DATA_THRESHOLD) == 512
assert sizeof(DEVICE_SMART_DATA_THRESHOLD) == READ_THRESHOLD_BUFFER_SIZE

# convenience classes


class SENDCMDOUTPARAMS_ID(Structure):
    _pack_ = 1
    _anonymous_ = ("u",)
    _fields_ = [("u", SENDCMDOUTPARAMS_NOBUF), ("devid", IDENTIFY_DEVICE_DATA)]


class SENDCMDOUTPARAMS_ATTR(Structure):
    _pack_ = 1
    _anonymous_ = ("u",)
    _fields_ = [("u", SENDCMDOUTPARAMS_NOBUF), ("devsmart", DEVICE_SMART_DATA_ATTRIBUTES)]


class SENDCMDOUTPARAMS_THRESH(Structure):
    _pack_ = 1
    _anonymous_ = ("u",)
    _fields_ = [("u", SENDCMDOUTPARAMS_NOBUF), ("devsmart", DEVICE_SMART_DATA_THRESHOLD)]


class AtaError(Exception):
    pass


def spt2dict(sptb: SCSI_PASS_THROUGH_DIRECT, cdb_cls: Structure, outstruct=None) -> dict:
    """
    sptb: SCSI_PASS_THROUGH_DIRECT structure with buffer
    cdb_cls: SCSI command descriptor block to be sent structure
    """

    out = struct2dict(sptb)
    out["spt"]["Cdb"] = struct2dict(cdb_cls.from_buffer_copy(sptb.spt.Cdb))
    if outstruct is not None:
        out["spt"]["DataBuffer"] = struct2dict(cast(out["spt"]["DataBuffer"], POINTER(outstruct)).contents)
    return out


def ata_str(c_array: Buffer) -> str:
    mem = memoryview(c_array)
    interleaved = bytearray(len(mem))
    interleaved[::2] = mem[1::2]
    interleaved[1::2] = mem[0::2]
    return interleaved.decode("ascii")


class NominalFormFactorEnum(IntEnum):
    NOT_REPORTED = 0
    INCH_5_25 = 1
    INCH_3_5 = 2
    INCH_2_5 = 3
    INCH_1_8 = 4
    LESS_THAN_1_8_INCH = 5


def NominalMediaRotationRateStr(val_h: int) -> str:
    if val_h == 0x0000:
        return "Rate not reported"
    elif val_h == 0x0001:
        return "Non-rotating media"
    elif 0x0002 <= val_h <= 0x0400:
        return "Reserved"
    elif 0x0401 <= val_h <= 0xFFFE:
        return f"{val_h} rpm"
    else:  # 0xFFFF
        return "Reserved"


class MajorRevisionFlag(IntFlag):
    ACS_4 = 2048
    ACS_3 = 1024
    ACS_2 = 512
    ATA8_ACS = 256
    ATA_ATAPI_7 = 128
    ATA_ATAPI_6 = 64
    ATA_ATAPI_5 = 32
    Obsolete4 = 16
    Obsolete3 = 8
    Obsolete2 = 4
    Obsolete1 = 2
    Reserved = 1


class MinorRevisionEnum(IntEnum):
    NOT_REPORTED = 0x0000
    ATA_ATAPI_5_T13_1321D_VERSION_3 = 0x0013
    ATA_ATAPI_5_T13_1321D_VERSION_1 = 0x0015
    ATA_ATAPI_5_PUBLISHED_ANSI_INCITS_340_2000 = 0x0016
    ATA_ATAPI_6_T13_1410D_VERSION_0 = 0x0018
    ATA_ATAPI_6_T13_1410D_VERSION_3A = 0x0019
    ATA_ATAPI_6_T13_1410D_VERSION_2 = 0x001B
    ATA_ATAPI_6_T13_1410D_VERSION_1 = 0x001C
    ATA_ATAPI_6_PUBLISHED_ANSI_INCITS_361_2002 = 0x0022
    ATA_ATAPI_7_T13_1532D_VERSION_1 = 0x001A
    ATA_ATAPI_7_PUBLISHED_ANSI_INCITS_397_2005 = 0x001D
    ATA_ATAPI_7_T13_1532D_VERSION_0 = 0x001E
    ATA_ATAPI_7_T13_1532D_VERSION_4A = 0x0021
    ATA8_ACS_VERSION_3C = 0x0027
    ATA8_ACS_VERSION_6 = 0x0028
    ATA8_ACS_VERSION_4 = 0x0029
    ATA8_ACS_VERSION_3E = 0x0033
    ATA8_ACS_VERSION_4C = 0x0039
    ATA8_ACS_VERSION_3F = 0x0042
    ATA8_ACS_VERSION_3B = 0x0052
    ATA8_ACS_VERSION_2D = 0x0107
    ACS_2_REVISION_2 = 0x0031
    ACS_2_PUBLISHED_ANSI_INCITS_482_2012 = 0x0082
    ACS_2_REVISION_3 = 0x0110
    ACS_3_REVISION_3B = 0x001F
    ACS_3_REVISION_5 = 0x006D
    ACS_3_PUBLISHED_ANSI_INCITS_522_2014 = 0x010A
    ACS_3_REVISION_4 = 0x011B
    ACS_4_REVISION_5 = 0x005E
    NOT_REPORTED2 = 0xFFFF


def ata_identify_json(devid: IDENTIFY_DEVICE_DATA) -> dict:
    ModelNumber = ata_str(devid.ModelNumber).strip(" ")
    SerialNumber = ata_str(devid.SerialNumber).strip(" ")
    FirmwareRevision = ata_str(devid.FirmwareRevision).strip(" ")
    NominalFormFactor = NominalFormFactorEnum(devid.NominalFormFactor)
    NominalMediaRotationRate = NominalMediaRotationRateStr(devid.NominalMediaRotationRate)
    MajorRevision = MajorRevisionFlag(devid.MajorRevision)
    MinorRevision = MinorRevisionEnum(devid.MinorRevision)

    return {
        "ModelNumber": ModelNumber,
        "SerialNumber": SerialNumber,
        "FirmwareRevision": FirmwareRevision,
        "MajorRevision": str(MajorRevision),
        "MinorRevision": MinorRevision.name,
        "NominalFormFactor": NominalFormFactor.name,
        "NominalMediaRotationRate": NominalMediaRotationRate,
        "CryptoScrambleExtCommandSupported": devid.CryptoScrambleExtCommandSupported,
        "GeneralConfiguration.DeviceType": devid.GeneralConfiguration.DeviceType,
        "UserAddressableSectors": devid.UserAddressableSectors,
        "SerialAtaCapabilities.NCQ": devid.SerialAtaCapabilities.NCQ,
        "SerialAtaCapabilities.SataGen1": devid.SerialAtaCapabilities.SataGen1,
        "SerialAtaCapabilities.SataGen2": devid.SerialAtaCapabilities.SataGen2,
        "SerialAtaCapabilities.SataGen3": devid.SerialAtaCapabilities.SataGen3,
        "SerialAtaFeaturesSupported device sleep": devid.SerialAtaFeaturesSupported.DEVSLP,
        "CommandSetSupport.APM": devid.CommandSetSupport.AdvancedPm,
        "CommandSetSupport.SMART": devid.CommandSetSupport.SmartCommands,
        "CommandSetSupport.Acoustics": devid.CommandSetSupport.Acoustics,
    }


class Methods(Enum):
    SMART = "smart"  # winioctl.SMART_RCV_DRIVE_DATA
    ATA_PASS_THROUGH = "ata-pass-through"
    SCSI_PASS_THROUGH_12 = "scsi-pass-through-12"
    SCSI_PASS_THROUGH_DIRECT_12 = "scsi-pass-through-direct-12"
    SCSI_PASS_THROUGH_16 = "scsi-pass-through-16"
    SCSI_PASS_THROUGH_DIRECT_16 = "scsi-pass-through-direct-16"
    SCSI_MINIPORT = "scsi-miniport"
    NVME = "nvme"

    @classmethod
    def _missing_(cls, value: Any):
        assert isinstance(value, str)
        value = value.lower()
        for member in cls:
            if member.value.lower() == value:
                return member
        return None


class SmartDevice:
    handle: Optional[int]

    def __init__(self, drive: Drive) -> None:
        self.drive = drive
        self.handle = drive.handle
        self.alignment_mask = self.drive.sqp_adapter()["AlignmentMask"]
        logging.debug("AlignmentMask: %s", self.alignment_mask)

    @classmethod
    def open(cls, drive_index: int, method: Optional[Methods]) -> "SmartDevice":
        if method == Methods.SCSI_MINIPORT:
            opener = partial(Drive.from_scsi_index, drive_index)
        else:
            opener = partial(Drive.from_drive_type_and_index, "PhysicalDrive", drive_index)

        try:
            drive = opener("r+")
        except PermissionError:
            logging.warning(
                "Failed to open drive %d with read/write access. Trying with meta access only.", drive_index
            )
            drive = opener("")

        if method == Methods.SCSI_MINIPORT:
            bus = STORAGE_BUS_TYPE.enum().BusTypeScsi
        else:
            try:
                bus = drive.sqp_device()["BusType"]
            except OSError as e:
                logging.error("sqp_device failed: %s", e)
                raise

        if bus in {STORAGE_BUS_TYPE.BusTypeAta, STORAGE_BUS_TYPE.BusTypeSata}:
            try:
                gvip = drive.get_smart_version()
                if (gvip["fCapabilities"] & CAP_SMART_CMD) != CAP_SMART_CMD:
                    raise RuntimeError("Smart not supported")
            except Exception:
                drive.close()
                raise
        elif bus in (STORAGE_BUS_TYPE.BusTypeUsb, STORAGE_BUS_TYPE.BusTypeNvme):
            pass
        elif bus == STORAGE_BUS_TYPE.BusTypeFileBackedVirtual:
            raise RuntimeError("Smart not supported on virtual device")
        else:
            logging.warning("Unsupported bus: %s", bus.name)

        if method is None:
            method = {
                STORAGE_BUS_TYPE.BusTypeAta: Methods.SMART,
                STORAGE_BUS_TYPE.BusTypeSata: Methods.SMART,
                STORAGE_BUS_TYPE.BusTypeUsb: Methods.SCSI_PASS_THROUGH_DIRECT_12,
                STORAGE_BUS_TYPE.BusTypeNvme: Methods.NVME,
            }[bus]

        return {
            Methods.SMART: SmartDeviceDefault,
            Methods.SCSI_PASS_THROUGH_12: SmartDeviceSatBuffered12,
            Methods.SCSI_PASS_THROUGH_DIRECT_12: SmartDeviceSatDirect12,
            Methods.SCSI_PASS_THROUGH_16: SmartDeviceSatBuffered16,
            Methods.SCSI_PASS_THROUGH_DIRECT_16: SmartDeviceSatDirect16,
            Methods.NVME: SmartDeviceNvme,
            Methods.SCSI_MINIPORT: SmartDeviceScsi,
        }[method](drive)

    def close(self) -> None:
        self.drive.close()
        self.handle = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def drive_info(self):
        raise NotImplementedError

    def smart(self):
        raise NotImplementedError

    @classmethod
    def drive_info_json(cls, drive_info) -> dict:
        return struct2dict(drive_info)


class SmartDeviceDefault(SmartDevice):
    # if bFeaturesReg == ATA_SMART_STATUS, outbuffer contains SENDCMDINPARAMS_NOBUF + IDEREGS

    def enable_smart(self) -> None:
        assert self.handle

        cmdin = SENDCMDINPARAMS_NOBUF()
        cmdout = SENDCMDOUTPARAMS_NOBUF()

        cmdin.cBufferSize = 0
        cmdin.irDriveRegs.bFeaturesReg = ENABLE_SMART
        cmdin.irDriveRegs.bSectorCountReg = 1
        cmdin.irDriveRegs.bSectorNumberReg = 1
        cmdin.irDriveRegs.bCylLowReg = SMART_CYL_LOW
        cmdin.irDriveRegs.bCylHighReg = SMART_CYL_HI
        cmdin.irDriveRegs.bDriveHeadReg = DRIVE_HEAD_REG
        cmdin.irDriveRegs.bCommandReg = SMART_CMD

        MyDeviceIoControl(self.handle, winioctl.SMART_SEND_DRIVE_COMMAND, cmdin, cmdout)

        assert cmdout.cBufferSize == 0
        if cmdout.DriverStatus.bDriverError != 0 or cmdout.DriverStatus.bIDEError != 0:
            raise AtaError(
                f"Enabling SMART failed. DriverError: {cmdout.DriverStatus.bDriverError}, IDEError: {cmdout.DriverStatus.bIDEError}"
            )

    @classmethod
    def drive_info_json(cls, devid: IDENTIFY_DEVICE_DATA) -> dict:
        return ata_identify_json(devid)

    def drive_info(self):
        assert self.handle

        cmdin = SENDCMDINPARAMS_NOBUF()
        cmdout = SENDCMDOUTPARAMS_ID()

        cmdin.cBufferSize = IDENTIFY_BUFFER_SIZE
        cmdin.irDriveRegs.bSectorCountReg = 1
        cmdin.irDriveRegs.bSectorNumberReg = 1
        cmdin.irDriveRegs.bDriveHeadReg = DRIVE_HEAD_REG
        cmdin.irDriveRegs.bCommandReg = ID_CMD

        MyDeviceIoControl(self.handle, winioctl.SMART_RCV_DRIVE_DATA, cmdin, cmdout)

        assert cmdout.cBufferSize == IDENTIFY_BUFFER_SIZE
        if cmdout.DriverStatus.bDriverError != 0 or cmdout.DriverStatus.bIDEError != 0:
            raise AtaError(
                f"Identifying drive failed. DriverError: {cmdout.DriverStatus.bDriverError}, IDEError: {cmdout.DriverStatus.bIDEError}"
            )

        assert (
            cmdout.devid.SerialAtaFeaturesSupported.Reserved0 == 0
            and cmdout.devid.SerialAtaFeaturesEnabled.Reserved0 == 0
            and cmdout.devid.CommandSetSupport.PowerManagement == 1
            and cmdout.devid.CommandSetSupport.WordValid == 1
            and cmdout.devid.CommandSetSupport.WordValid83 == 1
        )

        return cmdout.devid

    def smart(self):
        assert self.handle

        cmdin = SENDCMDINPARAMS()
        cmdout_attr = SENDCMDOUTPARAMS_ATTR()

        cmdin.cBufferSize = READ_ATTRIBUTE_BUFFER_SIZE
        cmdin.irDriveRegs.bFeaturesReg = READ_ATTRIBUTES
        cmdin.irDriveRegs.bSectorCountReg = 1
        cmdin.irDriveRegs.bSectorNumberReg = 1
        cmdin.irDriveRegs.bCylLowReg = SMART_CYL_LOW
        cmdin.irDriveRegs.bCylHighReg = SMART_CYL_HI
        cmdin.irDriveRegs.bDriveHeadReg = DRIVE_HEAD_REG  # is this necessary
        cmdin.irDriveRegs.bCommandReg = SMART_CMD

        MyDeviceIoControl(self.handle, winioctl.SMART_RCV_DRIVE_DATA, cmdin, cmdout_attr)

        assert cmdout_attr.cBufferSize == READ_ATTRIBUTE_BUFFER_SIZE
        if cmdout_attr.DriverStatus.bDriverError != 0 or cmdout_attr.DriverStatus.bIDEError != 0:
            raise AtaError(
                f"Reading SMART attributes failed. DriverError: {cmdout_attr.DriverStatus.bDriverError}, IDEError: {cmdout_attr.DriverStatus.bIDEError}"
            )

        cmdout_thresh = SENDCMDOUTPARAMS_THRESH()

        cmdin.cBufferSize = READ_THRESHOLD_BUFFER_SIZE
        cmdin.irDriveRegs.bFeaturesReg = READ_THRESHOLDS

        MyDeviceIoControl(self.handle, winioctl.SMART_RCV_DRIVE_DATA, cmdin, cmdout_thresh)

        assert cmdout_thresh.cBufferSize == READ_THRESHOLD_BUFFER_SIZE
        if cmdout_thresh.DriverStatus.bDriverError != 0 or cmdout_thresh.DriverStatus.bIDEError != 0:
            raise AtaError(
                f"Reading SMART thresholds failed. DriverError: {cmdout_thresh.DriverStatus.bDriverError}, IDEError: {cmdout_thresh.DriverStatus.bIDEError}"
            )

        return cmdout_attr.devsmart, cmdout_thresh.devsmart


class SmartDeviceScsi(SmartDevice):
    def drive_info(self):
        # requires scsi handle r"\\.\Scsi%d:"
        # see https://stackoverflow.com/a/20967563
        # or smartmontools os_win32.cpp

        assert self.handle

        class ScsiMiniPortIdentifyIn(Structure):
            _pack_ = 1
            _fields_ = [("srbc", SRB_IO_CONTROL), ("cmdin", SENDCMDINPARAMS_NOBUF)]

        class ScsiMiniPortIdentifyOut(Structure):
            _pack_ = 1
            _fields_ = [("srbc", SRB_IO_CONTROL), ("cmdout", SENDCMDOUTPARAMS_NOBUF), ("devid", IDENTIFY_DEVICE_DATA)]

        InBuffer = ScsiMiniPortIdentifyIn()
        OutBuffer = ScsiMiniPortIdentifyOut()

        InBuffer.srbc.HeaderLength = sizeof(SRB_IO_CONTROL)
        InBuffer.srbc.Signature = (UCHAR * 8)(*b"SCSIDISK")
        InBuffer.srbc.Timeout = 60  # in seconds
        InBuffer.srbc.ControlCode = IOCTL_SCSI_MINIPORT_IDENTIFY
        InBuffer.srbc.Length = sizeof(SENDCMDOUTPARAMS_NOBUF) + sizeof(IDENTIFY_DEVICE_DATA)

        InBuffer.cmdin.cBufferSize = IDENTIFY_BUFFER_SIZE
        InBuffer.cmdin.irDriveRegs.bCommandReg = ID_CMD

        MyDeviceIoControl(self.handle, IOCTL_SCSI_MINIPORT, InBuffer, OutBuffer)

        assert OutBuffer.srbc.ReturnCode == 0, OutBuffer.srbc.ReturnCode
        assert OutBuffer.cmdout.cBufferSize == IDENTIFY_BUFFER_SIZE, OutBuffer.cmdout.cBufferSize

        return OutBuffer


class SmartDeviceSat(SmartDevice):
    """SCSI to ATA Translation (SAT).
    Communicate with ATA (or SATA) devices through a SCSI application layer.
    USB storage (or bridges) usually use SCSI.
    """

    SENSE_INFO_LEN = 128 - sizeof(SCSI_PASS_THROUGH_DIRECT)  # before: 24

    @classmethod
    def drive_info_json(cls, devid: IDENTIFY_DEVICE_DATA) -> dict:
        return ata_identify_json(devid)

    def scsi_passthrough_direct(self, cdb: CDB, outstruct: Type[T], timeout: int = 5) -> T:
        """Pass-though raw SCSI commands to SCSI device.

        `cdb`: SCSI Command Descriptor Block
        `outstruct`: Output struct class
        `timeout`: timeout in seconds

        Requires read/write (w+) access.
        """

        assert self.alignment_mask == 0

        class SCSI_PASS_THROUGH_DIRECT_WITH_BUFFER(Structure):
            _fields_ = [("spt", SCSI_PASS_THROUGH_DIRECT), ("SenseInfo", UCHAR * self.SENSE_INFO_LEN)]

        assert SCSI_PASS_THROUGH_DIRECT_WITH_BUFFER.SenseInfo.offset % 8 == 0, (
            SCSI_PASS_THROUGH_DIRECT_WITH_BUFFER.SenseInfo.offset
        )

        sptb = SCSI_PASS_THROUGH_DIRECT_WITH_BUFFER()
        DataBuf = outstruct()

        sptb.spt.Length = sizeof(SCSI_PASS_THROUGH_DIRECT)
        sptb.spt.SenseInfoLength = sizeof(sptb.SenseInfo)
        sptb.spt.DataIn = SCSI_IOCTL_DATA_IN
        sptb.spt.DataTransferLength = sizeof(outstruct)
        sptb.spt.TimeOutValue = timeout
        sptb.spt.DataBuffer = cast(pointer(DataBuf), PVOID)
        sptb.spt.SenseInfoOffset = SCSI_PASS_THROUGH_DIRECT_WITH_BUFFER.SenseInfo.offset

        sptb.spt.CdbLength = sizeof(cdb)
        sptb.spt.Cdb = struct_to_array(cdb, (UCHAR * 16))

        assert sptb.spt.DataBuffer % 8 == 0

        MyDeviceIoControl(self.handle, IOCTL_SCSI_PASS_THROUGH_DIRECT, sptb, sptb, check_output=False)
        return DataBuf

    def scsi_passthrough_buffered(self, cdb: CDB, outstruct: Type[T], timeout: int = 5) -> T:
        """Pass-though raw SCSI commands to SCSI device.

        `cdb`: SCSI Command Descriptor Block
        `outstruct`: Output struct class
        `timeout`: timeout in seconds

        Requires read/write (w+) access.
        """

        class SCSI_PASS_THROUGH_WITH_BUFFERS(Structure):
            _fields_ = [("spt", SCSI_PASS_THROUGH), ("SenseInfo", UCHAR * self.SENSE_INFO_LEN), ("DataBuf", outstruct)]

        assert SCSI_PASS_THROUGH_WITH_BUFFERS.DataBuf.offset % 8 == 0
        assert SCSI_PASS_THROUGH_WITH_BUFFERS.SenseInfo.offset % 8 == 0

        sptb = SCSI_PASS_THROUGH_WITH_BUFFERS()

        sptb.spt.Length = sizeof(SCSI_PASS_THROUGH)
        sptb.spt.SenseInfoLength = sizeof(sptb.SenseInfo)
        sptb.spt.DataIn = SCSI_IOCTL_DATA_IN
        sptb.spt.DataTransferLength = sizeof(outstruct)
        sptb.spt.TimeOutValue = timeout
        sptb.spt.DataBufferOffset = SCSI_PASS_THROUGH_WITH_BUFFERS.DataBuf.offset
        sptb.spt.SenseInfoOffset = SCSI_PASS_THROUGH_WITH_BUFFERS.SenseInfo.offset

        sptb.spt.CdbLength = sizeof(cdb)
        sptb.spt.Cdb = struct_to_array(cdb, (UCHAR * 16))

        MyDeviceIoControl(self.handle, IOCTL_SCSI_PASS_THROUGH, sptb, sptb)

        return sptb.DataBuf

    def _drive_info(self, mode: str, atamode: str) -> Structure:
        assert self.handle

        cdb = CDB()

        if atamode == "ata12":
            ata = cdb.ATA_PASSTHROUGH12
            ata.OperationCode = SCSIOP_ATA_PASSTHROUGH12
        elif atamode == "ata16":
            ata = cdb.ATA_PASSTHROUGH16
            ata.OperationCode = SCSIOP_ATA_PASSTHROUGH16
        else:
            raise ValueError(f"Invalid atamode: {atamode}")

        ata.Protocol = 4
        ata.TLength = 2
        ata.ByteBlock = 1
        ata.TDir = 1
        ata.SectorCount = 1
        ata.Command = ID_CMD

        if mode == "direct":
            return self.scsi_passthrough_direct(cdb, IDENTIFY_DEVICE_DATA)
        elif mode == "buffered":
            return self.scsi_passthrough_buffered(cdb, IDENTIFY_DEVICE_DATA)
        else:
            raise ValueError(f"Invalid mode: {mode}")

    def _smart(self, mode: str, atamode: str) -> Tuple[Structure, Structure]:
        assert self.handle

        cdb = CDB()

        if atamode == "ata12":
            ata = cdb.ATA_PASSTHROUGH12
            ata.OperationCode = SCSIOP_ATA_PASSTHROUGH12
            ata.Features = READ_ATTRIBUTES
            ata.SectorCount = 1
            ata.LbaLow = 1
            ata.LbaMid = SMART_CYL_LOW
            ata.LbaHigh = SMART_CYL_HI
        elif atamode == "ata16":
            ata = cdb.ATA_PASSTHROUGH16
            ata.OperationCode = SCSIOP_ATA_PASSTHROUGH16
            ata.Features15_8 = READ_ATTRIBUTES
            ata.Features7_0 = READ_ATTRIBUTES
            ata.SectorCount15_8 = 1
            ata.SectorCount7_0 = 1
            ata.LbaLow15_8 = 1
            ata.LbaLow7_0 = 1
            ata.LbaMid15_8 = SMART_CYL_LOW
            ata.LbaMid7_0 = SMART_CYL_LOW
            ata.LbaHigh15_8 = SMART_CYL_HI
            ata.LbaHigh7_0 = SMART_CYL_HI
        else:
            raise ValueError(f"Invalid atamode: {atamode}")

        ata.Protocol = 4
        ata.TLength = 2
        ata.ByteBlock = 1
        ata.TDir = 1
        ata.Command = SMART_CMD

        if mode == "direct":
            smart_attr = self.scsi_passthrough_direct(cdb, DEVICE_SMART_DATA_ATTRIBUTES)
        elif mode == "buffered":
            smart_attr = self.scsi_passthrough_buffered(cdb, DEVICE_SMART_DATA_ATTRIBUTES)
        else:
            raise ValueError(f"Invalid mode: {mode}")

        cdb.Features = READ_THRESHOLDS

        if mode == "direct":
            smart_thresh = self.scsi_passthrough_direct(cdb, DEVICE_SMART_DATA_THRESHOLD)
        elif mode == "buffered":
            smart_thresh = self.scsi_passthrough_buffered(cdb, DEVICE_SMART_DATA_THRESHOLD)
        else:
            raise ValueError(f"Invalid mode: {mode}")

        return smart_attr, smart_thresh


class SmartDeviceSatDirect12(SmartDeviceSat):
    def drive_info(self):
        return self._drive_info("direct", "ata12")

    def smart(self):
        return self._smart("direct", "ata12")


class SmartDeviceSatBuffered12(SmartDeviceSat):
    def drive_info(self):
        return self._drive_info("buffered", "ata12")

    def smart(self):
        return self._smart("buffered", "ata12")


class SmartDeviceSatDirect16(SmartDeviceSat):
    def drive_info(self):
        return self._drive_info("direct", "ata16")

    def smart(self):
        return self._smart("direct", "ata16")


class SmartDeviceSatBuffered16(SmartDeviceSat):
    def drive_info(self):
        return self._drive_info("buffered", "ata16")

    def smart(self):
        return self._smart("buffered", "ata16")


class SmartDeviceAtaPassthrough(SmartDevice):
    """Send raw ATA commands to ATA devices"""

    def drive_info(self):
        # IOCTL_ATA_PASS_THROUGH
        raise NotImplementedError

    def smart(self):
        raise NotImplementedError


def nvme_str(c_array: Buffer) -> str:
    b = bytes(c_array)
    try:
        return b.decode("ascii")
    except UnicodeDecodeError:
        logging.warning("NVMe string not ASCII")
        return b.decode("latin1")


def none_if_null(obj: int) -> Optional[int]:
    return obj if obj != 0 else None


class SmartDeviceNvme(SmartDevice):
    def drive_info(self):
        return self.drive.sqp_device_protocol_specific(
            winioctl.STORAGE_PROTOCOL_TYPE.ProtocolTypeNvme,
            winioctl.STORAGE_PROTOCOL_NVME_DATA_TYPE.NVMeDataTypeIdentify,
            nvme.NVME_IDENTIFY_CNS_CODES.NVME_IDENTIFY_CNS_CONTROLLER,
        )

    @classmethod
    def drive_info_json(cls, cns_controller) -> dict:
        cd = cns_controller["NVME_IDENTIFY_CONTROLLER_DATA"]

        mdts = 2 ** cd["MDTS"] if cd["MDTS"] > 0 else 0
        ieee = f"{cd['IEEE'][2]:02X}-{cd['IEEE'][1]:02X}-{cd['IEEE'][0]:02X}"

        if cd["VER"] == 0:
            version = (1, 0, 0)
            version_str = "<1.2"
        else:
            ver = nvme.NVME_VERSION.from_buffer_copy(cd["VER"].to_bytes(4, "little"))
            version = (ver.MJR, ver.MNR, ver.TER)
            version_str = ".".join(map(str, version))

        out = {
            "NVMe version": version_str,
            "PCI Vendor ID": cd["VID"],  # >=1.0
            "PCI Subsystem Vendor ID": cd["SSVID"],  # >=1.0
            "Serial Number": nvme_str(cd["SN"]).strip(" "),  # >=1.0
            "Model Number": nvme_str(cd["MN"]).strip(" "),  # >=1.0
            "Firmware Revision": nvme_str(cd["FR"]).strip(" "),  # >=1.0
            "Recommended Arbitration Burst (count)": 2 ** cd["RAB"],  # >=1.0
            "IEEE OUI Identifier (hex)": ieee,  # >=1.0
            "Maximum Data Transfer Size (pages)": mdts,  # >=1.0
            "Controller ID": cd["CNTLID"],  # >=1.1
            "Host Memory Buffer Preferred Size (in 4 KiB)": None,  # >=1.2.0
            "Host Memory Buffer Minimum Size (in 4 KiB)": None,  # >=1.2.0
            "RTD3 Resume Latency (microseconds)": None,  # >=1.2.0
            "RTD3 Entry Latency (microseconds)": None,  # >=1.2.0
            "Total NVM Capacity (bytes)": None,  # >=1.2.0
            "Unallocated NVM Capacity (bytes)": None,  # >=1.2.0
            "Warning Composite Temperature Threshold (kelvin)": None,  # >=1.2.0
            "Critical Composite Temperature Threshold (kelvin)": None,  # >=1.2.0
        }

        if version >= (1, 2, 0):
            out.update(
                {
                    "RTD3 Resume Latency (microseconds)": cd["RTD3R"],
                    "RTD3 Entry Latency (microseconds)": cd["RTD3E"],
                    "Host Memory Buffer Preferred Size (in 4 KiB)": none_if_null(cd["HMPRE"]),
                    "Host Memory Buffer Minimum Size (in 4 KiB)": cd["HMMIN"],
                    "Total NVM Capacity (bytes)": int.from_bytes(cd["TNVMCAP"], byteorder="little"),
                    "Unallocated NVM Capacity (bytes)": int.from_bytes(cd["UNVMCAP"], byteorder="little"),
                    "Warning Composite Temperature Threshold (kelvin)": none_if_null(cd["WCTEMP"]),
                    "Critical Composite Temperature Threshold (kelvin)": none_if_null(cd["CCTEMP"]),
                }
            )

        return out

    def health_info(self):
        health_info = self.drive.sqp_device_protocol_specific(
            winioctl.STORAGE_PROTOCOL_TYPE.ProtocolTypeNvme,
            winioctl.STORAGE_PROTOCOL_NVME_DATA_TYPE.NVMeDataTypeLogPage,
            nvme.NVME_LOG_PAGES.NVME_LOG_PAGE_HEALTH_INFO,
        )

        hil = health_info["NVME_HEALTH_INFO_LOG"]
        cw = hil["CriticalWarning"]

        critical_warnings = {
            "Available space low": bool(cw["AvailableSpaceLow"]),
            "Temperature threshold": bool(cw["TemperatureThreshold"]),
            "Reliability degraded": bool(cw["ReliabilityDegraded"]),
            "Read only": bool(cw["ReadOnly"]),
            "Volatile memory backup device failed": bool(cw["VolatileMemoryBackupDeviceFailed"]),
        }

        return {
            "Critical warnings": critical_warnings,
            "Composite Temperature (kelvin)": int.from_bytes(hil["Temperature"], byteorder="little"),
            "Available spare (%)": hil["AvailableSpare"],
            "Available spare threshold (%)": hil["AvailableSpareThreshold"],
            "NVM life used (%)": hil["PercentageUsed"],
            "Data unit read (count in 1000)": int.from_bytes(hil["DataUnitRead"], byteorder="little"),
            "Data unit written (count in 1000)": int.from_bytes(hil["DataUnitWritten"], byteorder="little"),
            "Host read commands (count)": int.from_bytes(hil["HostReadCommands"], byteorder="little"),
            "Host written commands (count)": int.from_bytes(hil["HostWrittenCommands"], byteorder="little"),
            "Controller busy time (minutes)": int.from_bytes(hil["ControllerBusyTime"], byteorder="little"),
            "Power cycles (count)": int.from_bytes(hil["PowerCycle"], byteorder="little"),
            "Power on (hours)": int.from_bytes(hil["PowerOnHours"], byteorder="little"),
            "Unsafe shutdowns (count)": int.from_bytes(hil["UnsafeShutdowns"], byteorder="little"),
            "Media errors (count)": int.from_bytes(hil["MediaErrors"], byteorder="little"),
            "Error info log entries (count)": int.from_bytes(hil["ErrorInfoLogEntryCount"], byteorder="little"),
            "Warning temperature threshold exceeded (minutes)": hil["WarningCompositeTemperatureTime"],
            "Critical temperature threshold exceeded (minutes)": hil["CriticalCompositeTemperatureTime"],
            "Temperature sensor 1 (kelvin)": hil["TemperatureSensor1"],
            "Temperature sensor 2 (kelvin)": hil["TemperatureSensor2"],
            "Temperature sensor 3 (kelvin)": hil["TemperatureSensor3"],
            "Temperature sensor 4 (kelvin)": hil["TemperatureSensor4"],
            "Temperature sensor 5 (kelvin)": hil["TemperatureSensor5"],
            "Temperature sensor 6 (kelvin)": hil["TemperatureSensor6"],
            "Temperature sensor 7 (kelvin)": hil["TemperatureSensor7"],
            "Temperature sensor 8 (kelvin)": hil["TemperatureSensor8"],
        }


def decode_temp_wdc(data: bytes, specific: bytes) -> str:
    """confirmed for WD, HGST and TOSHIBA"""

    cur, min, max = unpack("<HHH", bytes(data) + bytes(specific))
    return f"cur={cur} min={min} max={max}"


def decode_spinup_time_wdc(data: bytes, specific: bytes) -> str:
    """confirmed for WD, TOSHIBA"""

    cur, avg = unpack("<HH", data)
    return f"cur={cur} avg={avg}"


def decode_power_on_wdc(data: bytes, specific: bytes) -> int:
    """confirmed for WD, HGST and TOSHIBA"""

    return int.from_bytes(data, "little")


def decode_reallocated_on_wdc(data: bytes, specific: bytes) -> int:
    """confirmed for WD"""

    return int.from_bytes(data, "little")


def decode_pending_count_wdc(data: bytes, specific: bytes) -> int:
    """confirmed for WD"""

    return int.from_bytes(data, "little")


def decode_uncorrectable_count_wdc(data: bytes, specific: bytes) -> int:
    """confirmed for WD"""

    return int.from_bytes(data, "little")


def decode_power_cycle_count_wdc(data: bytes, specific: bytes) -> int:
    """confirmed for WD, HGST and TOSHIBA"""

    return int.from_bytes(data, "little")


def decode_ultra_dma_crc_error_count_wdc(data: bytes, specific: bytes) -> int:
    """confirmed for WD, HGST and TOSHIBA"""

    return int.from_bytes(data, "little")


def smart_info_json(smart_attr, smart_thresh) -> Dict[str, Any]:
    smart = {}

    decode = {
        3: decode_spinup_time_wdc,
        9: decode_power_on_wdc,
        5: decode_reallocated_on_wdc,
        12: decode_power_cycle_count_wdc,
        194: decode_temp_wdc,
        197: decode_pending_count_wdc,
        198: decode_uncorrectable_count_wdc,
        199: decode_ultra_dma_crc_error_count_wdc,
    }

    for attr in smart_attr.Attributes.Attribute:
        id = attr.AttributeID
        if id != 0x0:
            smart[id] = {
                "Label": smart_attribute_dict.get(id, "UNKNOWN"),
                "CurrentValue": attr.CurrentValue,
                "WorstValue": attr.WorstValue,
                "PreFail": attr.Flags.PreFail,
                "Data": bytes(attr.Data),
                "AttributeSpecific": bytes(attr.AttributeSpecific),
            }

    for attr in smart_thresh.Thresholds.Threshold:
        id = attr.AttributeID
        if id != 0x0:
            smart[id]["Threshold"] = attr.Threshold

    out: Dict[str, Any] = {
        "columns": [
            "ID",
            "Label",
            "Pre-Fail",
            "Current Value",
            "Worst Value",
            "Threshold",
            "Decoded",
            "Data",
            "Attribute Specific",
        ],
        "rows": [],
    }

    for k, v in smart.items():
        decoded = decode.get(k, lambda x, y: "")(v["Data"], v["AttributeSpecific"])
        row = [
            k,
            v["Label"],
            v["PreFail"],
            v["CurrentValue"],
            v["WorstValue"],
            v.get("Threshold", 0),
            decoded,
            v["Data"].hex(),
            v["AttributeSpecific"].hex(),
        ]
        out["rows"].append(row)
    return out


def print_smart_info(smart_info: dict) -> None:
    console = Console()

    table = Table(title="SMART", padding=0)

    for col in smart_info["columns"]:
        table.add_column(col)

    for row in smart_info["rows"]:
        row = [str(i) for i in row]
        table.add_row(*row)

    console.print(table)


def drive_info(drive_index: int, method: Optional[Methods]) -> None:
    with SmartDevice.open(drive_index, method) as sd:
        info = sd.drive_info()
        pprint(sd.drive_info_json(info))

        # if info.CommandSetSupport.SmartCommands:

        try:
            smart_attr, smart_thresh = sd.smart()
            smart_info = smart_info_json(smart_attr, smart_thresh)
            print_smart_info(smart_info)
        except NotImplementedError:
            pprint(sd.health_info())


if __name__ == "__main__":
    from argparse import ArgumentParser

    parser = ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all-drives", action="store_true", help="Show information for all drives")
    group.add_argument("--drive-index", metavar="N", type=int, help="Show information for drive N")
    parser.add_argument(
        "--method",
        choices=[i.value for i in Methods],
        help="Chose access method. `smart` should work for directly attached ATA devices (like internal SATA drives, the different SCSI pass-through methods are for ATA devices attached via SCSI device (eg. USB), `nvme` is for directly attached nvme drives.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Show debug info")
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    if args.method is not None:
        args.method = Methods(args.method)

    if args.drive_index is not None:
        try:
            drive_info(args.drive_index, args.method)
        except FileNotFoundError:
            print(f"Cannot find drive {args.drive_index}")
        except (RuntimeError, OSError):
            logging.exception("Failed to read SMART info for drive %s", args.drive_index)
        except Exception:
            logging.exception("Failed to read SMART info for drive %s", args.drive_index)

    elif args.all_drives:
        for d in enum_disks():
            drive_index = d["DeviceNumber"]
            print("Drive", drive_index)
            try:
                drive_info(drive_index, args.method)
            except FileNotFoundError:
                logging.error("Cannot find drive %s", drive_index)
            except (RuntimeError, OSError):
                logging.exception("Failed to read SMART info for drive %s", drive_index)
            except Exception:
                logging.exception("Failed to read SMART info for drive %s", drive_index)
            print("---")
