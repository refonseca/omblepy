import sys
import asyncio
import struct
import datetime
import math
import logging
from bleak.exc import BleakError

logger = logging.getLogger("omblepy")
sys.path.append('..')
from sharedDriver import sharedDeviceDriverCode


# ── User Profiles ──────────────────────────────────────────────────────────

class UserProfile:
    def __init__(self, birthdate, height, gender):
        self.birthdate = birthdate # (Y, M, D)
        self.height = height
        self.gender = gender # "M" or "F"

# Profiles confirmed by user
PROFILES = {
    1: UserProfile((1979, 12, 25), 171, "M"),
    2: UserProfile((1979, 9, 22), 155, "F"),
    3: UserProfile((1996, 1, 1), 160, "M"),
}

# ── Low-level helpers ──────────────────────────────────────────────────────────

def _xor(data) -> int:
    r = 0
    for b in data:
        r ^= b
    return r


def _read_cmd(addr: int, n: int, chunk_n: int = None, state: bytes = None) -> bytes:
    if addr > 0x0200:
        if chunk_n is None: chunk_n = n
        length = 40
        c = bytearray([length, 0x01, addr & 0xFF, (addr >> 8) & 0xFF, n & 0xFF, chunk_n & 0xFF])
        if state and len(state) >= 24:
            c += state[:24]
        else:
            c += bytearray([0] * 24)
        if len(c) < 30:
            c += bytearray([0] * (30 - len(c)))
        now = datetime.datetime.now()
        c += bytearray([now.year - 2000, now.month, now.day, now.hour, now.minute, now.second])
        c = c[:39]
        if len(c) < 39:
            c += bytearray([0] * (39 - len(c)))
        c.append(_xor(c))
        return bytes(c)
    else:
        c = bytearray([0x08, 0x01, 0x00, (addr >> 8) & 0xFF, addr & 0xFF, n & 0xFF])
        c += bytes([0x00, _xor(c)])
        return bytes(c)


def _scan_datetime(data: bytes) -> datetime.datetime | None:
    """Return first plausible 6-byte [yy mm dd HH MM SS] datetime found in data."""
    # Method 1: Check for raw byte-aligned date (Type 1: 0x60)
    for i in range(len(data) - 5):
        yy, mo, dd, hh, mi, ss = data[i:i+6]
        yr = yy + 2000
        if (2020 <= yr <= 2035 and 1 <= mo <= 12 and 1 <= dd <= 31
                and 0 <= hh <= 23 and 0 <= mi <= 59 and ss <= 60):
            try:
                return datetime.datetime(yr, mo, dd, hh, mi, min(ss, 59))
            except ValueError:
                continue

    # Method 2: Check for XOR-encrypted date (Type 2: 0x5E)
    # Observed XOR key: f6 47 d9 6f 71 4f
    if data[0] == 0x5E and len(data) >= 7:
        key = [0xf6, 0x47, 0xd9, 0x6f, 0x71, 0x4f]
        yy = data[1] ^ key[0]
        mo = data[2] ^ key[1]
        dd = data[3] ^ key[2]
        hh = data[4] ^ key[3]
        mi = data[5] ^ key[4]
        ss = data[6] ^ key[5]
        yr = yy + 2000
        if (2020 <= yr <= 2035 and 1 <= mo <= 12 and 1 <= dd <= 31
                and 0 <= hh <= 23 and 0 <= mi <= 59 and ss <= 60):
            try:
                return datetime.datetime(yr, mo, dd, hh, mi, min(ss, 59))
            except ValueError:
                pass
    return None


# ── Device driver ──────────────────────────────────────────────────────────────

class deviceSpecificDriver(sharedDeviceDriverCode):
    """Omron HBF-222T body-composition scale driver supporting 3 user profiles."""

    deviceEndianess          = "big"
    _MAX_RECORDS             = 120 # Increased to cover more history
    _EEPROM_BASE_ADDR        = 0x01A0 # Start from headers to get Type-1 records
    _RECORD_STRIDE           = 32

    def __init__(self):
        super().__init__()
        self._session_state = None

    async def _custom_unlock(self, btobj):
        logger.debug("HBF-222T: performing custom unlock")
        omblepy_mod = sys.modules.get("omblepy") or sys.modules["__main__"]
        ble = omblepy_mod.bleClient
        unlock_key = bytes.fromhex("a635f6b5d2f947a3a7a3ebcd6b2ae964")
        q = asyncio.Queue()
        def _cb(_, data): q.put_nowait(bytes(data))
        try:
            await ble.start_notify(btobj.deviceUnlock_UUID, _cb)
            await ble.write_gatt_char(btobj.deviceUnlock_UUID, bytes([0x02] + [0]*16), response=False)
            await asyncio.wait_for(q.get(), timeout=3.0)
            await ble.write_gatt_char(btobj.deviceUnlock_UUID, bytes([0x01]) + unlock_key, response=False)
            await asyncio.wait_for(q.get(), timeout=3.0)
        finally:
            try: await ble.stop_notify(btobj.deviceUnlock_UUID)
            except: pass

    async def getRecords(self, btobj, useUnreadCounter, syncTime):
        try:
            await self._custom_unlock(btobj)
            await btobj.startTransmission()

            async def _read_eeprom(addr: int, size: int, block: int = 0x18) -> bytes:
                out = bytearray()
                while len(out) < size:
                    n = min(size - len(out), block)
                    cmd = _read_cmd(addr + len(out), n, chunk_n=n, state=self._session_state)
                    await btobj._waitForRxOrRetry(cmd)
                    chunk = btobj.rxDataBytes
                    if not chunk: break
                    out += chunk
                    # Establish session state from the very first profile/header read
                    if self._session_state is None and len(out) >= 24:
                        self._session_state = bytes(out[:24])
                return bytes(out)

            # Read a large block encompassing both latest headers and history
            raw = await _read_eeprom(
                self._EEPROM_BASE_ADDR,
                self._MAX_RECORDS * self._RECORD_STRIDE,
                block=0x20
            )

            # Map records by user and timestamp
            type1_map = {} # dt -> (user_id, visceral)
            type2_map = {} # dt -> (weight, fat_mass)
            
            for offset in range(0, len(raw), self._RECORD_STRIDE):
                blk = raw[offset : offset + self._RECORD_STRIDE]
                if len(blk) < 32 or blk[0] == 0xFF: continue
                
                dt = _scan_datetime(blk)
                if not dt: continue
                
                if blk[0] == 0x60: # Type 1
                    user_id = blk[1]
                    visceral = blk[17]
                    type1_map[dt] = (user_id, visceral)
                elif blk[0] == 0x5E: # Type 2
                    weight   = struct.unpack_from(">H", blk, 18)[0] / 100.0
                    fat_mass = struct.unpack_from(">H", blk, 20)[0] / 100.0
                    if weight > 0:
                        type2_map[dt] = (weight, fat_mass)

            user_records = {1: [], 2: [], 3: []}
            
            # Join Type-1 and Type-2 by matching timestamps
            # (Works because we can now decrypt the Type-2 timestamp)
            all_dts = sorted(set(type1_map.keys()) | set(type2_map.keys()))
            for dt in all_dts:
                if dt in type1_map and dt in type2_map:
                    user_id, visceral = type1_map[dt]
                    weight, fat_mass = type2_map[dt]
                    profile = PROFILES.get(user_id)
                    if not profile: continue
                    
                    fat_pct = round((fat_mass / weight * 100.0), 1) if weight > 0 else 0
                    lean_mass = weight - fat_mass
                    bmr = round(363 + 21.6 * lean_mass)
                    bmi = round(weight / ((profile.height/100.0)**2), 1)
                    
                    user_records[user_id].append({
                        "datetime":     dt,
                        "weight":       round(weight, 2),
                        "fat_pct":      fat_pct,
                        "fat_mass":     round(fat_mass, 2),
                        "visceral_fat": visceral,
                        "bmi":          bmi,
                        "bmr":          bmr,
                    })

            await btobj.endTransmission()
        except Exception as e:
            logger.error(f"HBF-222T: getRecords failed: {e}")
            raise

        all_results = []
        for i in range(1, 4):
            recs = user_records[i]
            logger.info(f"HBF-222T: User {i} - {len(recs)} record(s) found")
            all_results.append(recs)
            
        return all_results
