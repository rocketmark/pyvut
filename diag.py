#!/usr/bin/env python3
"""Minimal diagnostic: open tracker, check status, read raw packets."""
import hid, struct, sys

VID, PID = 0x0bb4, 0x06a3

devs = hid.enumerate(VID, PID)
if not devs:
    print("No tracker found")
    sys.exit(1)

for d in devs:
    print(f"  interface {d['interface_number']}: {d['path']}")

dev = hid.Device(path=devs[0]['path'])
print("Opened interface 0 OK")

# Try GET_STATUS feature report (non-blocking path check)
PACKET_GET_STATUS = 0xa002
out = struct.pack("<HBB", PACKET_GET_STATUS, 0, 0) + bytes(60)
print("Sending GET_STATUS...")
try:
    dev.send_feature_report(out)
    print("send_feature_report returned")
except Exception as e:
    print(f"send_feature_report failed: {e}")
    sys.exit(1)

print("Reading feature report response (blocking)...")
try:
    resp = dev.get_feature_report(0, 0x40)
    print(f"Got {len(resp)} bytes: {resp[:16].hex()}")
    unk, cmd_id, data_len, unk2 = struct.unpack("<BHBB", resp[:5])
    print(f"  cmd_id=0x{cmd_id:04x} data_len={data_len}")
    if cmd_id == PACKET_GET_STATUS and data_len >= 6:
        tracking_mode, power, batt, hmd_init, status_mask, btn = struct.unpack("<BBBBHL", resp[5:5+data_len])
        print(f"  tracking_mode={tracking_mode} power={power} batt={batt}% hmd_init={hmd_init} status=0x{status_mask:04x}")
except Exception as e:
    print(f"get_feature_report failed: {e}")
    sys.exit(1)

print("\nReading 10 interrupt packets (500ms timeout each)...")
for i in range(10):
    data = dev.read(0x100, timeout=500)
    if data:
        print(f"  pkt {i}: {len(data)} bytes  first=0x{data[0]:02x}")
    else:
        print(f"  pkt {i}: timeout (no data)")

dev.close()
