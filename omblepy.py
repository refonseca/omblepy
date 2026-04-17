import asyncio                                                      #avoid wait on bluetooth stack stalling the application
import terminaltables                                               #for pretty selection table for ble devices
import bleak                                                        #bluetooth low energy package for python
import re                                                           #regex to match bt mac address
import argparse                                                     #to process command line arguments
import datetime
import sys
import pathlib
import logging
import csv
import json

#global constants
parentService_UUID        = "0000fe4a-0000-1000-8000-00805f9b34fb"

#global variables
bleClient           = None
examplePairingKey   = bytearray.fromhex("00AAFFBB")   #arbitrary choise
deviceSpecific      = None                            #imported module for each device
logger              = logging.getLogger("omblepy")

def convertByteArrayToHexString(array):
    return (bytes(array).hex())


class bluetoothTxRxHandler:
    #BTLE Characteristic IDs
    deviceRxChannelUUIDs  = [
                                "49123040-aee8-11e1-a74d-0002a5d5c51b"
                            ]
    deviceTxChannelUUIDs  = [
                                "db5b55e0-aee7-11e1-965e-0002a5d5c51b"
                            ]
    deviceDataRxChannelIntHandles = [31]
    deviceUnlock_UUID         = "b305b680-aee7-11e1-a730-0002a5d5c51b"

    def __init__(self, pairing = False):
        self.currentRxNotifyStateFlag   = False
        self.rxPacketType               = None
        self.rxEepromAddress            = None
        self.rxDataBytes                = None
        self.rxFinishedFlag             = False

    async def _enableRxChannelNotifyAndCallback(self):
        if(self.currentRxNotifyStateFlag != True):
            for rxChannelUUID in self.deviceRxChannelUUIDs:
                await bleClient.start_notify(rxChannelUUID, self._callbackForRxChannels)
            self.currentRxNotifyStateFlag = True

    async def _disableRxChannelNotifyAndCallback(self):
        if(self.currentRxNotifyStateFlag != False):
            for rxChannelUUID in self.deviceRxChannelUUIDs:
                await bleClient.stop_notify(rxChannelUUID)
            self.currentRxNotifyStateFlag = False

    def _callbackForRxChannels(self, BleakGATTChar, rxBytes):
        logger.debug(f"rx ch0 < {convertByteArrayToHexString(rxBytes)}")
        packetSize = rxBytes[0]
        xorCrc = 0
        for byte in rxBytes:
            xorCrc ^= byte
        if(xorCrc):
            raise ValueError(f"data corruption in rx\ncrc: {xorCrc}\ncombniedBuffer: {convertByteArrayToHexString(rxBytes)}")
            return
        #extract information
        self.rxPacketType       = rxBytes[1:3]
        self.rxEepromAddress    = rxBytes[3:5]
        expectedNumDataBytes    = rxBytes[5]
        if(expectedNumDataBytes > (len(rxBytes) - 8)):
            self.rxDataBytes    = bytes(b'\xff') * expectedNumDataBytes
        else:
            if(self.rxPacketType) == bytearray.fromhex("8f00"): #need special case for end of transmission packet, otherwise transmission error code is not accessible
                self.rxDataBytes = rxBytes[6:7]
            else:
                self.rxDataBytes    = rxBytes[6: 6 + expectedNumDataBytes]
        self.rxFinishedFlag     = True
        return

    async def _waitForRxOrRetry(self, command, timeoutS = 1.0):
        self.rxFinishedFlag = False
        retries = 0
        while True:
            await bleClient.write_gatt_char(self.deviceTxChannelUUIDs[0], command)

            currentTimeout = timeoutS
            while(self.rxFinishedFlag == False):
                await asyncio.sleep(0.1)
                currentTimeout -= 0.1
                if(currentTimeout < 0):
                    break
            if(currentTimeout >= 0):
                break
            retries += 1
            logger.warning(f"Transmission failed, count of retries: {retries} / 5")
            if(retries >= 5):
                ValueError("Same transmission failed 5 times, abort")
                return

    async def startTransmission(self):
        await self._enableRxChannelNotifyAndCallback()
        startDataReadout    = bytearray.fromhex("0800000000100018")
        await self._waitForRxOrRetry(startDataReadout)
        if(self.rxPacketType != bytearray.fromhex("8000")):
            raise ValueError("invalid response to data readout start")

    async def endTransmission(self):
        stopDataReadout         = bytearray.fromhex("080f000000000007")
        await self._waitForRxOrRetry(stopDataReadout)
        if(self.rxPacketType != bytearray.fromhex("8f00")):
            raise ValueError("invlid response to data readout end")
            return
        if(self.rxDataBytes[0]):
            raise ValueError(f"Device reported error status code {self.rxDataBytes[0]} while sending endTransmission command.")
            return
        await self._disableRxChannelNotifyAndCallback()

    async def _writeBlockEeprom(self, address, dataByteArray):
        dataWriteCommand = bytearray()
        dataWriteCommand += (len(dataByteArray) + 8).to_bytes(1, 'big') #total packet size with 6byte header and 2byte crc
        dataWriteCommand += bytearray.fromhex("01c0")
        dataWriteCommand += address.to_bytes(2, 'big')
        dataWriteCommand += len(dataByteArray).to_bytes(1, 'big')
        dataWriteCommand += dataByteArray
        #calculate and append crc
        xorCrc = 0
        for byte in dataWriteCommand:
            xorCrc ^= byte
        dataWriteCommand += b'\x00'
        dataWriteCommand.append(xorCrc)
        await self._waitForRxOrRetry(dataWriteCommand)
        if(self.rxEepromAddress != address.to_bytes(2, 'big')):
            raise ValueError(f"recieved packet address {self.rxEepromAddress} does not match the written address {address.to_bytes(2, 'big')}")
        if(self.rxPacketType != bytearray.fromhex("81c0")):
            raise ValueError("Invalid packet type in eeprom write")
        return

    async def _readBlockEeprom(self, address, blocksize):
        dataReadCommand = bytearray.fromhex("080100")
        dataReadCommand += address.to_bytes(2, 'big')
        dataReadCommand += blocksize.to_bytes(1, 'big')
        #calculate and append crc
        xorCrc = 0
        for byte in dataReadCommand:
            xorCrc ^= byte
        dataReadCommand += b'\x00'
        dataReadCommand.append(xorCrc)
        await self._waitForRxOrRetry(dataReadCommand)
        if(self.rxEepromAddress != address.to_bytes(2, 'big')):
            raise ValueError(f"revieved packet address {self.rxEepromAddress} does not match requested address {address.to_bytes(2, 'big')}")
        if(self.rxPacketType != bytearray.fromhex("8100")):
            raise ValueError("Invalid packet type in eeprom read")
        return self.rxDataBytes

    async def writeContinuousEepromData(self, startAddress, bytesArrayToWrite, btBlockSize = 0x08):
        while(len(bytesArrayToWrite) != 0):
            nextSubblockSize = min(len(bytesArrayToWrite), btBlockSize)
            logger.debug(f"write to {hex(startAddress)} size {hex(nextSubblockSize)}")
            await self._writeBlockEeprom(startAddress, bytesArrayToWrite[:nextSubblockSize])
            bytesArrayToWrite = bytesArrayToWrite[nextSubblockSize:]
            startAddress += nextSubblockSize
        return

    async def readContinuousEepromData(self, startAddress, bytesToRead, btBlockSize = 0x10):
        eepromBytesData = bytearray()
        while(bytesToRead != 0):
            nextSubblockSize = min(bytesToRead, btBlockSize)
            logger.debug(f"read from {hex(startAddress)} size {hex(nextSubblockSize)}")
            eepromBytesData += await self._readBlockEeprom(startAddress, nextSubblockSize)
            startAddress    += nextSubblockSize
            bytesToRead     -= nextSubblockSize
        return eepromBytesData

    def _callbackForUnlockChannel(self, UUID_or_intHandle, rxBytes):
        self.rxDataBytes = rxBytes
        self.rxFinishedFlag = True
        return

    async def writeNewUnlockKey(self, newKeyByteArray = examplePairingKey):
        if(len(newKeyByteArray) != 4):
            raise ValueError(f"key has to be 4 bytes long, is {len(newKeyByteArray)}")
            return
        #enable key programming mode
        await bleClient.start_notify(self.deviceUnlock_UUID, self._callbackForUnlockChannel)
        self.rxFinishedFlag = False
        await bleClient.write_gatt_char(self.deviceUnlock_UUID, b'\x11' + newKeyByteArray, response=True)
        #while(self.rxFinishedFlag == False):
        #    await asyncio.sleep(0.1)
        #deviceResponse = self.rxDataBytes
        #if(deviceResponse[:2] != bytearray.fromhex("9100")):
        #    raise ValueError(f"Failure to program new key. Response: {deviceResponse}")
        #    return
        await bleClient.stop_notify(self.deviceUnlock_UUID)
        logger.info(f"Paired device successfully with new key {newKeyByteArray}.")
        logger.info("From now on you can connect omit the -p flag, even on other PCs with different bluetooth-mac-addresses.")
        return

    async def unlockWithUnlockKey(self, keyByteArray = examplePairingKey):
        await bleClient.start_notify(self.deviceUnlock_UUID, self._callbackForUnlockChannel)
        self.rxFinishedFlag = False
        await bleClient.write_gatt_char(self.deviceUnlock_UUID, b'\x01' + keyByteArray, response=True)
        timeout = 2.0
        while(self.rxFinishedFlag == False):
            await asyncio.sleep(0.1)
            timeout -= 0.1
            if timeout < 0:
                logger.warning("No unlock response from device (may be normal for some models), continuing.")
                await bleClient.stop_notify(self.deviceUnlock_UUID)
                return
        deviceResponse = self.rxDataBytes
        if(deviceResponse[:2] !=  bytearray.fromhex("8100")):
            raise ValueError(f"entered pairing key does not match stored one.")
            return
        await bleClient.stop_notify(self.deviceUnlock_UUID)
        return

def readCsv(filename):
    records = []
    with open(filename, mode='r', newline='', encoding='utf-8') as infile:
        reader = csv.DictReader(infile)
        for oldRecordDict in reader:
            oldRecordDict["datetime"] = datetime.datetime.strptime(oldRecordDict["datetime"], "%Y-%m-%d %H:%M:%S")
            records.append(oldRecordDict)
    return records

def appendCsv(allRecords):
    for userIdx in range(len(allRecords)):
        oldCsvFile = pathlib.Path(f"user{userIdx+1}.csv")
        dateText = datetime.datetime.now().strftime('%Y_%m_%d__%H_%M_%S')
        backup = pathlib.Path(f"backup_user{userIdx+1}_{dateText}.csv")
        datesOfNewRecords = [record["datetime"] for record in allRecords[userIdx]]
        if(oldCsvFile.is_file()):
            backup.write_bytes(oldCsvFile.read_bytes())
            records = readCsv(f"user{userIdx+1}.csv")
            allRecords[userIdx].extend(filter(lambda x: x["datetime"] not in datesOfNewRecords,records))
        allRecords[userIdx] = sorted(allRecords[userIdx], key = lambda x: x["datetime"])
        logger.info(f"writing data to user{userIdx+1}.csv")
        if allRecords[userIdx]:
            first_keys = list(allRecords[userIdx][0].keys())
            csv_fieldnames = ["datetime"] + [k for k in first_keys if k != "datetime"]
        else:
            csv_fieldnames = ["datetime", "dia", "sys", "bpm", "mov", "ihb"]
        with open(f"user{userIdx+1}.csv", mode='w', newline='', encoding='utf-8') as outfile:
            writer = csv.DictWriter(outfile, fieldnames=csv_fieldnames, extrasaction='ignore')
            writer.writeheader()
            for recordDict in allRecords[userIdx]:
                recordDict["datetime"] = recordDict["datetime"].strftime("%Y-%m-%d %H:%M:%S")
                writer.writerow(recordDict)

def saveUBPMJson(allRecords):
    has_bp = any('sys' in rec for user in allRecords for rec in user)
    if not has_bp:
        logger.info("Device does not produce blood-pressure records; skipping UBPM JSON.")
        return
    f = pathlib.Path(f"ubpm.json")
    UBPM = {}
    UBPM["UBPM"] = {}
    for userIdx in range(len(allRecords)):
        UBPM["UBPM"][f"U{userIdx+1}"] = []
        for rec in allRecords[userIdx]:
            recdate=datetime.datetime.strptime(rec["datetime"], "%Y-%m-%d %H:%M:%S")
            UBPM["UBPM"][f"U{userIdx+1}"].append({
                                "date": recdate.strftime("%d.%m.%Y"),
                                'time': recdate.strftime("%H:%M:%S"), 'msg': "",
                                'sys': int(rec['sys']), 'dia': int(rec['dia']), 'bpm': int(rec['bpm']), 'ihb': int(rec['ihb']), 'mov': int(rec['mov']) })
    f.write_text(json.dumps(UBPM, indent=4, sort_keys=True, default=str))

_LINUX_AGENT_PATH = '/omblepy/BleAgent'

async def _linux_register_pairing_agent():
    """
    Register a BlueZ D-Bus NoInputNoOutput pairing agent before connecting.

    The HEM-7144T2 sends a Security Request within ~90 ms of connection.  BlueZ responds
    immediately with a SMP Pairing Request whose IO capability is taken from the currently
    registered agent.  With no agent (or the default DisplayYesNo), BlueZ generates a
    User Confirmation Request that nobody answers, and the device disconnects after ~30 s.

    By registering a NoInputNoOutput agent BEFORE connect(), we make BlueZ advertise
    IO capability NoInputNoOutput in the Pairing Request.  Combined with the device's own
    NoInputNoOutput, the negotiated method is Just Works with no confirmation step, so the
    SMP exchange and LTK encryption complete automatically.

    Returns the D-Bus bus object (for cleanup) or None if unavailable.
    """
    try:
        from dbus_fast.aio import MessageBus
        from dbus_fast.service import ServiceInterface, method as dbus_method
        from dbus_fast import BusType
    except ImportError:
        try:
            from dbus_next.aio import MessageBus
            from dbus_next.service import ServiceInterface, method as dbus_method
            from dbus_next import BusType
        except ImportError:
            logger.warning("dbus_fast/dbus_next not found; cannot register BlueZ pairing agent.")
            return None

    class _JustWorksAgent(ServiceInterface):
        """Minimal BlueZ Agent1 that silently accepts Just Works (NoInputNoOutput) pairing."""
        def __init__(self):
            super().__init__('org.bluez.Agent1')
        @dbus_method()
        def Release(self): pass
        @dbus_method()
        def RequestPinCode(self, device: 'o') -> 's': return '0000'
        @dbus_method()
        def DisplayPinCode(self, device: 'o', pincode: 's'): pass
        @dbus_method()
        def RequestPasskey(self, device: 'o') -> 'u': return 0
        @dbus_method()
        def DisplayPasskey(self, device: 'o', passkey: 'u', entered: 'q'): pass
        @dbus_method()
        def RequestConfirmation(self, device: 'o', passkey: 'u'): pass
        @dbus_method()
        def RequestAuthorization(self, device: 'o'): pass
        @dbus_method()
        def AuthorizeService(self, device: 'o', uuid: 's'): pass
        @dbus_method()
        def Cancel(self): pass

    try:
        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        bus.export(_LINUX_AGENT_PATH, _JustWorksAgent())
        intro = await bus.introspect('org.bluez', '/org/bluez')
        mgr   = bus.get_proxy_object('org.bluez', '/org/bluez', intro).get_interface('org.bluez.AgentManager1')
        await mgr.call_register_agent(_LINUX_AGENT_PATH, 'NoInputNoOutput')
        await mgr.call_request_default_agent(_LINUX_AGENT_PATH)
        logger.info("Linux/BlueZ: NoInputNoOutput pairing agent registered.")
        return bus
    except Exception as e:
        logger.warning(f"Could not register BlueZ pairing agent: {e}")
        return None

async def selectBLEdevices():
    print("Select your Omron device from the list below...")
    while(True):
        devices = await bleak.BleakScanner.discover(return_adv=True)
        devices = list(sorted(devices.items(), key = lambda x: x[1][1].rssi, reverse=True))
        tableEntries = []
        tableEntries.append(["ID", "MAC", "NAME", "RSSI"])
        for deviceIdx, (macAddr, (bleDev, advData)) in enumerate(devices):
            tableEntries.append([deviceIdx, macAddr, bleDev.name, advData.rssi])
        print(terminaltables.AsciiTable(tableEntries).table)
        res = input("Enter ID or just press Enter to rescan.\n")
        if(res.isdigit() and int(res) in range(len(devices))):
            break
    return devices[int(res)][0]

async def main():
    global bleClient
    global deviceSpecific
    parser = argparse.ArgumentParser(description="python tool to read the records of omron blood pressure instruments")
    parser.add_argument('-d', "--device",     required="true", type=ascii,  help="Device name (e.g. HEM-7322T-D).")
    parser.add_argument("--loggerDebug",      action="store_true",          help="Enable verbose logger output")
    parser.add_argument("-p", "--pair",       action="store_true",          help="Programm the pairing key into the device. Needs to be done only once.")
    parser.add_argument("-m", "--mac",                          type=ascii, help="Bluetooth Mac address of the device (e.g. 00:1b:63:84:45:e6). If not specified, will scan for devices and display a selection dialog.")
    parser.add_argument('-n', "--newRecOnly", action="store_true",          help="Considers the unread records counter and only reads new records. Resets these counters afterwards. If not enabled, all records are read and the unread counters are not cleared.")
    parser.add_argument('-t', "--timeSync",   action="store_true",          help="Update the time on the omron device by using the current system time.")
    args = parser.parse_args()

    #setup logging
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    if(args.loggerDebug):
        logger.setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.INFO)

    #import device specific module
    if(not args.pair and not args.device):
        raise ValueError("When not in pairing mode, please specify your device type name with -d or --device")
        return
    if(args.device):
        deviceName = args.device.strip("'").strip('\"') #strip quotes around arg
        if getattr(sys, 'frozen', False):
            sys.path.insert(0, str(pathlib.Path(sys._MEIPASS) / "deviceSpecific"))
        else:
            sys.path.insert(0, "./deviceSpecific")
        try:
            logger.info(f"Attempt to import module for device {deviceName.lower()}")
            deviceSpecific = __import__(deviceName.lower())
        except ImportError:
            raise ValueError("the device is no supported yet, you can help by contributing :)")
            return

    #select device mac address
    validMacRegex = re.compile(r"^([0-9a-fA-F]{2}[:-]){5}([0-9a-fA-F]{2})$")
    if(args.mac is not None):
        btmac = args.mac.strip("'").strip('\"') #strip quotes around arg
        if(validMacRegex.match(btmac) is None):
            raise ValueError(f"argument after -m or --mac {btmac} is not a valid mac address")
            return
        bleAddr = btmac
    else:
        print("To improve your chance of a successful connection please do the following:")
        print(" -remove previous device pairings in your OS's bluetooth dialog")
        print(" -enable bluetooth on you omron device and use the specified mode (pairing or normal)")
        print(" -do not accept any pairing dialog until you selected your device in the following list\n")
        bleAddr = await selectBLEdevices()

    bleClient = bleak.BleakClient(bleAddr)
    _linux_agent_bus = None
    try:
        if sys.platform == "linux":
            # Register NoInputNoOutput agent BEFORE connecting so BlueZ uses it when the
            # device sends its Security Request (~90 ms after connection).  This prevents
            # the User Confirmation Request that otherwise blocks Just Works pairing.
            _linux_agent_bus = await _linux_register_pairing_agent()
        logger.info(f"Attempt connecting to {bleAddr}.")
        await bleClient.connect()
        # Allow time for the SMP exchange (triggered by the device's Security Request) to
        # complete now that the NoInputNoOutput agent is registered.
        # On some systems (like Raspberry Pi 5), GATT service discovery can take a bit longer.
        await asyncio.sleep(2.0)
        if sys.platform != "linux":
            try:
                await bleClient.pair(protection_level = 2)
            except Exception as e:
                if "OPERATION_ALREADY_IN_PROGRESS" in str(e) or "already" in str(e).lower():
                    logger.info("Device already paired, continuing.")
                else:
                    raise
        
        #verify that the device is an omron device by checking presence of certain bluetooth services
        found_services = []
        for i in range(5):
            found_services = [service.uuid for service in bleClient.services]
            if parentService_UUID in found_services:
                break
            logger.info(f"Waiting for Omron service {parentService_UUID} (found {len(found_services)} services so far)...")
            await asyncio.sleep(1.0)

        if parentService_UUID not in found_services:
            logger.error(f"Required Omron service {parentService_UUID} not found.")
            logger.error(f"Discovered services: {found_services}")
            if deviceSpecific is not None:
                logger.warning(f"Proceeding anyway as a specific device driver ({deviceName}) is loaded and might use direct handles.")
            else:
                raise OSError(f"""Some required bluetooth attributes not found on this ble device.
                                 Expected service {parentService_UUID} not found.
                                 This means that either, you connected to a wrong device,
                                 or that your OS has a bug when reading BT LE device attributes (certain linux versions).""")

        bluetoothTxRxObj = bluetoothTxRxHandler()
        if(args.pair):
            if deviceName.lower().startswith("hbf"):
                logger.info("Pairing (-p) for HBF scales typically uses a fixed key handled by the driver transport.")
            await bluetoothTxRxObj.writeNewUnlockKey()
            #this seems to be necessary when the device has not been paired to any device
            await bluetoothTxRxObj.startTransmission()
            await bluetoothTxRxObj.endTransmission()
        else:
            logger.info("communication started")
            devSpecificDriver = deviceSpecific.deviceSpecificDriver()
            allRecs = await devSpecificDriver.getRecords(btobj = bluetoothTxRxObj, useUnreadCounter = args.newRecOnly, syncTime = args.timeSync)
            logger.info("communication finished")
            appendCsv(allRecs)
            saveUBPMJson(allRecs)
    finally:
        if _linux_agent_bus is not None:
            try:
                _linux_agent_bus.disconnect()
            except Exception:
                pass
        logger.info("unpair and disconnect")
        if bleClient.is_connected:
            try:
                await bleClient.unpair()
            except Exception as e:
                logger.debug(f"Unpair failed (normal if not supported by OS): {e}")
            
            try:
                await bleClient.disconnect()
            except AssertionError as e:
                logger.error("Bleak AssertionError during disconnect. This usually happens when using the bluezdbus adapter.")
                logger.error("You can find the upstream issue at: https://github.com/hbldh/bleak/issues/641")
                logger.error(f"AssertionError details: {e}")
            except Exception as e:
                logger.error(f"Disconnect failed: {e}")

asyncio.run(main())
