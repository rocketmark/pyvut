# VIVE Ultimate Tracker — Research Notes

Compiled from reverse engineering of pyvut source code, official HTC documentation, and community findings. This document captures everything currently known about the tracker's SLAM map system, map commands, and headless operation.

---

## 1. Map Architecture

### The map lives on the tracker

The SLAM map is stored entirely on the tracker's own Android filesystem:

```
/data/lambda/       ← SLAM keyframe and feature data
/data/mapdata/      ← map data files
```

The PC (VIVE Hub) does not store the map. The tracker builds it, saves it, and reuses it independently. This is confirmed by:
- `clear_maps.sh` using `adb shell rm -rf /data/lambda /data/mapdata`
- The `MAP_EXIST` / `MAP_REUSE_OK` states being reported by the tracker on boot — it checks its own storage
- The pyvut README: "after the tracker stores the map, you can return to Ubuntu"

Each stored map has a UUID, tracked by `KEY_CURRENT_MAP_ID` and reported via `LAMBDA_MESSAGE_UPDATE_MAP_UUID`.

### VIVE Hub is a UI overlay, not a map builder

VIVE Hub does not feed data to the tracker during map building. It reads status back (likely via `KEY_MAP_STATE` queries) and renders a progress ring in its UI. The ring filling white is a visualisation of feature coverage reported by the tracker — VIVE Hub does not control when the save happens. The save is triggered automatically by the tracker's SLAM engine when enough features have been accumulated.

**Implication:** map building can be fully replicated by pyvut without VIVE Hub — VIVE Hub is just monitoring the same state machine that pyvut can already query.

---

## 2. The Official Map Building Process (VIVE Hub)

From HTC's documentation: https://www.vive.com/us/support/ultimate-tracker/category_howto/creating-a-tracking-map.html

1. Open VIVE Hub → Settings → VIVE Ultimate Tracker → "Start setup" → "Create tracking map"
2. **Set center point** — hold tracker at ~120 cm height, face PC screen, walk 150 cm backward, press power button
3. **Kneeling scan** — sweep tracker up/down/left/right in four compass directions while kneeling at center; a ring fills as features are recognised
4. **Standing scan** — same four-direction sweep repeated while standing
5. **Refine** (optional) — re-sweep any underscanned zones
6. **Auto-save** — when all ring segments fill white, the map saves automatically. No user action needed.

The SLAM subsystem on the tracker is named "lambda" throughout the firmware (Android properties `persist.lambda.*`, log files at `/data/tracking_log/slam.log.*`, `/data/tracking_log/horusd.log.0`).

---

## 3. Map State Machine

The tracker reports its map status via `KEY_MAP_STATE` (SLAM key index 5), queried with `P63:5` and received as `P61:5,value`.

```
MAP_NOT_CHECKED               (0)  initial state on connect
MAP_EXIST                     (1)  map found in storage
MAP_NOTEXIST                  (2)  no map in storage → auto-starts building
MAP_REBUILT                   (3)  map reconstructed in memory
MAP_SAVE_OK                   (4)  map written to /data/lambda, /data/mapdata ✓
MAP_SAVE_FAIL                 (5)  write failed
MAP_REUSE_OK                  (6)  stored map matched current environment ✓
MAP_REUSE_FAIL_FEATURE_DIFF   (7)  features too different from stored map
MAP_REUSE_FAIL_FEATURE_LESS   (8)  too few features visible
MAP_REBUILD_WAIT_FOR_STATIC   (9)  waiting for scene to be still
MAP_REBUILD_CREATE_MAP        (10) actively building
```

**Normal boot with stored map:**
```
NOT_CHECKED → EXIST → REUSE_OK → poses valid
```

**First boot or map wiped:**
```
NOT_CHECKED → NOTEXIST → REBUILD_WAIT_FOR_STATIC → REBUILD_CREATE_MAP → REBUILT → SAVE_OK
```

**Degraded environment (map stale):**
```
NOT_CHECKED → EXIST → REUSE_FAIL_FEATURE_DIFF or REUSE_FAIL_FEATURE_LESS
```

---

## 4. Map Commands

### Active (confirmed working)

| Command | Wire string | Trigger |
|---|---|---|
| `lambda_end_map(device_addr)` | `"ALE"` | Button press (bit 0x100 rising edge) |
| `lambda_end_map(device_addr)` | `"ALE"` | `MAP_NOT_CHECKED` stuck, client has host map |
| `send_ack_to(idx, "P64:3")` | `"P64:3"` (RESET_MAP) | On `ACK_AZZ` — client only, after device info exchange |
| `send_ack_to(idx, "P64:1")` | `"P64:1"` (ASK_MAP) | `KEY_RECEIVED_HOST_MAP == 0` and >10s since last ask |
| `ack_lambda_ask_status(i, KEY_MAP_STATE)` | `"P63:5"` | Periodic tick |
| `ack_lambda_ask_status(i, KEY_CURRENT_MAP_ID)` | `"P63:4"` | Periodic tick |
| `ack_lambda_ask_status(i, KEY_RECEIVED_HOST_MAP)` | `"P63:3"` | Periodic tick (clients only) |
| `ack_lambda_ask_status(i, KEY_RECEIVED_HOST_ED)` | `"P63:2"` | Periodic tick (clients only) |
| `ack_lambda_ask_status(i, KEY_TRANSMISSION_READY)` | `"P63:0"` | Periodic tick |

### Commented out — with inferred meanings

| Command | Wire string | Author note | Inferred meaning |
|---|---|---|---|
| `"P64:3"` (RESET_MAP) | `MAP_REBUILD_WAIT_FOR_STATIC` | no note | Reset SLAM state while stuck waiting for static scene |
| `"ALE"` to host from client | `MAP_REBUILD_WAIT_FOR_STATIC` | no note | End host map session to force clients to resync; may restart multi-tracker map session |
| `"ALE"` to client | `MAP_REBUILD_WAIT_FOR_STATIC` | no note | End client map session; client re-enters map state machine from top |
| `"P64:1"` (ASK_MAP) to host | `MAP_REBUILD_WAIT_FOR_STATIC` | **"doesn't work?"** | Tell host to push map to WiFi channel — host may need to be in a ready state first |
| `"P64:3"` (RESET_MAP) to client | `MAP_REBUILD_WAIT_FOR_STATIC` | no note | Same as above but targeting client directly |
| `"P61:5,3"` | Force MAP_STATE=MAP_REBUILT | no note | Spoof map state to skip WAIT_FOR_STATIC; almost certainly ignored (derived sensor state) |
| `"P61:6,10"` | Force TRACKING_STATE=MAP_REBUILD_CREATE_MAP | no note | Same; almost certainly ignored |
| `"ALE"` to host | `MAP_EXIST` stuck | no note | Break out of stuck MAP_EXIST; unclear if safe |
| `"ALE"` to host | `ACK_AZZ` (host path) | no note | End map on host at end of device info; commented in favour of doing nothing |
| `"ALE"` to client | `KEY_RECEIVED_HOST_MAP > 0` bump_map_once | no note | End map after client receives host map — may be an attempt to trigger re-init |
| `"ATM-1"` to client | same bump_map_once | no note | Switch to TRACKING_MODE_NONE to trigger re-read of `persist.lambda.3rdhost` |
| `"ATM1"` to client | same bump_map_once | no note | Switch to gyro-only mode as a forced SLAM re-init; known to corrupt `trans_setup` |
| `"P64:1"` (ASK_MAP) to host | `KEY_RECEIVED_HOST_MAP == 0` | **"doesn't work?"** | Ask host to push map; both host and client direction tried, both failed |
| `"P64:1"` (ASK_MAP) to client | `KEY_RECEIVED_HOST_MAP == 0` | **"doesn't do anything?"** | Same, targeting client |
| `"P64:3"` (RESET_MAP) to client | `KEY_RECEIVED_HOST_MAP == 0` | no note | Reset client map while waiting for host map |
| `"P64:0"` (ASK_ED) to client | `KEY_RECEIVED_HOST_ED == 0` | no note | Ask client to request essential data from host |
| `"P64:0"` (ASK_ED) to client | `KEY_TRANSMISSION_READY` | no note | Ask for ED when transmission ready fires |
| `"P64:3"` (RESET_MAP) to client | `KEY_TRANSMISSION_READY` | no note | Reset map when transmission readiness changes |
| `"P64:0"` (ASK_ED) | `ACK_LAMBDA_STATUS b != 2` | no note | Request ED when SLAM not in ready state (b=2) |
| `"P64:1"` (ASK_MAP) | `ACK_LAMBDA_STATUS b != 2` | no note | Request map when SLAM not ready |
| `"P64:2"` (KF_SYNC) | `ACK_LAMBDA_STATUS b != 2` | no note | Request keyframe sync when SLAM not ready — lighter than full map |
| `"ALE"` to all | `ACK_MAP_STATUS` received | no note | End map whenever any map status arrives |
| `"ALE"` | TrackerHID do_loop | no note | End map every loop tick — clearly exploratory |
| `"P63:4"` | DongleHID do_loop | no note | Query map ID every loop tick — clearly exploratory |

---

## 5. Understanding P61 (ACK_LAMBDA_SET_STATUS)

### What P61 is

`P61` is the "set status" direction of the SLAM key protocol. Format: `"P61:key,value"`. Its counterpart `P63` (ask status) is confirmed working — the tracker faithfully returns values when queried.

### Why the two attempted P61 values probably don't work

The two attempts (`P61:5,3` and `P61:6,10`) targeted `KEY_MAP_STATE` (5) and `KEY_CURRENT_TRACKING_STATE` (6). These are **derived sensor states** — the SLAM engine computes them continuously from camera frames, IMU, and feature matching. Writing to them from outside is like changing a speedometer reading to change speed. The engine recomputes them on the next cycle and the write is ignored.

### Which P61 keys are probably writable

Keys 0–3 are coordination flags, not sensor outputs:

| Key | Name | Why it's probably writable |
|---|---|---|
| 0 | `KEY_TRANSMISSION_READY` | WiFi map readiness handshake flag — only meaningful as an external signal |
| 1 | `KEY_RECEIVED_FIRST_FILE` | File transfer acknowledgement — must be externally settable |
| 2 | `KEY_RECEIVED_HOST_ED` | ED receipt acknowledgement — must be externally settable |
| 3 | `KEY_RECEIVED_HOST_MAP` | Map receipt acknowledgement — must be externally settable |

These flags have no sensor inputs. The only way they change is via a message saying "I sent/received this data." That means they **must** be writable from outside — otherwise there would be no way for one tracker to tell another "I got your file."

### The orphaned comment

Line 79 of `enums_horusd_ack.py`:
```python
# P61:0,1
```

This is the only comment in the enum file that looks like an unmade call rather than documentation. `P61:0,1` means "set KEY_TRANSMISSION_READY = 1" on the host — the probable trigger for the host to start pushing its map to the WiFi channel. It was noted and never tried.

### Hypotheses to test

In priority order:

1. **`P61:0,1` to host** — set KEY_TRANSMISSION_READY = 1. If this gates the host's WiFi map broadcast, it would start the map push to all connected clients. Most likely candidate for unblocking multi-tracker sync.

2. **`P61:3,0` to client before ASK_MAP** — reset "map received" flag to 0. The client may believe it already has the map from a previous session (flag = 1) and therefore not re-request. Forcing to 0 ensures the client is actually waiting.

3. **`P61:2,0` then `P64:0` (ASK_ED) to client** — reset "ED received" flag, then request ED. Same logic as above but for the essential data step that precedes map transfer.

---

## 6. Multi-Tracker Map Architecture

### The host is the sole map author

The host tracker builds the map, stores it, and pushes it to clients over WiFi. Evidence:
- `KEY_RECEIVED_HOST_MAP` and `KEY_RECEIVED_HOST_ED` are named from the client's perspective
- `ASK_ED` and `ASK_MAP` commands flow from client→host, never the other direction
- The host broadcasts a WiFi AP; data flows one way across it
- No "send my map to host" command exists anywhere in the codebase

### Client localisation

Clients don't build independent maps. They download the host's map and **localise within it** — running their own cameras to find their position in the host's coordinate frame. This is what `MAP_REUSE_OK/FAIL` means on a client: "I tried to match my camera view against the downloaded map, and it worked/failed."

### What MAP_REBUILD means on a client

When `MAP_REUSE_FAIL` occurs, the client falls back to building its own local map (`MAP_REBUILD_CREATE_MAP`). This local map is **unanchored** to the host's coordinate frame — the client's pose will be in a different space than the host's. This is the broken state that most of the stuck-state recovery code is trying to escape.

### Multi-tracker WiFi sequence (inferred)

```
1. Host reaches MAP_SAVE_OK (has a valid stored map)
2. Host sets KEY_TRANSMISSION_READY = 1 (autonomous, or via P61:0,1)
3. Host starts pushing map files over WiFi AP
4. Client: KEY_RECEIVED_FIRST_FILE → 1 (P61:1,1)
5. Client: KEY_RECEIVED_HOST_ED → 1 (P61:2,1)
6. Client: KEY_RECEIVED_HOST_MAP → 1 (P61:3,1)
7. Client SLAM engine initialises with host map
8. Client: MAP_STATE → MAP_EXIST → MAP_REUSE_OK
9. Client poses are now in host's coordinate frame
```

---

## 7. Single-Tracker Headless Operation (Pi Use Case)

For a single tracker connected to a Pi with no other trackers, the entire multi-tracker WiFi protocol is irrelevant. The only map question is:

**Does MAP_REUSE_OK arrive after boot?**

- `MAP_REUSE_OK` → poses valid, Pi can start streaming
- `MAP_REUSE_FAIL_FEATURE_DIFF` → environment changed too much, map rebuild needed
- `MAP_REUSE_FAIL_FEATURE_LESS` → not enough visual features visible (bad lighting, featureless walls)
- `MAP_NOTEXIST` → map was wiped or never built; full rebuild needed

The Pi workflow:
```
Boot tracker via USB
  ↓
Set WS_PCVR + TRACKING_MODE_SLAM_HOST (auto on init)
  ↓
Poll KEY_MAP_STATE via P63:5
  ↓
MAP_REUSE_OK → start streaming poses
MAP_REUSE_FAIL_* → alert operator, map rebuild needed
MAP_NOTEXIST → map needs to be built (see §8)
```

### Headless map rebuild on Pi (unconfirmed)

The map state machine suggests rebuilding may be possible without VIVE Hub:

```
1. Wipe existing map:
   Option A: adb shell rm -rf /data/lambda /data/mapdata  (confirmed works)
   Option B: send RESET_MAP (P64:3) — unknown if this wipes disk or only memory

2. Tracker enters MAP_NOTEXIST → auto-starts MAP_REBUILD_CREATE_MAP

3. Operator walks the space with tracker (same scan pattern as VIVE Hub)

4. Poll KEY_MAP_STATE until MAP_SAVE_OK (4)

5. Map is stored on tracker, valid until environment changes significantly
```

The key unknown: whether `RESET_MAP` (P64:3) wipes `/data/lambda` and `/data/mapdata` on disk, or only resets in-memory SLAM state. If only in-memory, `adb` is still required for a clean rebuild.

---

## 8. ALE vs Save — Important Distinction

`ALE` (`ACK_END_MAP`) ends the active map **session**. It does not trigger a save. The save is automatic — the tracker's SLAM engine saves when it has accumulated sufficient features. `MAP_SAVE_OK` (4) confirms the save happened.

In the map state machine:
```
MAP_REBUILD_CREATE_MAP (10) → [tracker decides it has enough features] → MAP_REBUILT (3) → MAP_SAVE_OK (4)
```

None of these transitions require a host command. They are internal to the SLAM engine.

`ALE` is best understood as "abandon this session" or "end the current tracking epoch." It causes the tracker to re-enter the map state machine from `MAP_NOT_CHECKED`. It may be useful for forcing a client to re-evaluate its map, or to break a stuck state — but it does not commit a map save.

---

## 9. adb Dependency

Several operations currently require `adb shell` access to the tracker's Android system. These are not available via HID:

| Operation | adb command |
|---|---|
| Wipe stored map | `rm -rf /data/lambda/ /data/mapdata/` |
| Set SLAM mode properties | `setprop persist.lambda.3rdhost 1` |
| Set SLAM mode properties | `setprop persist.lambda.normalmode 0` |
| Set SLAM mode properties | `setprop persist.lambda.trans_setup 1` |
| Read SLAM logs | `cat /data/tracking_log/slam.log.*` |
| Read horusd logs | `cat /data/tracking_log/horusd.log.0` |

The `persist.lambda.*` properties may persist across reboots — if set once via adb during a Windows session, they may not need to be set again on a Pi. This needs hardware verification.

`RESET_MAP` (P64:3) is the only candidate HID command for replacing the adb map wipe, but its scope (memory vs disk) is unknown.

---

## 10. LBE (Enterprise) Map Sharing

HTC provides an **LBE Configuration Tool** (separate from VIVE Hub, requires VIVE Business+ subscription) that can upload a shared map from one tracker to multiple trackers simultaneously. This is the enterprise "shared space" feature for location-based entertainment deployments.

The LBE tool is versioned separately (e.g. `0.0.0.6a`) and firmware compatibility issues have been reported. This is a different mechanism from the wireless host/client WiFi sync — it appears to transfer map files directly (possibly via USB or a different channel).

This is not currently reverse engineered in pyvut.

---

## 11. Open Questions

| Question | Priority | How to test |
|---|---|---|
| Does `RESET_MAP` (P64:3) wipe stored map files or only in-memory state? | HIGH | Send P64:3, reboot, check if MAP_NOTEXIST or MAP_EXIST |
| Do `persist.lambda.*` properties persist across reboots? | HIGH | Set via adb, reboot without adb, check if SLAM mode is correct |
| Does `P61:0,1` to host trigger WiFi map broadcast to clients? | HIGH | Two-tracker test: send P61:0,1 to host, watch client KEY_RECEIVED_HOST_MAP |
| Does `ALE` on the host restart the multi-tracker map session? | MEDIUM | Two-tracker test: send ALE to host, watch both trackers' map states |
| What does `P61:3,0` (reset "map received" flag) do on client? | MEDIUM | Send before ASK_MAP, watch whether client re-requests map |
| Does map building work headlessly (no VIVE Hub) if tracking mode + PCVR are set? | HIGH | Boot tracker via pyvut only, wipe map, watch for MAP_REBUILD_CREATE_MAP |
| What is the `ACK_LAMBDA_STATUS` payload format (a, b, c values)? | MEDIUM | Log all LS responses across sessions to find pattern |
| What does `LAMBDA_PROP_DEVICE_CONNECTED = 58` do? | LOW | Send P58 and observe response |
| What does `ACK_ATW "ATW"` do — does it enable acceleration data? | LOW | Send ATW, check if acc fields in pose packets populate |
| Does `LAMBDA_PROP_SAVE_MAP = 80` accept a P6x command? | LOW | Try P64:80 or P61:80,1 on host after MAP_REBUILT |

---

## Sources

- HTC official documentation: https://www.vive.com/us/support/ultimate-tracker/category_howto/creating-a-tracking-map.html
- VIVE developer portal: https://developer.vive.com/resources/hardware-guides/vive-ultimate-tracker-guidelines/
- Original reverse engineering: https://github.com/shinyquagsire23/vive_ultimate_tracker_re
- pyvut upstream: https://github.com/nijkah/pyvut
- HTC forum (LBE map tool): https://forum.htc.com/topic/18633-new-vive-ultimate-tracker-149-firmware-not-working-with-lbe-map-tool-0006a/
- HTC forum (multi-tracker map): https://forum.htc.com/topic/19993-getting-the-map-onto-the-trackers/
- pyvut source: `pyvut/tracker_core.py`, `pyvut/enums_horusd_ack.py`, `pyvut/enums_horusd_status.py`
- pyvut protocol reference: [usb_protocol.md](usb_protocol.md)
- Stagehand integration assessment: [stagehand_integration.md](stagehand_integration.md)
