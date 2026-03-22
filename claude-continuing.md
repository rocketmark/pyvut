# Continuing Session: VIVE Ultimate Tracker USB on Raspberry Pi

## Goal
Get the VIVE Ultimate Tracker streaming pose data over USB without Windows/SteamVR.
Eventually: headless SLAM map building on a Pi.

## What works (tested on macOS)
- `hid.enumerate(0x0bb4, 0x06a3)` finds the tracker (VID 0x0bb4, PID 0x06a3)
- **GET commands work**: `PACKET_GET_STATUS (0xa002)` returns live status data
- Status shows: `tracking_mode=1, power=0 (standby), batt=100%, hmd_init=0`
- `send_feature_report` returns 65 (success) for all commands
- ADB connects (`adb devices` shows FA4723B00194) but shell/exec-out are blocked ("error: closed")

## What doesn't work (on macOS)
- **SET commands have no effect**: `set_power_pcvr`, `set_camera_policy`, `set_camera_fps`, ACK sends — all accepted by OS but tracker never changes state
- `power` stays at 0 (WS_STANDBY) no matter what we send
- No pose data on either HID interface (interface 0 or 1)
- ADB pull crashes with protocol fault (FAIL response) — file sync disabled on tracker
- ADB shell/exec-out immediately closes — shell access disabled on tracker

## macOS-specific changes made to pyvut
In `pyvut/tracker_core.py` and `scripts/mac_test.py`, macOS requires a `0x00` report ID prefix byte prepended to `send_feature_report` calls:
```python
out = bytes([0x00]) + out  # macOS hidapi requires report ID prefix
```
**On Linux/Pi this prefix is NOT needed** — remove it or use `prefix=False`.

## Key files
- `scripts/mac_test.py` — minimal standalone HID test script (no pyvut imports), best for debugging
- `hid_test.py` — original test script with all packet constants and parse functions
- `pyvut/tracker_core.py` — main library, `TrackerHID` class for USB mode
- `pyvut/enums_horusd_status.py` — all status/state enums

## Current hypothesis: ADB properties may be required
From comments in `tracker_core.py`:
```
# Sometimes it will not track w/o:
# adb shell setprop persist.lambda.trans_setup 1
# adb shell setprop persist.lambda.normalmode 0
# adb shell setprop persist.lambda.3rdhost 1
```
These are Android system properties that control SLAM behavior. They might also gate whether the tracker accepts USB power mode changes at all. ADB shell is blocked on macOS (and appears blocked universally on this tracker), so we can't set them directly.

## What to try on the Pi

### Step 1: Basic HID test (no prefix needed on Linux)
```bash
pip install hidapi
cd ~/pyvut   # or wherever pyvut is
python hid_test.py
```
Watch for: does `parse_incoming()` return any data? Does `set_power_pcvr(1)` change the power state?

### Step 2: If hid_test.py hangs at device open
The tracker must be awake (solid blue LED) before plugging in USB. Breathing/pulsing blue = standby, won't enumerate data interfaces properly.

### Step 3: Check if prefix breaks things on Linux
`tracker_core.py` was edited to add `0x00` prefix. On Linux this is WRONG. Check line ~579 in tracker_core.py:
```python
out = bytes([0x00]) + out  # macOS hidapi requires report ID prefix  ← REMOVE THIS ON LINUX
```
For Linux, `send_feature_report` takes the packet WITHOUT a report ID prefix byte.

### Step 4: If SET commands work on Linux (power changes from 0)
That means the macOS hidapi/HID stack is the issue. We need a different approach on Mac.
Try streaming pose data with `hid_test.py`'s loop.

### Step 5: If SET commands still don't work on Linux
The tracker needs the Android properties set. Try:
```bash
adb devices   # check if tracker shows up
adb shell setprop persist.lambda.3rdhost 1
adb shell setprop persist.lambda.normalmode 0
adb shell setprop persist.lambda.trans_setup 1
```
On Linux, ADB shell may work (the macOS failure might be a macOS ADB quirk).
After setting props, unplug/replug tracker and retry.

### Step 6: If ADB shell works, also check logs
```bash
adb logcat | grep -i -E "lambda|slam|horus|track|power"
```
This will show what the tracker firmware does when we send HID commands.

## Tracker state machine summary
From `enums_horusd_status.py`:
- `power=0` = WS_STANDBY (current state, stuck here)
- `power=9` = WS_PCVR (desired state — tracking active)
- `tracking_mode=1` = TRACKING_MODE_1 (gyro only) — already set
- `tracking_mode=20` = TRACKING_MODE_SLAM_HOST — for full SLAM

The command to transition: `PACKET_SET_POWER_PCVR (0xa102)` with mode=1 (gyro) or mode=20 (SLAM host).

## Tracker init sequence (from tracker_core.py TrackerHID.__init__)
```python
set_camera_policy(3)        # PACKET_SET_CAMERA_POLICY 0xa106
set_camera_fps(60)          # PACKET_SET_CAMERA_FPS 0xa105
set_power_pcvr(20)          # PACKET_SET_POWER_PCVR 0xa102, mode=TRACKING_MODE_SLAM_HOST
```
That's the entire init. No WiFi, no pairing, no ACKs needed according to the code.

## Raw GET_STATUS response for reference
```
0002a00aff0100640001000000000000
```
Parse (`<BHBB` then 10 bytes of data):
- unk=0x00, cmd_id=0xa002, data_len=10, unk2=0xff
- tracking_mode=1, power=0, batt=100%, hmd_init=0, device_status_mask=0x0001

## Repo location
`pyvut` is at: `/Users/markstalzer/github/pyvut` (macOS)
Clone or rsync to Pi if not already there. All changes are local, not committed.
