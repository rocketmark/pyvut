# VIVE Ultimate Tracker + pyvut → Stagehand Integration Assessment

This document assesses the feasibility of integrating pyvut (VIVE Ultimate Tracker driver) into the Stagehand tracking appliance as an alternative or supplementary pose source.

---

## What Stagehand Is

Stagehand is a PoE-powered Raspberry Pi-based 6DoF tracking appliance for virtual production. The Pi runs **libsurvive** (a C library) to solve tracker poses from SteamVR base stations, then streams them over UDP (SHTP v2 protocol) to a Windows PC client, which forwards to Unreal Engine via a LiveLink plugin. The system is tightly coupled C on the Pi, Python on the Windows side.

```
Tracker (SteamVR base stations + Vive Tracker 2.0)
    ↓ USB HID
Raspberry Pi 4
  stagehand-agent (C)
    libsurvive → pos[3] + rot[4] (WXYZ)
    SHTP v2 UDP → port 7500
    ↓
Windows PC
  stagehand_client (Python)
    One Euro filter, reflection rejection
    SMLL v1 UDP → port 16666 (localhost)
    ↓
Unreal Engine
  StageManagerLiveLink plugin
    LiveLink subjects: SM_Tracker_*, SM_Encoder_*
```

---

## Summary Verdict

**Feasible, moderate effort, with one blocking question to resolve first.**

The pose data model is a clean match. The main challenges are the language boundary (C agent vs. Python pyvut), the absence of a plugin interface in the agent, and uncertainty around whether the Ultimate Tracker can reuse a stored SLAM map headlessly on a Pi.

---

## What Makes It Possible

**Pose model is a direct match.**
Stagehand's agent needs `pos[3]` (meters) and `rot[4]` (WXYZ quaternion). pyvut produces exactly this as `TrackerPose.position` and `TrackerPose.rotation`.

**~120 Hz is a soft deadline.**
The recovery state machine tolerates jitter and dropouts. There is no hard real-time requirement; late or dropped frames degrade gracefully.

**All filtering is client-side.**
The One Euro filter, reflection rejection, and pose-jump detection live in the Windows Python client (`livelink.py`, `shtp_receiver.py`), not on the Pi. A new pose source gets all of this for free without any client changes.

**Wire format is the boundary.**
The SHTP v2 protocol defines the interface between the Pi and the Windows client. What produces the pose data on the Pi side is a pure implementation detail — the client doesn't know or care.

**Recovery logic is decoupled and tested.**
`recovery.c` is a pure state machine with no I/O. It only needs to receive `EVT_POSE_RECEIVED` or `EVT_POSE_LOST` events. Feeding it from a different pose source requires no changes to recovery logic.

---

## What Makes It Hard

### 1. No plugin interface in the agent

`tracker.c` is a direct wrapper around libsurvive's C API. There is no abstraction layer for alternative pose sources. The libsurvive Simple API, threading model, and callback hooks are called directly from `tracker.c` and `main.c`. Adding pyvut requires either:

- Embedding Python in the C binary (requires GIL management, `PyInit`, thread-safe calling convention)
- Adding an IPC subprocess bridge (Unix socket or named pipe between C agent and Python daemon)

The subprocess bridge is strongly preferred — see §Recommended Approach.

### 2. pyvut is not designed for a Pi

pyvut was developed for Ubuntu desktop. On a Pi the tracker would connect via USB HID — that part works. However:

- **Initial SLAM map construction requires Windows + SteamVR.** There is no Linux-native map builder. See the [USB protocol doc](usb_protocol.md) §9 and the README prerequisites.
- **`adb setprop` may be required for SLAM to start.** Several tracker-side Android properties (`persist.lambda.3rdhost`, `persist.lambda.normalmode`, `persist.lambda.trans_setup`) may need to be set via `adb shell` before the tracker will enter SLAM mode. This is incompatible with a fully headless Pi workflow.
- **WiFi SLAM sync is required for multi-tracker.** Map synchronisation between trackers happens over WiFi, not USB. The Pi would need to act as the WiFi host for multi-tracker setups, which conflicts with its primary role as an Ethernet-connected PoE device.

### 3. Pose freshness detection is libsurvive-specific

Stagehand's recovery state machine gates pose freshness on `lightcap_age_s` — the time since the last optical lighthouse sweep, populated by libsurvive's internal hooks. This guards against the Kalman filter producing stale predicted poses after a lighthouse dropout.

pyvut has no equivalent signal. The closest substitute is `tracking_status` from the pose packet:

| pyvut value | Stagehand equivalent |
|---|---|
| `POSE_OK (2)` | pose fresh → `EVT_POSE_RECEIVED` |
| `POSE_RECENTLY_LOST (4)` | borderline — depends on threshold |
| `POSE_LOST (3)` | pose stale → `EVT_POSE_LOST` |
| `POSE_NOT_INITIALIZED (1)` | → `EVT_POSE_LOST` |
| `POSE_SYSTEM_NOT_READY (-1)` | → initialization timeout |

This mapping is workable but needs validation against real hardware to confirm the Ultimate Tracker's `tracking_status` transitions match Stagehand's timeout assumptions (60s init, 30s pose warn, 300s restart).

### 4. USB device metadata format differs

Stagehand's `DEVICE_INFO` SHTP frames and UI expect:
- A USB port path string (e.g. `"1-1.2"` from sysfs)
- A serial number string (e.g. `"LHR-BF074AD9"`)
- A human-readable device name

pyvut exposes a MAC address as the primary device identifier (`TrackerPose.mac`). Serial numbers are available via ACK queries (`ACK_DEVICE_SN`) but require explicit HID commands on startup. USB port paths are not directly exposed. These fields would need to be mapped or substituted.

### 5. Pi resource constraints

libsurvive already consumes ~40–50% of one Pi 4 core. Running a Python daemon with NumPy and USB HID polling alongside the C agent is viable but requires profiling. HID polling in pyvut is a tight loop by default — a small sleep or event-driven read should be added to reduce CPU load in the daemon.

---

## Recommended Integration Approach

### Unix domain socket subprocess bridge

No changes to the libsurvive integration are needed. The C agent reads poses from a socket; a Python daemon writes them.

```
stagehand-agent (C)                    pyvut-daemon (Python)
        |                                      |
        | /tmp/stagehand-pyvut.sock            |
        |<------ pose JSON frames -------------|
        |  {"pos":[x,y,z],                     |
        |   "rot":[w,x,y,z],                   |
        |   "valid": true,                      |
        |   "serial": "...",                    |
        |   "status": 2}                        |
```

**Why this approach:**
- No Python embedding in the C binary — no GIL, no `PyInit`, no FFI complexity
- pyvut runs in its natural Python environment with full NumPy support
- The health monitor already manages subprocess lifecycle; spawning and restarting `pyvut-daemon` is a small addition to existing logic
- The socket interface is mockable — integration tests can run without real hardware
- If pyvut-daemon crashes, the agent falls back to `EVT_POSE_LOST` until the socket reconnects

**New C components needed:**
- `tracker_socket.c` — non-blocking Unix socket reader, JSON deserializer, pose cache behind mutex
- Minor edits to `main.c` — runtime selection between libsurvive backend and socket backend (flag in `agent.json`)

**New Python components needed:**
- `pyvut_daemon.py` — wraps `UltimateTrackerAPI`, writes pose JSON to the socket at pose callback rate
- Health monitor extension — spawn/restart `pyvut-daemon` alongside `stagehand-agent`

**Estimated scope:** ~300–500 lines of new C, ~100–200 lines of Python, plus tests.

**Client-side changes:** None. SHTP, LiveLink, filtering, and UI are unchanged.

---

## The Blocking Question

**Can the VIVE Ultimate Tracker reuse a stored SLAM map autonomously on subsequent boots, without re-running the Windows setup?**

The pyvut codebase shows that stored maps are supported — map states `MAP_EXIST` and `MAP_REUSE_OK` indicate the tracker can load and reuse a map from its internal storage. If this works reliably:

1. Build the SLAM map once on Windows (SteamVR + VIVE Streaming Hub)
2. Move the tracker to the Pi
3. On every subsequent boot, the tracker loads its stored map and enters tracking without Windows involvement

This would make a fully headless Pi workflow viable. If the tracker requires Windows map reconstruction each session, the integration is only viable as a tethered/hybrid setup (Pi + Windows running simultaneously), which undermines the appliance model.

**This should be tested on real hardware before any integration work begins.**

Secondary questions to validate at the same time:
- Does `adb setprop` need to be run each boot, or do the properties persist across reboots?
- What is the tracker's cold-start time from USB connect to first valid pose in map-reuse mode?
- Does the Ultimate Tracker require SteamVR base stations, or does it perform inside-out SLAM without them?

---

## Coordinate Frame Compatibility

Stagehand streams poses in libsurvive's native coordinate frame. The Windows client applies a conversion to OpenVR frame before sending to Unreal:

```
libsurvive frame: right-handed, +X right, +Y forward, +Z up
OpenVR frame:     right-handed, +X right, +Y up, -Z forward
Unreal frame:     left-handed, +X forward, +Y right, +Z up
```

The Ultimate Tracker's coordinate frame is not documented in pyvut. It would need to be characterised and aligned to libsurvive's frame, or a configurable transform added to the socket bridge. If frames are misaligned, poses will appear rotated or mirrored in Unreal but all other logic will still work correctly.

---

## Single-Tracker vs. Multi-Tracker

| Scenario | Feasibility |
|---|---|
| Single Ultimate Tracker, USB to Pi | Feasible once map question is resolved |
| Multiple Ultimate Trackers via dongle, dongle USB to Pi | Feasible — pyvut's dongle path handles up to 5 trackers; one daemon instance, multiple pose streams |
| Ultimate Tracker + Vive Tracker 2.0 simultaneously | Requires multi-source logic in `main.c`; not currently supported |
| Multiple Pi appliances each running one Ultimate Tracker | Feasible — same as current multi-Pi architecture; no changes needed |

---

## Out of Scope for Initial Integration

- SLAM map transfer between trackers over WiFi (requires the Pi to act as WiFi host)
- OTA firmware updates via pyvut (`ota_parse.py`, `PACKET_SET_FOTA_BY_PC`)
- Face tracking features
- Button event forwarding (pyvut decodes button state; Stagehand has no button channel in SHTP)
- Encoder integration (FIZ Track encoders are USB HID, separate from tracker HID; no change needed)
