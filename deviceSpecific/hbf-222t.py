"""
hbf-222t.py  — Omron HBF-222T Body Composition Scale driver for omblepy

BLE transport: 4-channel parallel notifications.
  TX primary : handle 0x0321  (Write Without Response)
  RX channels: 0x0361 / 0x0371 / 0x0381 / 0x0391  (16 bytes each per fragment)
  Unlock     : handle 0x0311  (Write Request; response via Notify on same handle)

EEPROM record layout – 64-byte stride starting at 0x02C0:
  bytes  0-27  type-1: marker byte 0x5E/0x5F, visceral_fat at byte 3 (uint8)
  bytes 28-55  type-2: marker = 0xFF 0xFF (record present),
                       fat_mass×100 at bytes 20-21 (uint16 BE),
                       weight×100   at bytes 22-23 (uint16 BE)
  bytes 56-63  unused gap

User profile (height) is read from EEPROM 0x01D0 (48 bytes, 24 bytes per user).
Datetime is recovered by scanning each record for a plausible yy-mm-dd HH:MM:SS
six-byte sequence.

Derived fields
  fat_pct = fat_mass / weight × 100
  bmi     = weight / (height_m)²
  bmr     = 363 + 21.6 × (weight - fat_mass)   (Katch-McArdle, verified against app)
"""

import sys
import asyncio
import struct
import datetime
import math
import logging

logger = logging.getLogger("omblepy")
sys.path.append('..')
from sharedDriver import sharedDeviceDriverCode


# ── Low-level helpers ──────────────────────────────────────────────────────────

def _xor(data) -> int:
    r = 0
    for b in data:
        r ^= b
    return r


def _read_cmd(addr: int, n: int) -> bytes:
    c = bytearray([0x08, 0x01, 0x00, (addr >> 8) & 0xFF, addr & 0xFF, n])
    c += bytes([0x00, _xor(c)])
    return bytes(c)


def _scan_datetime(data: bytes) -> datetime.datetime | None:
    """Return first plausible 6-byte [yy mm dd HH MM SS] datetime found in data."""
    for i in range(len(data) - 5):
        yy, mo, dd, hh, mi, ss = data[i:i+6]
        yr = yy + 2000
        if not (2020 <= yr <= 2035 and 1 <= mo <= 12 and 1 <= dd <= 31
                and 0 <= hh <= 23 and 0 <= mi <= 59 and ss <= 60):
            continue
        try:
            return datetime.datetime(yr, mo, dd, hh, mi, min(ss, 59))
        except ValueError:
            continue
    return None


# ── BLE transport ──────────────────────────────────────────────────────────────

class _HBF222TTransport:
    """Multi-channel BLE transport for the HBF-222T."""

    H_UNLOCK    = 0x0311
    H_TX        = 0x0321
    _RX_LIST    = [0x0361, 0x0371, 0x0381, 0x0391]
    _UNLOCK_KEY = bytes.fromhex("a635f6b5d2f947a3a7a3ebcd6b2ae964")

    def __init__(self, client):
        self._c = client
        self._q = {h: asyncio.Queue() for h in self._RX_LIST}

    def _mk_cb(self, h):
        def cb(_, data):
            logger.debug(f"  rxh=0x{h:04x} {bytes(data).hex()}")
            self._q[h].put_nowait(bytes(data))
        return cb

    async def open(self):
        for h in self._RX_LIST:
            while not self._q[h].empty():
                self._q[h].get_nowait()
            await self._c.start_notify(h, self._mk_cb(h))
        logger.debug("HBF-222T: RX channels open")

    async def close(self):
        for h in self._RX_LIST:
            try:
                await self._c.stop_notify(h)
            except Exception:
                pass

    async def _wait(self, h: int, timeout: float = 5.0) -> bytes:
        return await asyncio.wait_for(self._q[h].get(), timeout=timeout)

    async def _xact(self, cmd: bytes, timeout: float = 5.0) -> bytes:
        """Send command on TX handle and assemble the multi-fragment response."""
        logger.debug(f"  txh=0x{self.H_TX:04x} {cmd.hex()}")
        await self._c.write_gatt_char(self.H_TX, cmd, response=False)
        f0 = await self._wait(self._RX_LIST[0], timeout)
        n  = math.ceil(f0[0] / 16)
        buf = bytearray(f0)
        for i in range(1, n):
            buf.extend(await self._wait(self._RX_LIST[i], timeout))
        return bytes(buf[:f0[0]])

    async def unlock(self):
        logger.debug("HBF-222T: unlock")
        q = asyncio.Queue()

        def _cb(_, data):
            q.put_nowait(bytes(data))

        await self._c.start_notify(self.H_UNLOCK, _cb)
        try:
            await self._c.write_gatt_char(
                self.H_UNLOCK, bytes([0x01]) + self._UNLOCK_KEY, response=True)
            try:
                resp = await asyncio.wait_for(q.get(), timeout=2.0)
                if resp[0] != 0x81:
                    logger.warning(f"HBF-222T: unexpected unlock response {resp.hex()}")
            except asyncio.TimeoutError:
                logger.warning("HBF-222T: unlock response timed out, continuing")
        finally:
            try:
                await self._c.stop_notify(self.H_UNLOCK)
            except Exception:
                pass

    async def start_tx(self):
        resp = await self._xact(bytes.fromhex("0800000000100018"))
        logger.debug(f"HBF-222T: start_tx resp {resp.hex()}")

    async def end_tx(self):
        try:
            resp = await self._xact(bytes.fromhex("080f000000000007"))
            logger.debug(f"HBF-222T: end_tx resp {resp.hex()}")
        except Exception as e:
            logger.debug(f"HBF-222T: end_tx ignored: {e}")

    async def read_eeprom(self, addr: int, size: int, block: int = 0x38) -> bytes:
        """Read `size` bytes starting at `addr`, split into `block`-sized requests."""
        out = bytearray()
        while size > 0:
            n = min(size, block)
            r = await self._xact(_read_cmd(addr, n))
            # response: [total_len] 81 00 [ah] [al] [data_len] [data...]
            out += r[6:6 + r[5]]
            addr += n
            size -= n
        return bytes(out)


# ── Device driver ──────────────────────────────────────────────────────────────

class deviceSpecificDriver(sharedDeviceDriverCode):
    """Omron HBF-222T body-composition scale driver."""

    deviceEndianess          = "big"
    _MAX_RECORDS             = 60
    _EEPROM_RECORDS_BASE     = 0x02C0
    _EEPROM_PROFILE_ADDR     = 0x01D0
    _EEPROM_PROFILE_SIZE     = 48       # covers users 1 and 2
    _PROFILE_USER_STRIDE     = 24
    _RECORD_STRIDE           = 64
    _T1_SIZE                 = 28
    _T2_SIZE                 = 28
    _DEFAULT_HEIGHT_CM       = 170

    def _parse_height(self, profile: bytes, user_idx: int = 0) -> int:
        off = user_idx * self._PROFILE_USER_STRIDE
        blk = profile[off:off + self._PROFILE_USER_STRIDE]
        if len(blk) >= 3:
            h = blk[2]
            if 100 <= h <= 250:
                return h
        if len(blk) >= 4:
            h2 = (blk[2] << 8) | blk[3]
            if 100 <= h2 <= 250:
                return h2
        logger.warning(f"HBF-222T: height not found in profile, using {self._DEFAULT_HEIGHT_CM} cm")
        return self._DEFAULT_HEIGHT_CM

    def _parse_record(self, block: bytes, height_cm: int) -> dict | None:
        t1 = block[:self._T1_SIZE]
        t2 = block[self._T1_SIZE:self._T1_SIZE + self._T2_SIZE]

        if t1 == b'\xff' * self._T1_SIZE or t2 == b'\xff' * self._T2_SIZE:
            return None
        if t1[0] not in (0x5E, 0x5F):
            return None

        visceral = t1[3]
        fat_x100 = struct.unpack_from(">H", t2, 20)[0]
        wt_x100  = struct.unpack_from(">H", t2, 22)[0]
        if wt_x100 == 0:
            return None

        weight   = wt_x100  / 100.0
        fat_mass = fat_x100 / 100.0
        fat_pct  = fat_mass / weight * 100.0
        bmi      = weight / (height_cm / 100.0) ** 2
        bmr      = round(363 + 21.6 * (weight - fat_mass))

        dt = _scan_datetime(t2) or _scan_datetime(t1) or datetime.datetime(2000, 1, 1)
        logger.debug(
            f"HBF-222T record: wt={weight} fat={fat_mass} "
            f"visc={visceral} bmi={round(bmi,1)} bmr={bmr} dt={dt}"
        )
        return {
            "datetime":     dt,
            "weight":       round(weight, 2),
            "fat_pct":      round(fat_pct, 1),
            "fat_mass":     round(fat_mass, 2),
            "visceral_fat": visceral,
            "bmi":          round(bmi, 1),
            "bmr":          bmr,
        }

    async def getRecords(self, btobj, useUnreadCounter, syncTime):
        omblepy_mod = sys.modules.get("omblepy") or sys.modules["__main__"]
        ble = omblepy_mod.bleClient

        tp = _HBF222TTransport(ble)
        await tp.open()
        try:
            await tp.unlock()
            await tp.start_tx()

            profile   = await tp.read_eeprom(self._EEPROM_PROFILE_ADDR, self._EEPROM_PROFILE_SIZE)
            height_cm = self._parse_height(profile, user_idx=0)
            logger.info(f"HBF-222T: user-1 height {height_cm} cm")

            raw = await tp.read_eeprom(
                self._EEPROM_RECORDS_BASE,
                self._MAX_RECORDS * self._RECORD_STRIDE,
                block=0x38,
            )

            records = []
            for slot in range(self._MAX_RECORDS):
                blk = raw[slot * self._RECORD_STRIDE:(slot + 1) * self._RECORD_STRIDE]
                if len(blk) < self._RECORD_STRIDE:
                    break
                rec = self._parse_record(blk, height_cm)
                if rec is not None:
                    records.append(rec)

            await tp.end_tx()
        finally:
            await tp.close()

        logger.info(f"HBF-222T: {len(records)} record(s) found")
        return [records]
