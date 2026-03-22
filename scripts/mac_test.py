"""
Minimal direct HID test for macOS — bypasses pyvut abstractions.
Shows exactly what commands succeed and whether data flows.
"""
import hid
import struct
import sys

VID_VIVE = 0x0bb4
PID_TRACKER = 0x06a3
PACKET_SET_CAMERA_POLICY = 0xa106
PACKET_SET_CAMERA_FPS    = 0xa105
PACKET_SET_POWER_PCVR    = 0xa102
PACKET_GET_STATUS        = 0xa002

def send_command(dev, cmd_id, data=None, prefix=True):
    if data is None:
        data = []
    out = struct.pack("<HBB", cmd_id, len(data), 0)
    out += bytes(data)
    out += bytes([0x0] * (0x40 - len(out)))
    if prefix:
        out = bytes([0x00]) + out  # macOS report ID prefix
    print(f"  → sending cmd {hex(cmd_id)} ({len(out)} bytes, prefix={prefix})")
    try:
        ret = dev.send_feature_report(out)
        print(f"  → send_feature_report returned {ret}")
        for i in range(10):
            resp = bytes(dev.get_feature_report(0, 0x40))
            if resp and len(resp) > 4:
                unk, cmd_ret, data_len, unk2 = struct.unpack("<BHBB", resp[:5])
                if cmd_ret != 0:
                    print(f"  ← response: cmd={hex(cmd_ret)} data_len={data_len} raw={resp[:16].hex()}")
                if cmd_ret == cmd_id:
                    return resp[5:5+data_len]
    except Exception as e:
        print(f"  ! exception: {e}")
    return bytes([])

print("=== Enumerating HID devices ===")
devices = hid.enumerate(VID_VIVE, PID_TRACKER)
if not devices:
    print("ERROR: Tracker not found. Is it plugged in and awake?")
    sys.exit(1)

for d in devices:
    print(f"  Found: interface={d['interface_number']} path={d['path']}")

# Open interface 0
iface0 = [d for d in devices if d['interface_number'] == 0]
if not iface0:
    print("ERROR: Interface 0 not found")
    sys.exit(1)

print(f"\n=== Opening interface 0 ===")
dev = hid.device()
dev.open_path(iface0[0]['path'])
print("  Opened OK")

print(f"\n=== Baseline status (no commands) ===")
ret = send_command(dev, PACKET_GET_STATUS)
if ret and len(ret) >= 3:
    tracking_mode, power, batt = struct.unpack("<BBB", ret[:3])
    print(f"  tracking_mode={tracking_mode} power={power} batt={batt}%")

print(f"\n=== set_camera_policy + set_camera_fps (with prefix) ===")
send_command(dev, PACKET_SET_CAMERA_POLICY, [3], prefix=True)
send_command(dev, PACKET_SET_CAMERA_FPS, [60], prefix=True)

print(f"\n=== set_power_pcvr mode=1 (with prefix) then status ===")
send_command(dev, PACKET_SET_POWER_PCVR, [1], prefix=True)
ret = send_command(dev, PACKET_GET_STATUS)
if ret and len(ret) >= 3:
    tracking_mode, power, batt = struct.unpack("<BBB", ret[:3])
    print(f"  after pcvr: tracking_mode={tracking_mode} power={power} batt={batt}%")

print(f"\n=== try OCVR mode (0xa101) ===")
send_command(dev, 0xa101, [1], prefix=True)
ret = send_command(dev, PACKET_GET_STATUS)
if ret and len(ret) >= 3:
    tracking_mode, power, batt = struct.unpack("<BBB", ret[:3])
    print(f"  after OCVR: tracking_mode={tracking_mode} power={power} batt={batt}%")

PACKET_GET_ACK = 0xa113
PACKET_SET_ACK = 0xa116

def send_ack(dev, ack_str, prefix=True):
    data = ack_str.encode("utf-8")
    ret = send_command(dev, PACKET_SET_ACK, list(data), prefix=prefix)
    print(f"  sent ACK {ack_str!r}")
    return ret

def poll_ack(dev):
    ret = send_command(dev, PACKET_GET_ACK, prefix=True)
    if ret and len(ret) > 1 and ret[0] != 0xFF:
        try:
            ack_str = ret[1:].decode("utf-8", errors="replace").rstrip("\x00")
            if ack_str.strip("\x00"):
                print(f"  ← ACK from tracker: {ack_str!r}")
                return ack_str
        except:
            pass
    return None

print(f"\n=== Sending host presence ACKs ===")
send_ack(dev, "ATH1", prefix=True)   # tracking host = 1
send_ack(dev, "AWH1", prefix=True)   # wifi host = 1
send_ack(dev, "FW1", prefix=True)    # unknown but sent early
send_ack(dev, "ARI1", prefix=True)   # role id = 1
ret = send_command(dev, PACKET_GET_STATUS)
if ret and len(ret) >= 3:
    tracking_mode, power, batt = struct.unpack("<BBB", ret[:3])
    print(f"  status after ACKs: tracking_mode={tracking_mode} power={power} batt={batt}%")

print(f"\n=== Polling ACK channel (20 polls) ===")
for i in range(20):
    poll_ack(dev)

print(f"\n=== Reading pose data on interface 0 (10 attempts, 3s timeout each) ===")
for i in range(10):
    print(f"  read attempt {i+1}...")
    resp = bytes(dev.read(0x400, timeout_ms=2000))
    if resp and len(resp) > 0:
        print(f"  GOT DATA on iface0: {len(resp)} bytes, first 16: {resp[:16].hex()}")
        break
    else:
        print(f"  no data")

dev.close()

print(f"\n=== Opening interface 1 and reading ===")
iface1 = [d for d in devices if d['interface_number'] == 1]
if iface1:
    dev1 = hid.device()
    dev1.open_path(iface1[0]['path'])
    print("  Opened interface 1 OK")
    for i in range(5):
        print(f"  read attempt {i+1}...")
        resp = bytes(dev1.read(0x400, timeout_ms=2000))
        if resp and len(resp) > 0:
            print(f"  GOT DATA on iface1: {len(resp)} bytes, first 16: {resp[:16].hex()}")
            break
        else:
            print(f"  no data")
    dev1.close()

print("\nDone.")
