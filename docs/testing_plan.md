# pyvut Testing Plan

## Overview

The repository currently has no automated tests — only two manual hardware scripts (`hid_test.py`, `rf_hid_test.py`) that require physical devices. This plan establishes a property-based and unit test suite using [pytest](https://pytest.org) and [Hypothesis](https://hypothesis.readthedocs.io/), covering all hardware-free logic.

All tests should run offline with no HID devices attached.

---

## Test Infrastructure

### Dependencies

```toml
[project.optional-dependencies]
test = [
    "pytest",
    "hypothesis",
    "numpy",
]
```

### Directory Layout

```
tests/
  conftest.py              # shared fixtures, strategies
  test_quaternion.py       # quaternion invariants
  test_pose_parsing.py     # pose packet parsing
  test_ack_parsing.py      # ACK string parsing
  test_state_machine.py    # map state machine transitions
  test_button_state.py     # two-packet button accumulation
  test_mac.py              # MAC address → tracker index
  test_crc.py              # CRC-128 round-trip (ota_parse.py)
  test_shared_memory.py    # SharedPoseBuffer atomicity
```

---

## Test Cases

### 1. Quaternion Invariants
**File:** `tests/test_quaternion.py`
**Priority:** HIGH
**Source:** `pyvut/tracker_core.py` (pose parsing), `scripts/visualize_pygame.py:47-65`

#### 1.1 Unit norm after float16 → float32 conversion
The firmware emits quaternions as float16x4 in `[w, z, y, x]` order, which are converted and reordered to float32 `[w, x, y, z]`. The result must be a unit quaternion.

```
Property: ||parse_quaternion(raw_f16_bytes)|| ≈ 1.0  (tolerance 0.01)
Strategy: generate random unit quaternions, encode as float16, verify norm after decode
```

#### 1.2 Rotation matrix determinant = 1
The quaternion-to-rotation-matrix conversion used in the visualizer must produce proper rotation matrices.

```
Property: det(quaternion_to_rotation_matrix(q)) ≈ 1.0
Strategy: generate unit quaternions, compute matrix, check determinant
```

#### 1.3 Rotation composition closure
Composing two valid rotations must yield a valid rotation.

```
Property: ||q1 * q2|| ≈ 1.0  for any two unit quaternions q1, q2
Strategy: generate pairs of unit quaternions, compose via Hamilton product, verify norm
```

#### 1.4 Identity quaternion round-trip
The identity rotation `[1, 0, 0, 0]` should survive encode/decode unchanged.

```
Property: decode(encode([1, 0, 0, 0])) == [1, 0, 0, 0]
```

---

### 2. Pose Packet Parsing
**File:** `tests/test_pose_parsing.py`
**Priority:** HIGH
**Source:** `pyvut/tracker_core.py:809-838`

#### 2.1 Position bounds
`parse_pose_data()` unpacks position as float32x3 with no range validation. Garbage firmware values must not silently corrupt state.

```
Property: all(-10.0 <= p[i] <= 10.0 for i in 0..2)  for any valid pose packet
Strategy: generate synthetic pose packets with physically plausible positions; separately
          generate packets with extreme values and verify error or skip behavior
```

#### 2.2 Packet size guard
Packets shorter than `0x25` bytes must not cause `struct.unpack` to raise unhandled exceptions.

```
Property: parse_pose_data(packet) raises no unhandled exception for any packet length
Strategy: generate bytes of random length [0, 0x30], verify no crash (error log or skip only)
```

#### 2.3 Tracker index from packet
The tracker index derived from the MAC in a pose packet must always be in `[0, 4]`.

```
Property: 0 <= tracker_index_from_pose(packet) <= 4
Strategy: generate random pose packets with varying MAC bytes, verify index bounds
```

#### 2.4 Timestamp preservation
The parsed timestamp from a valid pose packet must match the raw value in the packet.

```
Property: parse_pose_data(packet).timestamp == expected_ms
Strategy: construct minimal valid packets with known timestamps, verify round-trip
```

#### 2.5 Acceleration magnitude bounds
IMU acceleration (float16x3) must not exceed physical limits after conversion (max ~5g ≈ 50 m/s²).

```
Property: ||acc|| <= 50.0  for valid pose packets
Strategy: generate packets with maximum float16 acceleration values, verify parser behavior
```

---

### 3. ACK String Parsing
**File:** `tests/test_ack_parsing.py`
**Priority:** MEDIUM
**Source:** `pyvut/tracker_core.py:855`

#### 3.1 No crash on malformed ACK
`parse_ack()` uses `.split(":")` and `.split(",")` with no bounds checking. Truncated or corrupted ACKs must not raise unhandled exceptions.

```
Property: parse_ack(data) never raises an unhandled exception
Strategy: generate random byte strings of length [0, 256] including invalid UTF-8
```

#### 3.2 Known-good ACK round-trip
A valid ACK string must be parsed to the correct enum and value.

```
Property: parse_ack(encode(ACK_TYPE, value)) == (ACK_TYPE, value)
Strategy: parametrize over all ACK enum values with representative payloads
```

#### 3.3 ACK prefix uniqueness
No two ACK enum values should share the same string prefix (greedy match ambiguity).

```
Property: for all pairs (a, b) in ACK_ENUMS × ACK_ENUMS where a != b,
          not (a.startswith(b) or b.startswith(a))
Strategy: exhaustive check over enum values (not property-based — deterministic)
```

---

### 4. Map State Machine
**File:** `tests/test_state_machine.py`
**Priority:** HIGH
**Source:** `pyvut/tracker_core.py:753`, `pyvut/enums_horusd_status.py`

#### 4.1 Valid transition whitelist
The 11 map states have implied valid transitions. Invalid firmware-reported transitions must be rejected or logged, not silently applied.

Defined valid transitions:
```
MAP_NOT_CHECKED  → MAP_EXIST | MAP_NOTEXIST
MAP_EXIST        → MAP_EXIST | MAP_REBUILT | MAP_NOTEXIST
MAP_NOTEXIST     → MAP_REBUILT | MAP_CREATE
MAP_REBUILT      → MAP_SAVED_OK | MAP_SAVED_FAIL
MAP_SAVED_OK     → MAP_REUSED_OK | MAP_REBUILD_WAIT
MAP_SAVED_FAIL   → MAP_REBUILD_WAIT
MAP_REUSED_OK    → MAP_EXIST
MAP_REUSED_FAIL  → MAP_REBUILD_WAIT
MAP_REBUILD_WAIT → MAP_REBUILT | MAP_CREATE
MAP_CREATE       → MAP_EXIST
```

```
Property: handle_map_state(current, next) accepts valid transitions,
          rejects or logs invalid ones without corrupting state
Strategy: Hypothesis RuleBasedStateMachine — valid transitions keep invariant,
          random invalid transitions must not silently advance state
```

#### 4.2 State stability under repeated same-state messages
Receiving the same map state twice should be idempotent.

```
Property: handle_map_state(s, s) == s  (no spurious transition)
Strategy: parametrize over all 11 states
```

#### 4.3 `has_host_map` implies `connected_to_host`
A tracker cannot hold a host map without being connected.

```
Property: has_host_map[idx] => connected_to_host[idx]  at all times
Strategy: generate ACK sequences that set/clear these flags, verify invariant after each step
```

---

### 5. Button State Accumulation
**File:** `tests/test_button_state.py`
**Priority:** MEDIUM
**Source:** `pyvut/tracker_core.py:833-838`

#### 5.1 Two-packet accumulation correctness
Button state is built across two consecutive pose packets: high byte (0x80 flag) stores bits 8–15, low byte stores bits 0–7.

```
Property: buttons_after(high_packet, low_packet) == (high_byte << 8) | low_byte
Strategy: generate pairs of packets with known button bytes, verify merged result
```

#### 5.2 No spurious bits after clear
After a button-clear packet, button state must be exactly 0.

```
Property: buttons_after(clear_packet) == 0
Strategy: generate clear packets (all button bits zero), verify state
```

#### 5.3 Single-packet edge case
Receiving only one half of the pair (e.g. only a high byte) must not produce garbage state.

```
Property: partial packet yields defined behavior (either 0 or previous state, not random)
Strategy: send only high packet, verify state is deterministic
```

---

### 6. MAC Address → Tracker Index
**File:** `tests/test_mac.py`
**Priority:** MEDIUM
**Source:** `pyvut/__init__.py` (`mac_to_idx`)

#### 6.1 Index always in [0, 4]
`mac_to_idx(b)` uses `b[1] & 0xF` but never clamps to the valid tracker slot range.

```
Property: 0 <= mac_to_idx(mac) <= 4
Strategy: generate random 6-byte MACs, verify index is in range
Note: this test may expose a bug — the lower nibble of byte[1] ranges 0–15,
      but only slots 0–4 are valid. Behavior for nibbles 5–15 is undefined.
```

#### 6.2 Consistent mapping
Same MAC always maps to same index.

```
Property: mac_to_idx(mac) == mac_to_idx(mac)  (deterministic)
Strategy: generate MACs, call twice, verify equal
```

---

### 7. CRC-128 (OTA Parser)
**File:** `tests/test_crc.py`
**Priority:** MEDIUM
**Source:** `ota_parse.py`

#### 7.1 Round-trip: valid segment passes CRC
A segment with a correctly computed CRC must verify.

```
Property: htc_crc128(segment) == compute_crc(segment)
Strategy: generate random byte segments, compute CRC, verify passes
```

#### 7.2 Corruption detection
Flipping any single bit in a segment must cause CRC verification to fail.

```
Property: htc_crc128(corrupt(segment)) != htc_crc128(segment)
Strategy: generate segments, flip random bit, verify CRC differs
```

#### 7.3 Endianness sensitivity
The CRC function uses little-endian unpacking (`<Q`). Byte-swapped input must yield a different CRC.

```
Property: htc_crc128(segment) != htc_crc128(segment_byte_swapped)
           for most non-palindromic inputs
Strategy: generate non-trivial segments, byte-reverse, verify CRC differs
```

---

### 8. SharedPoseBuffer Atomicity
**File:** `tests/test_shared_memory.py`
**Priority:** LOW
**Source:** `pyvut/api.py:63-76`

#### 8.1 Writer atomicity
A reader process must never observe a partial write — it must see either the old pose or the new pose, never a mix.

```
Property: read_pose(idx) returns a pose where all fields belong to the same write call
Strategy: spawn writer process doing rapid writes; reader checks field consistency;
          run for N iterations, assert no torn reads observed
```

#### 8.2 Valid flag consistency
If `valid[idx]` is False, no pose fields for that slot should be trusted.

```
Property: valid[idx] == False => pose fields may be uninitialized (test documents this contract)
Strategy: read fresh SharedPoseBuffer before any write, verify valid flags are all False
```

---

## Execution

```bash
# Install test dependencies
pip install -e ".[test]"

# Run all tests
pytest tests/

# Run with verbose Hypothesis output
pytest tests/ --hypothesis-show-statistics

# Run only high-priority tests
pytest tests/test_quaternion.py tests/test_pose_parsing.py tests/test_state_machine.py
```

---

## Out of Scope

- Tests requiring physical HID devices (covered by existing `hid_test.py`, `rf_hid_test.py`)
- Wi-Fi socket communication tests
- Firmware update (FOTA/FWU) protocol testing
- Real SLAM map synchronization end-to-end tests
