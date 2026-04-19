import sys
import asyncio
import struct
import datetime
import logging
from sharedDriver import sharedDeviceDriverCode

logger = logging.getLogger("omblepy")

class deviceSpecificDriver(sharedDeviceDriverCode):
    """
    Driver HBF-222T: Implementação definitiva com suporte a histórico longo e precisão de segundos.
    """

    deviceEndianess = "big"
    
    # Endereços base oficiais
    PROFILES = [
        (1, 0x02C0),
        (2, 0x06A0),
        (3, 0x0A80),
        (4, 0x0E60)
    ]

    def __init__(self):
        super().__init__()
        self.recordByteSize = 32

    async def _custom_unlock(self, btobj):
        logger.debug("HBF-222T: handshake de desbloqueio")
        omblepy_mod = sys.modules.get("omblepy") or sys.modules["__main__"]
        ble = omblepy_mod.bleClient
        unlock_key = bytes.fromhex("a635f6b5d2f947a3a7a3ebcd6b2ae964")
        q = asyncio.Queue()
        def _cb(_, data): q.put_nowait(bytes(data))
        try:
            await ble.start_notify(btobj.deviceUnlock_UUID, _cb)
            await ble.write_gatt_char(btobj.deviceUnlock_UUID, bytes([0x02] + [0]*16), response=True)
            await asyncio.wait_for(q.get(), timeout=3.0)
            await ble.write_gatt_char(btobj.deviceUnlock_UUID, bytes([0x01]) + unlock_key, response=True)
            await asyncio.wait_for(q.get(), timeout=3.0)
        finally:
            try: await ble.stop_notify(btobj.deviceUnlock_UUID)
            except: pass

    def _bits(self, raw, offset, n, start):
        """Extrai n bits de um inteiro de 16 bits (Big Endian) no offset indicado."""
        val = struct.unpack_from(">H", raw, offset)[0]
        return (val >> start) & ((1 << n) - 1)

    def decode(self, raw):
        """Decodifica o bloco de 32 bytes da EEPROM com precisão de segundos."""
        if len(raw) < 32 or raw[0:2] == b'\xff\xff':
            return None
        try:
            # Métricas Vitais (Big Endian Bit-Packing)
            weight   = self._bits(raw, 26, 12, 4) * 0.05
            fat_pct  = self._bits(raw, 2,  10, 6) * 0.1
            bmr      = self._bits(raw, 4,  12, 4)
            muscle   = self._bits(raw, 6,  10, 6) * 0.1
            bmi      = self._bits(raw, 8,  10, 6) * 0.1
            visceral = raw[10] & 0x7F
            body_age = (raw[11] >> 4) & 0x0F

            # Timestamp Detalhado
            year   = (raw[7]  & 0x3F) + 2000
            month  = raw[11]  & 0x0F
            day    = (raw[12] >> 3) & 0x1F
            hour   = self._bits(raw, 12, 5, 6)
            minute = raw[9]   & 0x3F
            second = raw[13]  & 0x3F # Segundos identificados nos bits 0-5 do byte 13

            if not (30.0 < weight < 200.0) or not (1 <= month <= 12):
                return None

            return {
                "datetime": datetime.datetime(year, month, day, hour, minute, min(second, 59)),
                "weight": round(weight, 2),
                "fat_pct": round(fat_pct, 1),
                "fat_mass": round(weight * fat_pct / 100.0, 2),
                "muscle": round(muscle, 1),
                "bmi": round(bmi, 1),
                "visceral_fat": visceral,
                "bmr": bmr,
                "body_age": body_age
            }
        except Exception as e:
            logger.debug(f"HBF-222T: erro no parsing: {e}")
            return None

    async def getRecords(self, btobj, useUnreadCounter, syncTime):
        try:
            await self._custom_unlock(btobj)
            await btobj.startTransmission()

            user_records = {1: [], 2: [], 3: [], 4: []}

            for uid, base_addr in self.PROFILES:
                logger.info(f"Lendo histórico completo do Perfil {uid}...")
                # Aumentado para 30 slots para capturar todas as medidas do print
                for step in range(30):
                    addr = base_addr + (step * 32)
                    raw_data = await btobj.readContinuousEepromData(addr, 32, 32)
                    
                    if not raw_data or all(b == 0xFF for b in raw_data):
                        break
                    
                    res = self.decode(raw_data)
                    if res:
                        user_records[uid].append(res)
                        logger.info(f"OK P{uid}: {res['datetime']} -> {res['weight']}kg")
                    await asyncio.sleep(0.02)

            await btobj.endTransmission()
            return [user_records[i] for i in range(1, 5)]
            
        except Exception as e:
            logger.error(f"HBF-222T: falha: {e}")
            raise
