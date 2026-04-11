import sys
import datetime
import logging
logger = logging.getLogger("omblepy")

sys.path.append('..')
from sharedDriver import sharedDeviceDriverCode

class deviceSpecificDriver(sharedDeviceDriverCode):

    deviceEndianess           = "big"
    userStartAdressesList     = [0x2e8]
    perUserRecordsCountList   = [30]
    recordByteSize            = 0x0e
    transmissionBlockSize     = 0x38

    settingsReadAddress       = 0x260
    #settingsWriteAddress            = 0x0286

    #AsettingsUnreadRecordsBytes      = [0x00, 0x08]
    #AsettingsTimeSyncBytes           = [0x14, 0x1e]

    def deviceSpecific_ParseRecordFormat(self, singleRecordAsByteArray):
        logger.debug(f"raw hex: {bytes(singleRecordAsByteArray).hex()}")
        recordDict             = dict()
        recordDict["mov"]      = 0
        recordDict["ihb"]      = 0
        recordDict["sys"]      = self._bytearrayBitsToInt(singleRecordAsByteArray, 0, 7) + 25
        recordDict["dia"]      = self._bytearrayBitsToInt(singleRecordAsByteArray, 8, 15)
        recordDict["bpm"]      = self._bytearrayBitsToInt(singleRecordAsByteArray, 16, 23)
        year                   = self._bytearrayBitsToInt(singleRecordAsByteArray, 24, 31) + 2000
        month                  = self._bytearrayBitsToInt(singleRecordAsByteArray, 32, 35)
        # bits 36-42 reserved/unknown
        day                    = self._bytearrayBitsToInt(singleRecordAsByteArray, 43, 47)
        hour                   = self._bytearrayBitsToInt(singleRecordAsByteArray, 48, 52)
        minute                 = self._bytearrayBitsToInt(singleRecordAsByteArray, 53, 58)
        second                 = self._bytearrayBitsToInt(singleRecordAsByteArray, 59, 63)
        second                 = min([second, 59])
        logger.debug(f"parsed: sys={recordDict['sys']} dia={recordDict['dia']} bpm={recordDict['bpm']} date={year}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:{second:02d}")
        recordDict["datetime"] = datetime.datetime(year, month, day, hour, minute, second)
        return recordDict

    def deviceSpecific_syncWithSystemTime(self):
        raise ValueError("Not supported yet.")
