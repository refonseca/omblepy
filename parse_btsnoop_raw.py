"""
parse_btsnoop_raw.py — dump all ATT packets from a btsnoop_hci.log

Shows every ATT Write, Notification, and Read to help identify
the BLE protocol used by an unknown device.

Usage:
    python parse_btsnoop_raw.py [btsnoop_hci.log] [--filter MAC]
"""

import sys
import struct
import datetime

BTSNOOP_MAGIC   = b"btsnoop\x00"
BTSNOOP_HDR_FMT = ">8sII"
BTSNOOP_HDR_SZ  = struct.calcsize(BTSNOOP_HDR_FMT)

RECORD_HDR_FMT  = ">IIIIq"
RECORD_HDR_SZ   = struct.calcsize(RECORD_HDR_FMT)

BTSNOOP_EPOCH_US = 946684800_000_000

HCI_ACL_PKT  = 0x02
L2CAP_ATT_CID = 0x0004

ATT_OPCODES = {
    0x01: "ERROR_RSP",
    0x02: "MTU_REQ",
    0x03: "MTU_RSP",
    0x08: "READ_BY_TYPE_REQ",
    0x09: "READ_BY_TYPE_RSP",
    0x0A: "READ_REQ",
    0x0B: "READ_RSP",
    0x0C: "READ_BLOB_REQ",
    0x0D: "READ_BLOB_RSP",
    0x10: "READ_BY_GROUP_TYPE_REQ",
    0x11: "READ_BY_GROUP_TYPE_RSP",
    0x12: "WRITE_REQ",
    0x13: "WRITE_RSP",
    0x16: "PREPARE_WRITE_REQ",
    0x17: "PREPARE_WRITE_RSP",
    0x18: "EXECUTE_WRITE_REQ",
    0x19: "EXECUTE_WRITE_RSP",
    0x1B: "HANDLE_VALUE_NOTIF",
    0x1D: "HANDLE_VALUE_IND",
    0x1E: "HANDLE_VALUE_CONF",
    0x52: "WRITE_CMD",
}

# ATT opcodes we care about for protocol analysis
INTERESTING_OPS = {0x12, 0x52, 0x1B, 0x1D, 0x0B, 0x09, 0x11}


def ts_to_str(ts_us: int) -> str:
    unix_us = ts_us - BTSNOOP_EPOCH_US
    dt = datetime.datetime(1970, 1, 1) + datetime.timedelta(microseconds=unix_us)
    return dt.strftime("%H:%M:%S.%f")[:-3]


def hexdump(data: bytes, indent: int = 6) -> str:
    pad = " " * indent
    lines = []
    for i in range(0, len(data), 16):
        chunk = data[i:i+16]
        hex_part = " ".join(f"{b:02x}" for b in chunk)
        asc_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{pad}{i:04x}  {hex_part:<47}  {asc_part}")
    return "\n".join(lines)


def parse_btsnoop(path: str, after: str = None, tail: int = None):
    with open(path, "rb") as f:
        raw = f.read()

    magic, version, datalink = struct.unpack_from(BTSNOOP_HDR_FMT, raw, 0)
    if magic != BTSNOOP_MAGIC:
        sys.exit("Not a btsnoop file (bad magic)")
    print(f"btsnoop v{version}  datalink={datalink}  file_size={len(raw)} bytes\n")

    after_us = None
    if after:
        h, m = (after.split(":") + ["0"])[:3][:2]
        after_us = (int(h) * 3600 + int(m) * 60) * 1_000_000

    pos = BTSNOOP_HDR_SZ
    pkt_num = 0
    att_count = 0
    handle_data: dict[int, list] = {}
    buffer = []  # used when tail is set

    while pos + RECORD_HDR_SZ <= len(raw):
        orig_len, incl_len, flags, drops, ts = struct.unpack_from(RECORD_HDR_FMT, raw, pos)
        pos += RECORD_HDR_SZ
        data = raw[pos:pos + incl_len]
        pos += incl_len
        pkt_num += 1

        if len(data) < 5:
            continue

        hci_type = data[0]
        if hci_type != HCI_ACL_PKT:
            continue

        # --after filter: ts is microseconds since btsnoop epoch (2000-01-01)
        if after_us is not None:
            ts_of_day = ts % 86_400_000_000
            if ts_of_day < after_us:
                continue

        direction = "->DEV" if (flags & 1) == 0 else "<-DEV"

        acl_hdr    = struct.unpack_from("<HH", data, 1)
        acl_len    = acl_hdr[1]
        acl_payload = data[5:5 + acl_len]

        if len(acl_payload) < 5:
            continue

        l2cap_cid = struct.unpack_from("<H", acl_payload, 2)[0]
        if l2cap_cid != L2CAP_ATT_CID:
            continue

        att_payload = acl_payload[4:]
        if not att_payload:
            continue

        att_op = att_payload[0]
        op_name = ATT_OPCODES.get(att_op, f"0x{att_op:02x}")

        if att_op not in INTERESTING_OPS:
            continue

        att_count += 1

        # Extract handle and value for write/notify/read_rsp
        handle = None
        value  = b""

        if att_op in (0x12, 0x52) and len(att_payload) >= 3:  # WRITE_REQ / WRITE_CMD
            handle = struct.unpack_from("<H", att_payload, 1)[0]
            value  = att_payload[3:]
        elif att_op in (0x1B, 0x1D) and len(att_payload) >= 3:  # NOTIF / IND
            handle = struct.unpack_from("<H", att_payload, 1)[0]
            value  = att_payload[3:]
        elif att_op == 0x0B:  # READ_RSP
            value = att_payload[1:]
        elif att_op in (0x09, 0x11):  # READ_BY_TYPE_RSP / READ_BY_GROUP_TYPE_RSP
            value = att_payload[1:]

        handle_str = f"hdl=0x{handle:04x}" if handle is not None else "          "
        entry = (ts, pkt_num, direction, op_name, handle_str, value, handle)

        if tail:
            buffer.append(entry)
            if len(buffer) > tail:
                buffer.pop(0)
        else:
            print(f"[{ts_to_str(ts)}] pkt={pkt_num:>5}  {direction}  {op_name:<22} {handle_str}  len={len(value):>3}")
            if value:
                print(hexdump(value))

        # Track unique payloads per handle for summary
        if handle is not None:
            if handle not in handle_data:
                handle_data[handle] = []
            handle_data[handle].append((op_name, value.hex()))

    if tail and buffer:
        for ts, pkt_num_, dir_, op_name_, handle_str_, value_, handle_ in buffer:
            print(f"[{ts_to_str(ts)}] pkt={pkt_num_:>5}  {dir_}  {op_name_:<22} {handle_str_}  len={len(value_):>3}")
            if value_:
                print(hexdump(value_))

    print(f"\n{'='*70}")
    print(f"Total HCI packets: {pkt_num}   ATT packets shown: {att_count}")

    if handle_data:
        print(f"\nHandles seen ({len(handle_data)} unique):")
        for h in sorted(handle_data.keys()):
            ops = set(x[0] for x in handle_data[h])
            print(f"  0x{h:04x}  ops={','.join(sorted(ops))}  packets={len(handle_data[h])}")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("log", nargs="?", default="btsnoop_hci.log")
    ap.add_argument("--after", metavar="HH:MM", help="Only show packets after this time (e.g. 17:30)")
    ap.add_argument("--tail", type=int, metavar="N", help="Only show last N ATT packets")
    args = ap.parse_args()
    parse_btsnoop(args.log, after=args.after, tail=args.tail)
