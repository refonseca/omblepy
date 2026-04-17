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

    # settingsWriteAddress / settingsTimeSyncBytes are NOT set here because the
    # HEM-7144T2 sync block uses different read and write addresses, which cannot
    # be expressed via the sharedDriver offset model.  getRecords is overridden
    # below to handle sync directly.
    settingsReadAddress       = 0x260

    # The sync-status block has asymmetric addresses on this device:
    #   READ  from 0x28C  (device updates this each BLE session)
    #   WRITE to  0x2D0  (host writes here to set the clock reference)
    # Verified against 6 Omron Connect HCI captures in btsnoop_hci.log.
    _SYNCBLOCK_ADDR  = 0x2D0   # write address
    _SYNCBLOCK_SIZE  = 0x10

    # Set to False to disable automatic future-date correction.
    _auto_correct_dates = True

    def deviceSpecific_ParseRecordFormat(self, singleRecordAsByteArray):
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
        # Not called by sharedDriver (getRecords is overridden).
        raise NotImplementedError("Use getRecords override for HEM-7144T2 sync.")

    def _build_syncblock(self, now: datetime.datetime) -> bytearray:
        """
        Build the 16-byte sync-status block written to EEPROM 0x2D0.

        Layout (verified against 6 Omron Connect HCI captures in btsnoop_hci.log):
          [0]    0xC0  — constant flags
          [1]    0xA1  — time-synced flag (Omron Connect uses 0xA0 for read-only sessions)
          [2-7]  0x00  — reserved
          [8]    year - 2000
          [9]    month
          [10]   day
          [11]   hour (24 h)
          [12]   minute
          [13]   second
          [14]   CRC1 = sum(bytes 0-13) & 0xFF
          [15]   CRC2 = (-CRC1) & 0xFF
        """
        block     = bytearray(16)
        block[0]  = 0xC0
        block[1]  = 0xA1
        block[8]  = now.year - 2000
        block[9]  = now.month
        block[10] = now.day
        block[11] = now.hour
        block[12] = now.minute
        block[13] = now.second
        crc1      = sum(block[:14]) & 0xFF
        block[14] = crc1
        block[15] = (-crc1) & 0xFF
        return block

    def _correct_future_dates(self, allUserRecordsList: list) -> list:
        """
        Auto-detect and correct implausible future-dated measurements.

        The HEM-7144T2's hardware RTC cannot be set via BLE.  When the
        device clock is wrong it stamps every new measurement with a date
        that is consistently N days ahead of today.  This method:

          1. Collects the day-offset of every record whose date is more
             than 2 days in the future (allowing for mild timezone drift).
          2. Picks the most common offset as the correction value.
          3. Subtracts that offset only from future-dated records, leaving
             historically-correct old records untouched.

        If no future-dated records are found, the list is returned unchanged.
        """
        today     = datetime.date.today()
        threshold = datetime.timedelta(days=2)

        future_offsets = []
        for perUserList in allUserRecordsList:
            for r in perUserList:
                delta = r["datetime"].date() - today
                if delta > threshold:
                    future_offsets.append(delta.days)

        if not future_offsets:
            return allUserRecordsList

        # Most common offset (usually the same value for every wrong record)
        correction_days = max(set(future_offsets), key=future_offsets.count)
        correction      = datetime.timedelta(days=correction_days)

        logger.warning(
            f"Device clock is {correction_days} day(s) ahead of today. "
            f"Auto-correcting future-dated measurements by -{correction_days} days."
        )

        for perUserList in allUserRecordsList:
            for r in perUserList:
                if (r["datetime"].date() - today) > threshold:
                    r["datetime"] -= correction
                    logger.debug(f"  corrected: {r['datetime'] + correction} -> {r['datetime']}")

        return allUserRecordsList

    async def getRecords(self, btobj, useUnreadCounter, syncTime):  # noqa: useUnreadCounter not supported on this model
        """
        Override sharedDriver.getRecords to handle HEM-7144T2 sync block directly.

        The sync-status block uses asymmetric addresses (READ 0x28C / WRITE 0x2D0),
        which cannot be expressed via the sharedDriver settingsWriteAddress offset model.
        """
        await btobj.unlockWithUnlockKey()
        await btobj.startTransmission()

        allUsersReadCommandsList = await self._getReadCommands_AllRecords()

        logger.info("start reading data, this can take a while, use debug flag to see progress")
        allUserRecordsList = []
        for userIdx, userReadCommandsList in enumerate(allUsersReadCommandsList):
            userConcatenatedRecordBytes = bytearray()
            for readCommand in userReadCommandsList:
                userConcatenatedRecordBytes += await btobj.readContinuousEepromData(
                    readCommand["address"], readCommand["size"], self.transmissionBlockSize)
            perUserAnalyzedRecordsList = []
            for recordStartOffset in range(0, len(userConcatenatedRecordBytes), self.recordByteSize):
                singleRecordBytes = userConcatenatedRecordBytes[recordStartOffset:recordStartOffset + self.recordByteSize]
                if singleRecordBytes != b'\xff' * self.recordByteSize:
                    try:
                        singleRecordDict = self.deviceSpecific_ParseRecordFormat(singleRecordBytes)
                        perUserAnalyzedRecordsList.append(singleRecordDict)
                    except Exception as e:
                        logger.warning(f"Error parsing record for user{userIdx+1} at offset {recordStartOffset} "
                                       f"data {bytes(singleRecordBytes).hex()}: {e}, ignoring this record.")
            allUserRecordsList.append(perUserAnalyzedRecordsList)

        if self._auto_correct_dates:
            allUserRecordsList = self._correct_future_dates(allUserRecordsList)

        if syncTime:
            now        = datetime.datetime.now()
            sync_block = self._build_syncblock(now)
            logger.info(f"syncing device clock to {now.strftime('%Y-%m-%d %H:%M:%S')}")
            await btobj.writeContinuousEepromData(self._SYNCBLOCK_ADDR, sync_block, btBlockSize=8)

        await btobj.endTransmission()
        return allUserRecordsList
