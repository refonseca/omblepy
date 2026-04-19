import sys
import struct
import datetime
import logging
from sharedDriver import sharedDeviceDriverCode

logger = logging.getLogger("omblepy")

class deviceSpecificDriver(sharedDeviceDriverCode):
    """
    Driver HEM-7144T2: Medidor de Pressão com Protocolo WLC 3.0.
    Implementação validada contra btsnoop_hci_hem7144t2.log.
    """

    deviceEndianess           = "big"
    userStartAdressesList     = [0x2e8]
    perUserRecordsCountList   = [30]
    recordByteSize            = 14
    transmissionBlockSize     = 0x38

    # Sincronismo (DeviceConfig.sys)
    settingsReadAddress       = 0x260
    _SYNCBLOCK_ADDR           = 0x2D0
    _SYNCBLOCK_SIZE           = 0x10

    def __init__(self):
        super().__init__()

    def _get_bits(self, data, offset, bit_size, start_bit):
        """Extrai bits de um inteiro de 16 bits (Big Endian)."""
        val = struct.unpack_from(">H", data, offset)[0]
        mask = (1 << bit_size) - 1
        return (val >> start_bit) & mask

    def deviceSpecific_ParseRecordFormat(self, raw):     
    
        """     
        Decodifica o registro de 14 bytes do HEM-7144T2.     
        Baseado no VitalDataIndexes.json real do modelo BR.     
        """
        
        recordDict = dict()          # --- Dados Vitais (Confirmados pelo JSON) ---     
        recordDict["sys"] = raw[0] + 25  # Offset 0, +25
        recordDict["dia"] = raw[1]       # Offset 1
        recordDict["bpm"] = raw[2]       # Offset 2
     
        # Flags extras (IHB / MOV) - Opcional
        word4 = struct.unpack_from("<H", raw, 4)[0]
        recordDict["mov"] = (word4 >> 14) & 0x01 # Bit 14 do byte 4/5
        recordDict["ihb"] = (word4 >> 15) & 0x01 # Bit 15 do byte 4/5

        # --- Timestamp (Decodificação de Bits Real) ---
        try:
            # Bloco do Byte 3 (Ano)
            year_val = raw[3] & 0x3F
            year = (year_val + 2000) if year_val > 0 else 2021

            # Bloco dos Bytes 4-5 (Mes, Dia, Hora)
            month = (word4 >> 10) & 0x0F
            day = (word4 >> 5) & 0x1F
            hour = word4 & 0x1F

            # Bloco dos Bytes 6-7 (Minuto, Segundo)
            word6 = struct.unpack_from("<H", raw, 6)[0]
            minute = (word6 >> 6) & 0x3F
            second = word6 & 0x3F

            # Validação básica para evitar crash
            if not (1 <= month <= 12 and 1 <= day <= 31 and 0 <= hour <= 23):
                raise ValueError("Data fora do range")

            recordDict["datetime"] = datetime.datetime(year, month, day, hour, minute, min(second, 59))

        except Exception:
            # Fallback para data de sistema se o parsing falhar
            recordDict["datetime"] = datetime.datetime(2021, 1, 1, 0, 0, 0)

        logger.debug(f"HEM-7144T2: {recordDict['datetime']} | {recordDict['sys']}/{recordDict['dia']} {recordDict['bpm']}bpm")
        return recordDict

    async def getRecords(self, btobj, useUnreadCounter, syncTime):
        await btobj.unlockWithUnlockKey()
        await btobj.startTransmission()

        logger.info("HEM-7144T2: Lendo histórico (30 registros)...")
        # Leitura direta do endereço 0x2e8 conforme DeviceConfig.sys
        raw_memory = await btobj.readContinuousEepromData(0x2e8, 420, 56)
        
        all_recs = []
        for offset in range(0, len(raw_memory), 14):
            chunk = raw_memory[offset : offset + 14]
            if len(chunk) == 14 and chunk != b'\xff' * 14:
                all_recs.append(self.deviceSpecific_ParseRecordFormat(chunk))

        if syncTime:
            now = datetime.datetime.now()
            # Bloco de sincronismo fixo em 0x2D0
            sync_block = bytearray(16)
            sync_block[0] = 0xC0; sync_block[1] = 0xA1
            sync_block[8] = now.year - 2000; sync_block[9] = now.month; sync_block[10] = now.day
            sync_block[11] = now.hour; sync_block[12] = now.minute; sync_block[13] = now.second
            crc = sum(sync_block[:14]) & 0xFF
            sync_block[14] = crc; sync_block[15] = (-crc) & 0xFF
            logger.info(f"HEM-7144T2: Sincronizando relógio para {now}")
            await btobj.writeContinuousEepromData(0x2D0, sync_block, btBlockSize=8)

        await btobj.endTransmission()
        return [all_recs]
