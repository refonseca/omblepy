"""
hbf-222t.py  — Omron HBF-222T Body Composition Scale driver for omblepy

BLE transport: 4-channel parallel notifications.
  TX primary : db5b55e0-aee7-11e1-965e-0002a5d5c51b
  RX channels: 49123040 / 4d0bf320 / 5128ce60 / 560f1420
  Unlock     : b305b680-aee7-11e1-a730-0002a5d5c51b
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

    U_UNLOCK    = "b305b680-aee7-11e1-a730-0002a5d5c51b"
    U_TX        = "db5b55e0-aee7-11e1-965e-0002a5d5c51b"
    _RX_LIST    = [
        "49123040-aee8-11e1-a74d-0002a5d5c51b",
        "4d0bf320-aee8-11e1-a0d9-0002a5d5c51b",
        "5128ce60-aee8-11e1-b84b-0002a5d5c51b",
        "560f1420-aee8-11e1-8184-0002a5d5c51b"
    ]
    _UNLOCK_KEY = bytes.fromhex("a635f6b5d2f947a3a7a3ebcd6b2ae964")

    def __init__(self, client):
        self._c = client
        self._q = {u: asyncio.Queue() for u in self._RX_LIST}

    def _mk_cb(self, u):
        def cb(_, data):
            logger.debug(f"  rxu={u[:8]}... {bytes(data).hex()}")
            self._q[u].put_nowait(bytes(data))
        return cb

    async def open(self):
        logger.debug("HBF-222T: Opening RX channels...")
        for u in self._RX_LIST:
            while not self._q[u].empty():
                self._q[u].get_nowait()
            await self._c.start_notify(u, self._mk_cb(u))
        await asyncio.sleep(1.0)
        logger.debug("HBF-222T: RX channels open")

    async def close(self):
        for u in self._RX_LIST:
            try:
                await self._c.stop_notify(u)
            except Exception:
                pass

    async def _wait_any(self, timeout: float = 10.0) -> bytes:
        """Wait for a notification on ANY of the 4 RX channels."""
        tasks = [asyncio.create_task(self._q[u].get()) for u in self._RX_LIST]
        try:
            done, pending = await asyncio.wait(tasks, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
            for p in pending:
                p.cancel()
            if not done:
                raise asyncio.TimeoutError("Timeout waiting for any RX channel")
            return list(done)[0].result()
        except Exception:
            for t in tasks: t.cancel()
            raise

    async def _xact(self, cmd: bytes, timeout: float = 10.0) -> bytes:
        """Send command on TX handle and assemble the multi-fragment response."""
        logger.debug(f"  txu={self.U_TX[:8]}... {cmd.hex()}")
        # Revert to response=False as per HBF hardware behavior
        await self._c.write_gatt_char(self.U_TX, cmd, response=False)
        
        f0 = await self._wait_any(timeout)
        total_len = f0[0]
        n  = math.ceil(total_len / 16)
        buf = bytearray(f0)
        
        # If more fragments are needed, we might need to wait on specific queues
        # but for simple commands like start_tx, it's usually 1 fragment.
        while len(buf) < total_len:
             buf.extend(await self._wait_any(timeout))
             
        return bytes(buf[:total_len])

    async def unlock(self):
        logger.debug("HBF-222T: unlock")
        q = asyncio.Queue()

        def _cb(_, data):
            q.put_nowait(bytes(data))

        await self._c.start_notify(self.U_UNLOCK, _cb)
        try:
            await self._c.write_gatt_char(
                self.U_UNLOCK, bytes([0x01]) + self._UNLOCK_KEY, response=True)
            try:
                resp = await asyncio.wait_for(q.get(), timeout=3.0)
                if resp[0] != 0x81:
                    logger.warning(f"HBF-222T: unexpected unlock response {resp.hex()}")
                logger.debug("HBF-222T: unlock successful, waiting for stabilization...")
                await asyncio.sleep(2.0)
            except asyncio.TimeoutError:
                logger.warning("HBF-222T: unlock response timed out, continuing")
        finally:
            try:
                await self._c.stop_notify(self.U_UNLOCK)
            except Exception:
                pass

    async def start_tx(self):
        # Envia o comando de início. Alguns modelos precisam de uma pequena pausa antes.
        await asyncio.sleep(1.0)
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
    _EEPROM_PROFILE_SIZE     = 48
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
        
        # IMPORTANTE: Desbloquear ANTES de ativar notificações de RX
        # Isso garante que a balança aceite a mudança de estado
        await tp.unlock()
        
        await tp.open()
        try:
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
