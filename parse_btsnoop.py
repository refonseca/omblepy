"""
parse_btsnoop.py  — extract Omron EEPROM write commands from btsnoop_hci.log

Looks for ATT Write Request packets sent to the Omron TX characteristic
that contain EEPROM write commands (opcode 0x81C0 big-endian in payload).

Usage:
    python parse_btsnoop.py [btsnoop_hci.log]
"""

import sys
import struct
import datetime

# ── btsnoop file format ───────────────────────────────────────────────────────
BTSNOOP_MAGIC   = b"btsnoop\x00"
BTSNOOP_HDR_FMT = ">8sII"          # magic, version, datalink
BTSNOOP_HDR_SZ  = struct.calcsize(BTSNOOP_HDR_FMT)

RECORD_HDR_FMT  = ">IIIIq"         # orig_len, incl_len, flags, drops, timestamp_us
RECORD_HDR_SZ   = struct.calcsize(RECORD_HDR_FMT)

# btsnoop epoch: 2000-01-01 00:00:00 UTC in microseconds since unix epoch
BTSNOOP_EPOCH_US = 946684800_000_000

# ── HCI / ATT constants ───────────────────────────────────────────────────────
HCI_ACL_PKT           = 0x02
L2CAP_ATT_CID         = 0x0004

ATT_WRITE_REQ         = 0x12
ATT_WRITE_CMD         = 0x52
ATT_READ_REQ          = 0x0A
ATT_READ_BY_TYPE_REQ  = 0x08

# Omron EEPROM read / write opcodes (big-endian in packet payload)
OMRON_WRITE_CMD = 0x81C0
OMRON_READ_CMD  = 0x81C1

# ── helpers ───────────────────────────────────────────────────────────────────

def ts_to_str(ts_us: int) -> str:
    """Convert btsnoop timestamp (us since 2000-01-01) to readable string."""
    unix_us = ts_us - BTSNOOP_EPOCH_US
    dt = datetime.datetime(1970, 1, 1) + datetime.timedelta(microseconds=unix_us)
    return dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def parse_btsnoop(path: str):
    with open(path, "rb") as f:
        raw = f.read()

    # validate header
    magic, version, datalink = struct.unpack_from(BTSNOOP_HDR_FMT, raw, 0)
    if magic != BTSNOOP_MAGIC:
        sys.exit("Not a btsnoop file (bad magic)")
    print(f"btsnoop v{version}  datalink={datalink}")

    pos = BTSNOOP_HDR_SZ
    pkt_num = 0
    results = []

    while pos + RECORD_HDR_SZ <= len(raw):
        orig_len, incl_len, flags, drops, ts = struct.unpack_from(RECORD_HDR_FMT, raw, pos)
        pos += RECORD_HDR_SZ
        data = raw[pos:pos + incl_len]
        pos += incl_len
        pkt_num += 1

        if len(data) < 4:
            continue

        # flags bit 0: 0=sent-by-host, 1=received-by-host
        # flags bit 1: 0=data, 1=command/event
        direction = "HOST→CTRL" if (flags & 1) == 0 else "CTRL→HOST"
        is_cmd_or_evt = bool(flags & 2)

        # we want ACL data packets (H4 indicator byte = 0x02 for HCI ACL)
        hci_type = data[0]
        if hci_type != HCI_ACL_PKT:
            continue

        if len(data) < 5:
            continue

        # ACL header: handle[12]+pb[2]+bc[2] (2 bytes) + data_len (2 bytes)
        acl_hdr    = struct.unpack_from("<HH", data, 1)
        acl_handle = acl_hdr[0] & 0x0FFF
        acl_len    = acl_hdr[1]
        acl_payload = data[5:5 + acl_len]

        if len(acl_payload) < 4:
            continue

        # L2CAP header: len (2) + cid (2)
        l2cap_len = struct.unpack_from("<H", acl_payload, 0)[0]
        l2cap_cid = struct.unpack_from("<H", acl_payload, 2)[0]

        if l2cap_cid != L2CAP_ATT_CID:
            continue

        att_payload = acl_payload[4:]
        if len(att_payload) < 1:
            continue

        att_op = att_payload[0]

        if att_op in (ATT_WRITE_REQ, ATT_WRITE_CMD) and len(att_payload) >= 3:
            handle = struct.unpack_from("<H", att_payload, 1)[0]
            value  = att_payload[3:]

            # Check for Omron EEPROM write opcode at start of value
            if len(value) >= 6:
                omron_op = struct.unpack_from(">H", value, 0)[0]
                if omron_op == OMRON_WRITE_CMD:
                    eeprom_addr = struct.unpack_from(">H", value, 2)[0]
                    write_len   = value[4]
                    write_data  = value[5:5 + write_len]
                    results.append({
                        "ts":        ts_to_str(ts),
                        "pkt":       pkt_num,
                        "dir":       direction,
                        "handle":    handle,
                        "addr":      eeprom_addr,
                        "length":    write_len,
                        "data":      write_data.hex(),
                    })

        elif att_op == ATT_READ_REQ and len(att_payload) >= 3:
            handle = struct.unpack_from("<H", att_payload, 1)[0]
            # Not logging reads by default — uncomment if needed:
            # print(f"  READ_REQ handle=0x{handle:04x}")

    return results


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "btsnoop_hci.log"
    writes = parse_btsnoop(path)

    if not writes:
        print("\nNo Omron EEPROM write commands found.")
        return

    print(f"\nFound {len(writes)} EEPROM write command(s):\n")
    print(f"{'#':>4}  {'Timestamp':<27}  {'Dir':<10}  {'HDNL':>6}  {'ADDR':>6}  {'LEN':>4}  Data")
    print("-" * 110)
    for i, w in enumerate(writes):
        print(f"{i+1:>4}  {w['ts']:<27}  {w['dir']:<10}  0x{w['handle']:04x}  "
              f"0x{w['addr']:04x}  {w['length']:>4}  {w['data']}")

    # Summarise unique addresses
    addrs = sorted(set(w["addr"] for w in writes))
    print(f"\nUnique EEPROM addresses written: {', '.join(f'0x{a:04x}' for a in addrs)}")

    # Show grouped writes per address
    print("\nPer-address write summary:")
    for addr in addrs:
        addr_writes = [w for w in writes if w["addr"] == addr]
        print(f"\n  Address 0x{addr:04x}  ({len(addr_writes)} write(s)):")
        for w in addr_writes:
            print(f"    [{w['ts']}] len={w['length']}  {w['data']}")


if __name__ == "__main__":
    main()
