#!/usr/bin/env python3
"""
hbf222t_claude.py
Standalone reader for Omron HBF-222T body composition scale.
Protocol: WLC 3.0  –  based on HBF222T_RE_SUMMARY.md reverse engineering.

Key fix vs hbf222t_final_test.py: data offsets corrected to RE summary values
  (weight @26, fat @2, visceral @10) instead of the shifted values (-2) used
  in the previous test script.
"""

import asyncio, struct, datetime, logging, sys, subprocess
import bleak

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("HBF222T")

# ── UUIDs (WLC 3.0 protocol) ──────────────────────────────────────────────────
UUID_UNLOCK = "b305b680-aee7-11e1-a730-0002a5d5c51b"
UUID_TX     = "db5b55e0-aee7-11e1-965e-0002a5d5c51b"
UUID_RX     = [
    "49123040-aee8-11e1-a74d-0002a5d5c51b",   # CH0 – always starts a new packet
    "4d0bf320-aee8-11e1-a0d9-0002a5d5c51b",   # CH1
    "5128ce60-aee8-11e1-b84b-0002a5d5c51b",   # CH2
    "560f1420-aee8-11e1-8184-0002a5d5c51b",   # CH3
]
UNLOCK_KEY = bytes.fromhex("a635f6b5d2f947a3a7a3ebcd6b2ae964")

# EEPROM profile base addresses; each measurement slot is 32 bytes (0x20)
PROFILES = [(1, 0x02C0), (2, 0x06A0), (3, 0x0A80), (4, 0x0E60)]
SLOTS    = 5    # slots to probe per profile


# ── WLC 3.0 multi-channel packet reassembler ──────────────────────────────────
class Reassembler:
    """
    Accumulate BLE notification fragments from CH0-CH3 into one packet.
    CH0 carries the first fragment (buf[0] = total length).
    CH1-CH3 carry subsequent fragments in order.
    """

    def __init__(self):
        self._buf  = bytearray()
        self._need = 0
        self._nxt  = 1
        self.done  = asyncio.Event()
        self.ptype = b""   # buf[1:3]  – response type code
        self.addr  = b""   # buf[3:5]  – EEPROM echo address
        self.data  = b""   # buf[6 : 6+n]  – payload bytes

    def reset(self):
        self._buf  = bytearray()
        self._need = 0
        self._nxt  = 1
        self.done.clear()

    def feed(self, uuid: str, chunk: bytes):
        try:
            ch = UUID_RX.index(uuid)
        except ValueError:
            return

        if ch == 0:
            self._buf  = bytearray(chunk)
            self._need = chunk[0] if chunk else 0
            self._nxt  = 1
        elif self._buf and ch == self._nxt:
            self._buf.extend(chunk)
            self._nxt = (self._nxt + 1) % len(UUID_RX)
        else:
            return   # unexpected channel or no active packet

        if self._need and len(self._buf) >= self._need:
            buf = bytes(self._buf[: self._need])
            xor = 0
            for b in buf: xor ^= b
            if xor:
                log.error(f"BCC error (xor={xor:#04x}): {buf.hex()}")
                self.done.set()
                return
            self.ptype = buf[1:3]
            self.addr  = buf[3:5]
            n          = buf[5]
            self.data  = buf[6 : 6 + n]
            log.debug(f"pkt ptype={self.ptype.hex()} addr={self.addr.hex()} "
                      f"n={n} data={self.data.hex()}")
            self.done.set()


# ── Linux / BlueZ pairing agent ───────────────────────────────────────────────
async def _register_linux_agent():
    try:
        from dbus_fast.aio import MessageBus
        from dbus_fast.service import ServiceInterface, method as dm
        from dbus_fast import BusType
    except ImportError:
        return None

    class _JW(ServiceInterface):
        def __init__(self): super().__init__("org.bluez.Agent1")
        @dm()
        def Release(self): pass
        @dm()
        def RequestPinCode(self, device: "o") -> "s": return "0000"
        @dm()
        def DisplayPinCode(self, device: "o", pincode: "s"): pass
        @dm()
        def RequestPasskey(self, device: "o") -> "u": return 0
        @dm()
        def DisplayPasskey(self, device: "o", passkey: "u", entered: "q"): pass
        @dm()
        def RequestConfirmation(self, device: "o", passkey: "u"): pass
        @dm()
        def RequestAuthorization(self, device: "o"): pass
        @dm()
        def AuthorizeService(self, device: "o", uuid: "s"): pass
        @dm()
        def Cancel(self): pass

    try:
        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        bus.export("/omblepy/hbf222t", _JW())
        intro = await bus.introspect("org.bluez", "/org/bluez")
        mgr = (bus.get_proxy_object("org.bluez", "/org/bluez", intro)
                  .get_interface("org.bluez.AgentManager1"))
        await mgr.call_register_agent("/omblepy/hbf222t", "NoInputNoOutput")
        await mgr.call_request_default_agent("/omblepy/hbf222t")
        log.info("BlueZ Just-Works agent active.")
        return bus
    except Exception as e:
        log.warning(f"Agent registration failed: {e}")
        return None


# ── Protocol helpers ──────────────────────────────────────────────────────────
def _bcc(cmd: bytearray) -> bytearray:
    """Append XOR checksum so XOR of all bytes == 0."""
    chk = 0
    for b in cmd: chk ^= b
    cmd.append(chk)
    return cmd


async def _tx(client, asm: Reassembler, cmd: bytes, timeout: float = 5.0) -> bytes:
    asm.reset()
    await client.write_gatt_char(UUID_TX, bytes(cmd), response=True)
    await asyncio.wait_for(asm.done.wait(), timeout)
    return asm.data


async def unlock(client):
    """WLC 3.0 two-step challenge/key unlock."""
    q: asyncio.Queue = asyncio.Queue()
    await client.start_notify(UUID_UNLOCK, lambda _, d: q.put_nowait(bytes(d)))
    # Step 1 – send challenge (0x02 + 16 zero bytes)
    await client.write_gatt_char(UUID_UNLOCK, bytes([0x02] + [0] * 16), response=True)
    await asyncio.wait_for(q.get(), 5.0)
    # Step 2 – send master key
    await client.write_gatt_char(UUID_UNLOCK, bytes([0x01]) + UNLOCK_KEY, response=True)
    await asyncio.wait_for(q.get(), 5.0)
    await client.stop_notify(UUID_UNLOCK)
    log.info("Unlock OK")


async def start_session(client, asm: Reassembler):
    await _tx(client, asm, bytes.fromhex("0800000000100018"))
    if asm.ptype != bytes.fromhex("8000"):
        raise RuntimeError(f"Session start rejected: ptype={asm.ptype.hex()}")
    log.info("Session open")


async def end_session(client, asm: Reassembler):
    await _tx(client, asm, bytes.fromhex("080f000000000007"))
    if asm.ptype not in (bytes.fromhex("8f00"),):
        log.warning(f"End-session ptype={asm.ptype.hex()} (may be normal)")
    log.info("Session closed")


async def read_block(client, asm: Reassembler, addr: int, size: int = 0x20) -> bytes:
    """
    Send GETDATA command for `size` bytes at EEPROM `addr`.
    Command format: [08][01][00][ADDR_H][ADDR_L][SIZE][00][BCC]
    """
    cmd = _bcc(bytearray([
        0x08, 0x01, 0x00,
        (addr >> 8) & 0xFF, addr & 0xFF,
        size & 0xFF, 0x00,
    ]))
    data = await _tx(client, asm, cmd)
    if asm.ptype != bytes.fromhex("8100"):
        log.warning(f"addr {addr:#06x}: ptype={asm.ptype.hex()}")
    return data


# ── Measurement decoder ───────────────────────────────────────────────────────
def _bits(raw: bytes, offset: int, n: int, start: int, size: int = 2) -> int:
    """Extract n bits from a Big Endian integer of 'size' bytes at byte offset."""
    if size == 2:
        val = struct.unpack_from(">H", raw, offset)[0]
    elif size == 4:
        val = struct.unpack_from(">I", raw, offset)[0]
    else:
        val = raw[offset]
    return (val >> start) & ((1 << n) - 1)


def decode(raw: bytes):
    """
    Decode a 32-byte WLC 3.0 measurement block.
    All multi-byte bitfields are BIG ENDIAN.
    """
    if len(raw) < 32 or raw[0:2] == b'\xff\xff':
        return None
    try:
        # Peso (Index 0101): Offset 26, 12 bits, start 4 -> * 0.05
        # Ex: 44 E0 -> BigEndian 0x44E0 >> 4 = 0x44E (1102) -> 1102 * 0.05 = 55.10kg
        weight   = _bits(raw, 26, 12, 4) * 0.05
        
        # Gordura (Index 0103): Offset 2, 10 bits, start 6 -> * 0.1
        fat_pct  = _bits(raw, 2,  10, 6) * 0.1
        
        # BMR (Index 0104): Offset 4, 12 bits, start 4
        bmr      = _bits(raw, 4,  12, 4)
        
        # Músculo (Index 0105): Offset 6, 10 bits, start 6 -> * 0.1
        muscle   = _bits(raw, 6,  10, 6) * 0.1
        
        # BMI (Index 0106): Offset 8, 10 bits, start 6 -> * 0.1
        bmi      = _bits(raw, 8,  10, 6) * 0.1
        
        # Visceral (Index 0107): Offset 10, 7 bits, start 0
        visceral = raw[10] & 0x7F
        
        # Idade Corporal (Index 010A): Offset 11, 4 bits, start 4
        body_age = (raw[11] >> 4) & 0x0F

        # Timestamp (RE summary §5)
        year   = (raw[7]  & 0x3F) + 2000
        month  = raw[11]  & 0x0F
        day    = (raw[12] >> 3) & 0x1F
        hour   = _bits(raw, 12, 5, 6, size=2)
        minute = raw[9]   & 0x3F

        if not (30.0 < weight < 200.0):
            return None

        dt = datetime.datetime(year, month, day, hour, minute)
        return dict(dt=dt, weight=round(weight, 2), fat_pct=round(fat_pct, 1),
                    muscle=round(muscle, 1), bmi=round(bmi, 1),
                    visceral=visceral, bmr=bmr, body_age=body_age)
    except Exception as e:
        log.debug(f"decode error: {e}")
        return None


# ── Entry point ───────────────────────────────────────────────────────────────
DEVICE_ADDRESS = "00:5F:BF:E1:9E:E9"


async def main():
    bus    = await _register_linux_agent() if sys.platform == "linux" else None
    client = None
    try:
        for attempt in range(5):
            try:
                log.info(f"Scan attempt {attempt + 1} for {DEVICE_ADDRESS}")
                dev = await bleak.BleakScanner.find_device_by_address(
                    DEVICE_ADDRESS, timeout=12.0)
                if dev is None:
                    continue
                client = bleak.BleakClient(dev, timeout=15.0)
                await client.connect()
                if sys.platform == "linux":
                    await client.pair(protection_level=2)
                    await asyncio.sleep(1.0)
                log.info("Connected")
                break
            except Exception as e:
                log.warning(f"Attempt {attempt + 1} failed: {e}")
                if client:
                    try: await client.disconnect()
                    except: pass
                if sys.platform == "linux":
                    subprocess.run(["bluetoothctl", "remove", DEVICE_ADDRESS],
                                   capture_output=True)
                    await asyncio.sleep(2.0)
                if attempt == 4:
                    raise
                await asyncio.sleep(3.0)

        asm = Reassembler()
        for uuid in UUID_RX:
            await client.start_notify(
                uuid, lambda _, d, u=uuid: asm.feed(u, bytes(d)))

        await unlock(client)
        await start_session(client, asm)

        results = []
        for uid, base in PROFILES:
            for slot in range(SLOTS):
                addr = base + slot * 0x20
                raw  = await read_block(client, asm, addr)
                print(f"P{uid}[{slot}] {addr:#06x}: {raw.hex()}")
                rec = decode(raw)
                if rec:
                    rec["profile"] = uid
                    results.append(rec)
                await asyncio.sleep(0.05)

        await end_session(client, asm)

        print("\n" + "=" * 74)
        print("HBF-222T  MEASUREMENTS")
        print("=" * 74)
        if results:
            for r in sorted(results, key=lambda x: x["dt"]):
                print(
                    f"P{r['profile']} | {r['dt']} | "
                    f"Weight {r['weight']:5.2f} kg | Fat {r['fat_pct']:4.1f}% | "
                    f"Muscle {r['muscle']:4.1f}% | BMI {r['bmi']:4.1f} | "
                    f"Visceral {r['visceral']:2d} | BMR {r['bmr']:4d} kcal | "
                    f"BodyAge {r['body_age']:2d}"
                )
        else:
            print("No valid measurements decoded.")
            print("Review the raw hex dumps above and adjust decode() offsets if needed.")
        print("=" * 74)

    finally:
        if client and client.is_connected:
            try: await client.disconnect()
            except: pass
        if bus:
            try: bus.disconnect()
            except: pass


if __name__ == "__main__":
    asyncio.run(main())
