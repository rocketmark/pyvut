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

#### 1.3 Precision loss from float16 encoding does not break rotation matrix orthogonality
float16 has 10-bit mantissa precision. After conversion, the resulting rotation matrix must remain orthogonal even under quantization error — rows must be mutually perpendicular and unit length.

```
Property: for M = quaternion_to_rotation_matrix(parse_quaternion(encode_f16(q))),
          M @ M.T ≈ I  (tolerance 0.01)
Strategy: generate unit quaternions near gimbal-lock angles and large rotations,
          encode as float16, decode, build matrix, check M @ M.T - I < tolerance
Note: tests pyvut's conversion pipeline under realistic firmware precision, not just numpy math
```

#### 1.4 Non-trivial quaternion survives encode/decode with bounded error
Quaternions with components near float16 representation boundaries (e.g. ±0.5, ±√2/2) are most susceptible to rounding. The round-trip error must stay within float16 precision limits.

```
Property: ||parse_quaternion(encode_f16(q)) - q|| < 0.002  for normalized q
Strategy: generate quaternions with components drawn from float16 boundary values
          (±2^-n for n in 1..10), verify per-component error against float16 epsilon
Note: replaces trivial identity test — focuses on precision boundaries where bugs appear
```

---

### 2. Pose Packet Parsing
**File:** `tests/test_pose_parsing.py`
**Priority:** HIGH
**Source:** `pyvut/tracker_core.py:809-838`

#### 2.1 Out-of-range positions are rejected or flagged
`parse_pose_data()` unpacks position as float32x3 with no range validation. Garbage firmware values (NaN, Inf, values beyond any plausible play space) must not silently update tracker state.

```
Property: parse_pose_data(packet_with_extreme_position) does not update pose_pos[idx]
          OR sets tracking_status to an error state
Strategy: construct valid pose packets with positions outside ±10m, ±1e6, NaN, and Inf;
          verify tracker state is not silently updated with bad data
Note: tests only the rejection path — generating in-bounds positions and asserting
      they pass is trivially true by construction and is omitted
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
Property: handle_map_state(current, invalid_next) does not advance state
          (state remains `current` and an error is logged or raised)
Strategy: Hypothesis RuleBasedStateMachine — for each state, generate transitions
          outside its valid-successor set and verify state is unchanged after the call
Note: testing that valid transitions are accepted is trivially true by definition
      and is omitted; this test focuses entirely on invalid transition rejection
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

#### 5.3 Incomplete pair does not corrupt prior button state
Receiving only a high-byte packet (0x80 flag set) without a subsequent low-byte packet must leave button state unchanged from its last fully-committed value.

```
Property: buttons_after_high_only(prev_state, high_packet) == prev_state
Strategy: set a known prior button state via a complete pair; send only a high-byte packet;
          verify button state equals the prior committed value, not a partial merge
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

#### 6.2 MACs that differ only outside byte[1] map to the same index
`mac_to_idx` uses only `b[1] & 0xF`. Two MACs identical in byte[1] must yield the same index regardless of other bytes, and two MACs differing in byte[1] nibble must yield different indices.

```
Property: mac_to_idx(mac_a) == mac_to_idx(mac_b)  iff  mac_a[1] & 0xF == mac_b[1] & 0xF
Strategy: generate pairs of MACs sharing byte[1] but differing elsewhere → assert equal;
          generate pairs differing in byte[1] nibble → assert unequal
Note: replaces trivial f(x)==f(x) determinism check with a test of the actual
      byte-field isolation contract
```

---

### 7. CRC-128 (OTA Parser)
**File:** `tests/test_crc.py`
**Priority:** MEDIUM
**Source:** `ota_parse.py`

#### 7.1 CRC matches known reference vectors
Validate `htc_crc128` against independently computed reference outputs, not by calling the same function twice. Reference vectors should be derived from a known-good LFSR implementation or from captured OTA images with published checksums.

```
Property: htc_crc128(segment) == expected_crc  for each reference vector
Strategy: use at minimum three hard-coded (segment, crc) pairs derived from an
          independent LFSR implementation or real OTA captures; parametrize as pytest cases
Note: replaces htc_crc128(x) == compute_crc(x) which is f(x)==f(x) if compute_crc
      is the same function — reference vectors are the only meaningful oracle here
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

#### 8.2 Valid flag is False for all slots before any write
A freshly initialized `SharedPoseBuffer` must report `valid[idx] == False` for every slot before any pose is written. This guards against uninitialized shared memory being read as a live pose.

```
Property: for all idx in 0..4, SharedPoseBuffer().valid[idx] == False
Strategy: instantiate SharedPoseBuffer without any write_pose calls;
          assert valid[idx] is False for all 5 slots;
          then write one slot and assert only that slot flips to True
Note: replaces "may be uninitialized" (unfalsifiable) with a concrete assertion
      about the flag transition on first write
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
