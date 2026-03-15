# pyvut USB Protocol Reference

Derived from reverse-engineering notes in `pyvut/tracker_core.py`, `hid_test.py`, and the `enums_*.py` files. This document covers the direct-USB tracker path (`TrackerHID`) and the wireless dongle path (`DongleHID`) side by side. Everything here is inferred from firmware behaviour — fields marked **[?]** are uncertain.

---

## 1. USB Identifiers

| Constant | Value | Meaning |
|---|---|---|
| `VID_VIVE` | `0x0bb4` | HTC Vive |
| `VID_VALVE` | `0x28de` | Valve |
| `VID_NORDIC` | `0x1915` | Nordic (DFU only) |
| `PID_TRACKER` | `0x06a3` | VIVE Ultimate Tracker (direct USB) |
| `PID_DONGLE` | `0x0350` | VIVE Ultimate Tracker Dongle |
| `HID1_INTERFACE_NUM` | `0` | Primary HID interface (used by pyvut) |
| `HID3_INTERFACE_NUM` | `1` | Secondary HID interface (unused) |

---

## 2. Transport Layer

### 2.1 TrackerHID (direct USB)

All communication uses HID feature reports on interface 0.

**Outgoing command packet — 64 bytes (0x40)**
```
Offset  Size  Type    Field
0       2     u16 LE  cmd_id
2       1     u8      data_len
3       1     u8      0x00 (padding)
4       N     bytes   command data
4+N     …     bytes   0x00 padding to 64 bytes total
```

**Incoming response packet**
```
Offset  Size  Type    Field
0       1     u8      unk [?]
1       2     u16 LE  cmd_id (must match outgoing)
3       1     u8      data_len
4       1     u8      unk2 [?]
5       N     bytes   response data (N = data_len)
```
The host polls `get_feature_report()` up to 10 times waiting for a response with a matching `cmd_id`. If none arrives, an empty result is returned.

**Streaming incoming data** arrives via `device_hid1.read(0x400)` and is processed in `do_loop()`.

---

### 2.2 DongleHID (wireless dongle)

Outgoing commands use HID feature reports on interface 0. Incoming data (poses, ACKs, status) arrives as HID input reports via `device_hid1.read(0x400)`.

**Outgoing command packet — 65 bytes (0x41)**
```
Offset  Size  Type  Field
0       1     u8    0x00
1       1     u8    cmd_id (dongle opcode)
2       1     u8    len(data) + 2
3       N     bytes command data
3+N     …     bytes 0x00 padding to 65 bytes
```

**Incoming packet envelope**
```
Offset  Size  Type    Field
0       1     u8      cmd_id (dongle opcode)
1       1     u8      data_len
2       2     u16 LE  unk [?]
4       N     bytes   payload (N = data_len − 4)
```

---

## 3. Initialisation Sequence

### 3.1 TrackerHID (direct USB)

On construction, the library does the following automatically — no explicit calls needed:

1. Enumerate `VID_VIVE / PID_TRACKER`.
2. Open device on HID interface 0.
3. Send `PACKET_SET_POWER_PCVR` (0xa102) with `TRACKING_MODE_SLAM_HOST` (20).
4. Load `pyvut/wifi_info.json`.

> **Note:** `adb setprop` may be required for SLAM to start. See §8.

### 3.2 DongleHID (wireless dongle)

On construction:

1. Enumerate `VID_VIVE / PID_DONGLE`.
2. Open device on HID interface 0.
3. Query dongle hardware info (these all go out during init):
   - `DCMD_GET_CR_ID` + `CR_ID_PCBID` → PCB ID
   - `DCMD_GET_CR_ID` + `CR_ID_SKUID` → SKU ID
   - `DCMD_GET_CR_ID` + `CR_ID_SN` → serial number
   - `DCMD_GET_CR_ID` + `CR_ID_SHIP_SN` → shipping serial
   - `DCMD_GET_CR_ID` + `CR_ID_CAP_FPC` → cap FPC
   - `DCMD_QUERY_ROM_VERSION` + `0x00` → ROM version
4. Enable pairing: `DCMD_REQUEST_RF_CHANGE_BEHAVIOR` with `RF_BEHAVIOR_PAIR_DEVICE` and `[1, 1, 1, 1, 0, 0]`.
5. Load `pyvut/wifi_info.json`.

---

## 4. Outgoing Commands (Host → Tracker)

All commands below are for `TrackerHID` unless noted. ACK strings are for `DongleHID` sent via `send_ack_to()`.

### 4.1 Command IDs (PACKET_*)

| Constant | Value | Purpose |
|---|---|---|
| `PACKET_FILE_READ` | `0x10` | File read start |
| `PACKET_READ_FILEDATA` | `0x11` | File read data chunk |
| `PACKET_READ_FILEDATA_END` | `0x12` | File read end |
| `PACKET_READ_FILEBIGDATA` | `0x13` | File read large chunk |
| `PACKET_WRITE_FILESIZE` | `0x16` | File write — declare size |
| `PACKET_WRITE_FILEDATA` | `0x17` | File write — data chunk |
| `PACKET_FILE_WRITE` | `0x18` | File write commit |
| `PACKET_FILE_DELETE` | `0x19` | File delete |
| `PACKET_GET_STR_INFO` | `0xa001` | Get string info (device metadata) |
| `PACKET_GET_STATUS` | `0xa002` | Get status register |
| `PACKET_SET_TRACKING_MODE` | `0xa003` | Set tracking mode |
| `PACKET_SET_REBOOT` | `0xa004` | Reboot tracker |
| `PACKET_SET_STR_INFO` | `0xa005` | Set string info |
| `PACKET_SET_POWER_OCVR` | `0xa101` | Set OCVR power mode |
| `PACKET_SET_POWER_PCVR` | `0xa102` | Set PCVR power mode ← **used on init** |
| `PACKET_SET_POWER_EYVR` | `0xa103` | Set EYVR power mode |
| `PACKET_SET_CAMERA_FPS` | `0xa105` | Set camera frame rate [?] |
| `PACKET_SET_CAMERA_POLICY` | `0xa106` | Set camera policy |
| `PACKET_SET_USER_TIME` | `0xa111` | Set tracker clock (seconds via `clock_settime`) [?] |
| `PACKET_SET_OT_STATUS` | `0xa112` | Set OT status [?] |
| `PACKET_GET_ACK` | `0xa113` | Get ACK queue entry |
| `PACKET_SET_PLAYER_STR` | `0xa114` | Set player string |
| `PACKET_SET_HAPTIC` | `0xa115` | Trigger haptic feedback |
| `PACKET_SET_ACK` | `0xa116` | Send ACK response to tracker |
| `PACKET_SET_WATCHDOG_KICK` | `0xa121` | Reset watchdog timer |
| `PACKET_SET_FOTA_BY_PC` | `0xa122` | Start firmware update from PC |
| `PACKET_SET_WIFI_SSID_PW` | `0xa151` | Set WiFi SSID + password together |
| `PACKET_SET_WIFI_FREQ` | `0xa152` | Set WiFi frequency |
| `PACKET_SET_WIFI_SSID` | `0xa153` | Set WiFi SSID |
| `PACKET_SET_WIFI_PW` | `0xa154` | Set WiFi password |
| `PACKET_SET_WIFI_COUNTRY` | `0xa155` | Set WiFi country code |
| `PACKET_GET_WIFI_HOST_INFO` | `0xa156` | Get WiFi host info |
| `PACKET_SET_WIFI_ONLY_MODE` | `0xa157` | Enable WiFi-only mode |
| `PACKET_GET_WIFI_ONLY_MODE` | `0xa158` | Query WiFi-only mode |
| `PACKET_SET_FT_LOCK` | `0xa171` | Face tracking: lock |
| `PACKET_SET_FT_UNLOCK` | `0xa172` | Face tracking: unlock |
| `PACKET_SET_FT_FAIL` | `0xa173` | Face tracking: fail |
| `PACKET_GET_PROPERTY` | `0xa201` | Get property value |
| `PACKET_SET_PROPERTY` | `0xa202` | Set property value |
| `PACKET_SET_FACTORY` | `0xafff` | Factory reset [dangerous] |

### 4.2 ACK Strings (sent via DongleHID to RF-paired trackers)

ACK strings are UTF-8 encoded, prepended with a 1-byte length, and sent inside a `DCMD_TX` packet addressed to the target tracker's MAC.

**TX ACK packet format**
```
Byte 0:     0x10 or 0x11 (partial MAC match mode)
Bytes 1-6:  MAC address (byte[1] ORed with tracker slot index)
Byte 7:     0x00
Byte 8:     0x01
Byte 9:     len(ack_string) as u8
Bytes 10+:  ack_string (UTF-8)
```

| ACK String | Category | Meaning |
|---|---|---|
| `"ANA"` | N | **[?]** sent on connect, possibly `"OT1"`; tracker responds with `"NANA?"` |
| `"ADS"` | N | Device serial number query |
| `"ASS"` | N | Shipping serial query |
| `"ASI"` | N | SKU ID query |
| `"API"` | N | PCB ID query |
| `"AV1"` | N | Firmware version query |
| `"AZZ"` | N | End of device info exchange (no data) |
| `"AGN"` | N | **[?]** data: `0,1,0` (trans_setup, normalmode, 3rdhost) |
| `"ARI"` | N | Role ID |
| `"AFM"` | N | Start firmware update (FOTA) |
| `"APC"` | N | Power off + clear pairing list |
| `"APF"` | N | Power off |
| `"APS"` | N | Standby |
| `"APR"` | N | Reset |
| `"ACF"` | N | Camera FPS |
| `"ACP"` | N | Camera policy |
| `"ATM"` | N | Set tracking mode |
| `"ATH"` | N | Set tracking host flag |
| `"AWH"` | N | Set WiFi host flag |
| `"ATS"` | N | Set user time (seconds) |
| `"ALE"` | N | End map |
| `"ANI"` | N | Set device ID (WiFi-related) |
| `"ATW"` | N | **[?]** enables acceleration data |
| `"LP"` | P | Lambda property (identical use to AGN: `0,1,0`) |
| `"LS"` | P | Lambda status |
| `"P61:key,value"` | P | Set SLAM status key |
| `"P63:key_id"` | P | Query SLAM status key |
| `"P64:command_id"` | P | Send SLAM command |
| `"P82:message_id:data"` | P | SLAM message **[?]** |
| `"Ws"` | W | WiFi SSID (full) |
| `"Wp"` | W | WiFi password |
| `"Wc"` | W | WiFi country code |
| `"Wf"` | W | WiFi frequency |
| `"WS"` | W | WiFi SSID + password together |
| `"WC"` | W | WiFi connect command |
| `"WH"` | W | WiFi host SSID |
| `"FW"` | F | **[?]** purpose unknown; sent with `"1"` on client connect |

---

## 5. Incoming Messages (Tracker → Host)

### 5.1 Pose Data Packet

Arrives as a streaming HID read. For `DongleHID`, pose packets arrive inside an F4 envelope; for `TrackerHID` they arrive directly.

**Outer frame (TrackerHID direct read) — 0x2ce+ bytes**
```
Offset  Size  Type      Field
0       1     u8        unk0 [?]
1       2     u16 LE    pkt_idx
3       4     u32 LE    mask
7       8     u64 LE    hmd_us (timestamp, microseconds)
15      1     u8        hdcc_status0
16      1     u8        hdcc_status1
17      1     u8        ack_in_queue
18      1     u8        device_status
19      17    bytes     unk3 [?]

Then up to 7 pose slots at stride 0x61 (97 bytes each):
  Offset 0x27 + (0x61 × i):
    +0    1  u8      pose_len
    +1    N  bytes   pose_data (see below, N = pose_len)
    +0x59 8  u64 LE  pose_timestamp (milliseconds)

Netsync data at 0x2ce:
  7 × 16-byte entries, each = two u64 LE values [?]
```

**Pose data payload — 0x25 or 0x27 bytes**
```
Offset  Size  Type       Field
0       1     u8         idx (tracker slot, 0-4)
1       1     u8         btns (see button decode below)
2       12    3×f32 LE   pos (XYZ position in metres)
14      8     4×f16 LE   rot (quaternion in firmware order [w,z,y,x])
22      6     3×f16 LE   acc (acceleration)
28      8     4×f16 LE   rot_vel (angular velocity)
36      1     u8         tracking_status
```

**Quaternion reordering:** firmware emits `[w, z, y, x]` as float16. pyvut converts to float32 and reorders to standard `[w, x, y, z]`.

**Button decode (two-packet accumulation):**
- `btns & 0x80` set → this is the upper byte: `button_state = (btns & 0x7F) << 8` (lower byte preserved from prior state)
- `btns & 0x80` clear → this is the lower byte: `button_state |= btns`
- Rising edge of bit 0x100 → triggers `lambda_end_map()` automatically

**Tracking status values:**

| Value | Meaning |
|---|---|
| `2` | Position + rotation valid |
| `3` | Rotation only (position lost) |
| `4` | Pose frozen — position stale, rotation valid |
| `POSE_SYSTEM_NOT_READY (-1)` | System initialising |
| `POSE_NO_IMAGES_YET (0)` | Waiting for camera frames |
| `POSE_NOT_INITIALIZED (1)` | SLAM not started |
| `POSE_OK (2)` | Tracking good |
| `POSE_LOST (3)` | Tracking lost |
| `POSE_RECENTLY_LOST (4)` | Lost < N frames ago |

### 5.2 ACK Response Packets

ACK packets arrive as text strings inside the incoming HID stream. They are parsed by prefix:

| Prefix | Handler | Notes |
|---|---|---|
| `"C…"` | calibration data 1 | Accumulated across multiple packets |
| `"c…"` | calibration data 2 | Accumulated across multiple packets |
| `"N…"` | device info | See list in §4.2 |
| `"P61:"` | SLAM status notification | Format: `P61:key,value` |
| `"P63:"` | SLAM status response | Format: `P63:key_id` |
| `"P64:"` | SLAM command response | Format: `P64:command_id` |
| `"P82:"` | SLAM message | Format: `P82:message_id:data` |
| `"Ws"` | WiFi SSID | |
| `"Wp"` | WiFi password | |
| `"Wc"` | WiFi country | |
| `"Wf"` | WiFi frequency | |
| `"WH"` | Host SSID notification | Triggers client WiFi configuration |
| `"WC"` | WiFi connect result | Updates `connected_to_host[idx]` |
| `"MS"` | Map status update | Passed to `handle_map_state()` |
| `"APF"` or `"APR"` | Tracker power off / reset | Triggers `handle_disconnected()` |
| `"NANA?"` | **[?]** sent on connect | Purpose unknown |

### 5.3 Tracker Status Packet (DongleHID pair state)

Received when a tracker pairs or unpairs.

```
Offset  Size  Type      Field
0       1     u8        unk [?]
1       1     u8        data_len
2       1     u8        (data marker, skipped)
3       1     u8        unk [?]
4       4     u32 LE    pair_state[0]
8       4     u32 LE    pair_state[1]
12      4     u32 LE    pair_state[2]
16      4     u32 LE    pair_state[3]
20      4     u32 LE    pair_state[4]
```

**Pair state bitmask values:**

| Bit | Constant | Meaning |
|---|---|---|
| `0x0001` | `PAIRSTATE_1` | [?] |
| `0x0002` | `PAIRSTATE_2` | [?] |
| `0x0004` | `PAIRSTATE_4` | [?] |
| `0x0008` | `PAIR_STATE_PAIRED` | Device is bonded |
| `0x0010` | `PAIRSTATE_10` | [?] |

Known observed values: `0x4000003` (unpaired, pairing info present), `0x1000003` (unpaired, no pairing info), `0x320fc008` / `0x320ff808` / `0x320fa808` (paired).

### 5.4 Dongle F4 Incoming Packet (DongleHID tracker data envelope)

```
Offset  Size  Type      Field
0       1     u8        cmd_id (always DRESP_F4)
1       2     u16 LE    pkt_idx
3       6     bytes     device_addr (MAC address)
9       2     u16 LE    type_maybe
11      1     u8        data_len
12      N     bytes     payload
```

`type_maybe` values:

| Value | Meaning |
|---|---|
| `0x101` | ACK string from tracker |
| `0x110` | Pose data from tracker |
| other | Generic data; `payload[0]` = data_id |

---

## 6. State Machines

### 6.1 Tracker Work States

Set internally by the tracker in response to power and tracking commands.

```
WS_STANDBY     (0)  → idle
WS_CONNECTING  (1)  → RF pairing in progress
WS_REPAIRING   (2)  → re-pairing after dropout
WS_CONNECTED   (3)  → paired, not yet tracking
WS_TRACKING    (4)  → 6DoF pose stream active
WS_RECOVERY    (5)  → recovering from lost tracking
WS_REBOOT      (6)  → rebooting
WS_SHUTDOWN    (7)  → powering off
WS_OCVR        (8)  → OCVR (Oculus-style) power mode
WS_PCVR        (9)  → PCVR power mode ← normal operating mode
WS_EYVR        (10) → EYVR power mode
WS_RESTART     (11) → restarting
```

### 6.2 SLAM Map States

Progresses automatically based on firmware SLAM engine state, reported via `ACK_MAP_STATUS` / `P61:KEY_MAP_STATE,value`. pyvut monitors and detects stalls.

```
MAP_NOT_CHECKED               (0)  → initial state on connect
MAP_EXIST                     (1)  → map found on tracker storage
MAP_NOTEXIST                  (2)  → no map found
MAP_REBUILT                   (3)  → map reconstructed
MAP_SAVE_OK                   (4)  → map saved to storage
MAP_SAVE_FAIL                 (5)  → save failed
MAP_REUSE_OK                  (6)  → existing map reused successfully
MAP_REUSE_FAIL_FEATURE_DIFF   (7)  → reuse failed: features too different
MAP_REUSE_FAIL_FEATURE_LESS   (8)  → reuse failed: insufficient features
MAP_REBUILD_WAIT_FOR_STATIC   (9)  → waiting for scene to be static
MAP_REBUILD_CREATE_MAP        (10) → actively building new map
```

**Stall detection thresholds (pyvut-side):**
- `stuck_on_static > 7` at `MAP_REBUILD_WAIT_FOR_STATIC` → trigger map bump
- `stuck_on_exists > 3` → reset counter
- `stuck_on_not_checked > 3` → reset counter

### 6.3 SLAM Pose States

| Value | Constant | Meaning |
|---|---|---|
| `-1` | `POSE_SYSTEM_NOT_READY` | Subsystem not initialised |
| `0` | `POSE_NO_IMAGES_YET` | No camera frames yet |
| `1` | `POSE_NOT_INITIALIZED` | SLAM not started |
| `2` | `POSE_OK` | Full 6DoF tracking |
| `3` | `POSE_LOST` | Tracking lost |
| `4` | `POSE_RECENTLY_LOST` | Recently lost (may recover) |

Extended pose states (from `POSESTATE_*`):

| Value | Constant | Meaning |
|---|---|---|
| `0` | `POSESTATE_OK` | Good |
| `1` | `POSESTATE_LOST` | Lost |
| `2` | `POSESTATE_UNINITIALIZED` | Not started |
| `3` | `POSESTATE_RECOVER` | Recovering |
| `4` | `POSESTATE_FOV_BOUNDARY` | Near camera FOV edge |
| `5` | `POSESTATE_FOV_OCCLUSION` | Camera occluded |
| `6` | `POSESTATE_DEAD_ZONE` | Dead zone |
| `7` | `POSESTATE_NOMEASUREMENT` | No measurement |
| `8` | `POSESTATE_NONCONVERGE` | SLAM not converged |
| `9` | `POSESTATE_IK` | IK mode |
| `10` | `POSESTATE_INTEGRATOR` | Integrator mode |
| `11` | `POSESTATE_NEW_MAP` | Building new map |

### 6.4 SLAM KEY Indices

Used in `P61:` (set), `P63:` (query) ACK strings to identify which status value is being exchanged.

| Index | Constant | Meaning |
|---|---|---|
| `0` | `KEY_TRANSMISSION_READY` | WiFi map transmission ready |
| `1` | `KEY_RECEIVED_FIRST_FILE` | First map file received |
| `2` | `KEY_RECEIVED_HOST_ED` | Host essential data received |
| `3` | `KEY_RECEIVED_HOST_MAP` | Host map received |
| `4` | `KEY_CURRENT_MAP_ID` | Current map UUID |
| `5` | `KEY_MAP_STATE` | Map state value (§6.2) |
| `6` | `KEY_CURRENT_TRACKING_STATE` | Tracking state value (§6.3) |

### 6.5 SLAM Commands (P64:)

| Value | Constant | Meaning |
|---|---|---|
| `0` | `ASK_ED` | Request essential data from host |
| `1` | `ASK_MAP` | Request map from host |
| `2` | `KF_SYNC` | Request keyframe sync |
| `3` | `RESET_MAP` | Reset map on tracker |

### 6.6 Tracking Modes

| Value | Constant | Notes |
|---|---|---|
| `-1` | `TRACKING_MODE_NONE` | Reads `persist.lambda.3rdhost` [?] |
| `1` | `TRACKING_MODE_1` | Gyro only; sets `3rdhost=0, normalmode=1, trans_setup=0` |
| `2` | `TRACKING_MODE_2` | Body tracking [?] |
| `11` | `TRACKING_MODE_SLAM_CLIENT` | SLAM client; sets `3rdhost=0, normalmode=0` |
| `20` | `TRACKING_MODE_SLAM_HOST` | SLAM host; sets `3rdhost=1, normalmode=0` ← default on USB init |
| `21` | `TRACKING_MODE_21` | Body tracking variant [?] |
| `51` | `TRACKING_MODE_51` | SetUVCStatus [?] |

---

## 7. Pairing and Role Assignment Flow (DongleHID)

This sequence runs automatically once `DongleHID` is constructed and `do_loop()` is called.

```
Host PC                              Dongle                    Tracker
   |                                    |                          |
   |-- DCMD_REQUEST_RF_CHANGE_BEHAVIOR ->|                          |
   |   (enable pairing)                  |                          |
   |                                    |<-- RF pair event ---------|
   |<-- DRESP_PAIR_EVENT (MAC) ----------|                          |
   |                                    |                          |
   |-- ACK: ack_set_role_id(idx, 1) ----------------------------------->|
   |-- ACK: ack_set_tracking_mode(idx, TRACKING_MODE_NONE) ------------>|
   |                                    |                          |
   | [if first tracker = host]          |                          |
   |-- ACK: WiFi country ------------------------------------------------>|
   |-- ACK: ack_set_tracking_host(idx, 1) -------------------------------->|
   |-- ACK: ack_set_wifi_host(idx, 1) ------------------------------------>|
   |-- ACK: ack_set_new_id(idx, 0) ---------------------------------------->|
   |-- ACK: ack_set_tracking_mode(idx, TRACKING_MODE_SLAM_HOST) ---------->|
   |                                    |                          |
   | [if subsequent tracker = client]   |                          |
   |-- ACK: ack_set_tracking_host(idx, 0) -------------------------------->|
   |-- ACK: ack_set_wifi_host(idx, 0) ------------------------------------>|
   |-- ACK: ack_set_new_id(idx, slot_index) -------------------------------->|
   |-- ACK: ack_set_tracking_mode(idx, TRACKING_MODE_SLAM_CLIENT) -------->|
   |                                    |                          |
   |<-- ACK responses, device info, calibration data -------------------|
   |<-- Pose stream begins (after tracker reaches WS_TRACKING) ---------|
```

---

## 8. Periodic Maintenance

### Watchdog (TrackerHID)

- Increment `watchdog_delay` after each outgoing command.
- When `watchdog_delay >= 1000`: send `PACKET_SET_WATCHDOG_KICK`, reset to 0.
- Failure to kick the watchdog may cause the tracker to stop streaming.

### Periodic SLAM Status Queries (DongleHID)

- Increment `tick_periodic` on each incoming packet.
- When `tick_periodic > 1000`: reset to 0, then for each client tracker that is connected send:
  - `ack_lambda_ask_status(idx, KEY_TRANSMISSION_READY)`
  - `ack_lambda_ask_status(idx, KEY_CURRENT_MAP_ID)`
  - `ack_lambda_ask_status(idx, KEY_MAP_STATE)`
  - `ack_lambda_ask_status(idx, KEY_CURRENT_TRACKING_STATE)`
  - `ack_lambda_ask_status(idx, KEY_RECEIVED_HOST_ED)` (clients only)
  - `ack_lambda_ask_status(idx, KEY_RECEIVED_HOST_MAP)` (clients only)

### Host Map Request Backoff

- When `KEY_RECEIVED_HOST_MAP == 0` and `now - last_host_map_ask_ms > 10000`: send `ASK_MAP`, update timestamp.

---

## 9. What Is and Isn't Sent Over USB

| Function | Transport |
|---|---|
| Pose data (position, rotation, acceleration, buttons) | USB HID (direct) or RF→USB (dongle) |
| ACK string exchange (config, mode, WiFi credentials) | USB HID (direct) or RF→USB (dongle) |
| Watchdog kick | USB HID (direct only) |
| Haptic trigger | USB HID |
| Camera FPS / policy | USB HID |
| Power mode (PCVR / OCVR / EYVR) | USB HID |
| Device info queries (SN, PCB ID, version) | USB HID |
| SLAM map data sync between trackers | **WiFi only** (not USB) |
| SLAM essential data (ED) | **WiFi only** |
| SLAM keyframe sync | **WiFi only** |
| `adb setprop` (tracker-side properties) | **adb / USB ADB only** — not HID |
| Map wipe (`/data/lambda`, `/data/mapdata`) | **adb shell only** |

---

## 10. Error Codes

### HID-level errors

| Value | Constant | Meaning |
|---|---|---|
| `0x2` | `ERR_BUSY` | Device busy |
| `0x3` | `ERR_03` | [?] |
| `0xEE` | `ERR_UNSUPPORTED` | Command not supported |

### SLAM / System error codes

| Value | Meaning |
|---|---|
| `1100` | `ERROR_NO_CAMERA` |
| `1121` | `ERROR_CAMERA_SSR_1` |
| `1122` | `ERROR_CAMERA_SSR_2` |
| `1200` | `ERROR_NO_IMU` |
| `1300` | `ERROR_NO_POSE` |

These arrive in `P82:0:error_code` (LAMBDA_MESSAGE_ERROR) ACK strings.

---

## 11. Known Unknowns and Caveats

The following are taken directly from source comments:

- **SLAM often requires `adb setprop`** before tracking will start over USB:
  ```
  adb shell setprop persist.lambda.trans_setup 1
  adb shell setprop persist.lambda.normalmode 0
  adb shell setprop persist.lambda.3rdhost 1
  ```
- **`TRACKING_MODE_1` may corrupt `trans_setup`** — see comment in `tracker_core.py:44`.
- **Client trackers sometimes require a map wipe** before they will sync:
  ```
  adb shell rm -rf /data/lambda/
  adb shell mkdir -p /data/lambda/
  adb shell rm /data/mapdata/*
  ```
- **`DCMD_21` (0x21) bricked at least one dongle.** It is listed in `DCMDS_THAT_WRITE_FLASH` and `DCMDS_THAT_RESTART`. Do not send it.
- **`is_host()` always returns `True`** — multi-host arbitration is unimplemented (`TODO` in source).
- **Tracker MAC addresses are described as "fake"** — the lower nibble of byte[1] is used as a slot index but may not reflect real hardware MACs.
- **`ACK_ANA` / `"NANA?"`** is received from the tracker on connect but its purpose is unknown.
- **`ACK_ATW`** may enable acceleration data in the pose stream — unconfirmed.
- **`ACK_FW "1"`** is sent to clients after device info exchange — purpose unknown.
- **The first byte of certain dongle responses is always `0xFF`** — reason unknown.
- **WiFi 5 GHz command IDs** (`WIFI_CMD_*`) are defined but their exact packet formats are not fully documented.
- **`PACKET_SET_USER_TIME`** calls `clock_settime` on the tracker — interaction with SLAM timestamps not characterised.
- **Netsync fields** at offset `0x2ce` in the pose frame are parsed but not used.
