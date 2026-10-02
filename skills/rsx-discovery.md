---
name: rsx-discovery
description: ReflectionService returns method tables, not strings, so a type check finds nothing. Read before probing for an API.
---

# Discovering what Studio exposes

## Reflection method entries are tables, not strings

`ReflectionService:GetMethodsOfClass(cls)` returns a table of **tables**, each
with `Name`, `Display`, `Owner`, `Parameters` and `Permits`.

```lua
-- WRONG: matches nothing, and you conclude the method does not exist
for _, m in ipairs(RS:GetMethodsOfClass(cls)) do
  if type(m) == "string" and m:find("AddPlayer") then ... end
end

-- RIGHT
for _, m in ipairs(RS:GetMethodsOfClass(cls)) do
  if m.Name:find("AddPlayer") then ... end
end
```

This cost real time here: `StudioTestService:AddPlayers` was reported as
non-existent for several rounds because of exactly that filter.

## Reflection is not the same as runtime

`GetService` resolves things that reflection cannot see. Keep a `pcall` probe as
a fallback rather than trusting one or the other.

- `StudioService` resolves through `GetService` but `GetMethodsOfClass` returns
  **nil** for it, and `GetClass` returns nil.
- `ScriptDebuggerService` is the same.
- `settings()` does not exist under `execute_luau`.
- `StudioTestService` *is* reflection-visible, and has 50 methods.

`GetClasses()` returns about 640 classes; filtering on `Permits.GetService`
gives the service roster.

## An invalid member read throws and discards your output

This is the sharpest edge. Reading a member that does not exist raises, which
aborts the whole `execute_luau` call, so anything already printed is lost.

```lua
-- WRONG: one bad name throws away every result
local res = svc.NotAMember

-- RIGHT
local ok, res = pcall(function() return svc[name] end)
```

Always guard member reads in a loop over names you did not write yourself.

## The useful services, and what they are for

| service | members worth knowing |
|---|---|
| `StudioTestService` | `ExecuteMultiplayerTestAsync`, `AddPlayers`, `EndTest`, `LeaveTest`, `CanLeaveTest`, `GetTestArgs`, `EditModeActive` |
| `ScriptDebuggerService` | `AddBreakpoint`, `RemoveBreakpoint`, `ClearBreakpoints`, `OnStopped` |
| `StudioDeviceSimulatorService` | `GetDeviceListAsync` (45 devices), `SetDeviceAsync`, `SetResolutionAsync`, `SetOrientationAsync`, `GetPixelDensityAsync`, `StopSimulationAsync` |
| `CaptureService` + `AssetService` | `CaptureScreenshot` then `CreateEditableImageAsync` |
| `ScriptEditorService` | `UpdateSourceAsync` for chunked writes |
| `SceneAnalysisService` | rendering, memory, instance composition |

## Prefer the shipped skills to re-deriving this

Roblox ships these through the relayed `skill` tool, and they are far more
complete than anything reconstructed by hand:

- `rbx-debug` - debugger, `OnStopped`, structured variable inspection
- `rbx-device-simulator-lua` - device profiles, orientation, layout
- `rbx-perf-profiling` - MicroProfiler through `LibMP`
- `rbx-scene-analysis` - `SceneAnalysisService`
- `rbx-unit-test` - ModuleScript tests
- `rbx-docs-search` - engine API and creator docs
- `rbx-create-skill` - author your own

Check there before writing a probe. The `rsx-*` skills in this folder cover
only what is specific to this transport, which is where the traps actually are.

## `EncodingService` is real, buffer-native, and invisible to reflection

`EncodingService:Base64Encode(buffer)` works and returns a **buffer of ASCII
base64 bytes**. Measured on a 3,718,728-byte frame: 7.5 ms, against 527 ms for a
hand-rolled per-3-byte Luau loop, byte-for-byte identical. 70x.

`GetMethodsOfClass("EncodingService")` does **not** list it, so reflection cannot
answer the question - it has to be called. This project once carried the opposite
claim ("rejects a buffer") and a hand-rolled loop justified by it.

Pair it with `buffer.tostring`, which is a single C-level copy (5 ms for 4.9 MB).
That is safe *only* because base64 output is pure ASCII, so no byte reaches 0x80
and none is sign-extended. Hand-rolled pixels would not be.

`EncodingService:CompressBuffer(buffer, algorithm)` also works. The second
argument is required and of type `CompressionAlgorithm`, and **only `0` is
accepted** - ints 1-3 and strings both fail with "Unable to cast ... to
CompressionAlgorithm". There is no `Enum.CompressionAlgorithm` reachable by name,
and `Enum.CompressionFormat` / `ZstdCompressionLevel` do not exist in this build.

## RunService needs a colon in raw Luau

`RunService:IsEdit()` and `RunService:IsServer()`. `RunService.IsEdit()` is a
parse error, not a nil call.

## Capabilities cannot be introspected

`getcapabilities` returns nil, and `SecurityCapabilities:GetCapabilities` is
not a valid member. Required capabilities cannot be enumerated, so you cannot
ask what a given call will be permitted to do. Attempt it and handle failure.

## A place that did not open says why, in the log

A Studio that attaches to the mesh but never gets a place name is not a mystery.
The `[telemetryLog]` channel carries the whole open attempt:

```
State: OpenPlaceInitialization -> CreateDataModel -> LoadDataModel
  -> WaitForStreaming -> PostLoadDataModel -> EnterDataModelScope
  -> PreSuccess -> OpenPlaceSuccess
```

and on failure, `State: OpenPlaceFailure` with `ErrorType` and an
`ErrorMessage` on the next lines. Measured over 25 Edit launches: 20 opened,
2 failed "Error fetching latest place version" (**all URI launches**), and 3
never reached a terminal state because sign-in never completed - those spin on
`status:403 Forbidden` and never log `Login got Standalone DM ready`.

**That 2-in-25 did not reproduce.** Re-measured 2026-10-02 over 14 fresh
launches - 6 on `universeId:0`, 8 on the place's real universe id - every one
opened on the first attempt with zero errors. The distribution above is still the
best record of the failure's *shape*, but not its *rate*: read it as "this
happens sometimes", not "roughly 1 launch in 12". In particular, **do not
attribute it to the universe id** - an earlier note here did, and that inference
was withdrawn. See `rsx-targeting`.

So a URI launch with no place name is usually a **failed fetch of the published
place**. Retrying the transport cannot fix that; the launch has to be retried -
which is why `resolve_universe_id` retries three times by default, and why
`extended_manage_instance action=launch` is the layer that should retry rather
than the transport.

**Two traps, both of which gave wrong answers before being caught:**

- The sequence **continues past the terminal state** to `PlaceIdle`. Taking the
  last state reported **0 of 21 successful launches as opened**. Only the
  terminal states decide the outcome.
- **`OpenPlacePreSuccess` ends with the word "Success"** and is a *progress*
  state. A `endswith("Success")` test calls a half-finished load a success, which
  is the worst direction to be wrong in. Spell the terminal set out.

`Hang In Progress` is **not** a hang: it appears in logs that then open their
place normally, including one with 5 instances already running. Only
`Hang Detected` means it escalated, and that string appears in **0 of 47** logs
here - so a startup hang did not happen on this machine at Studio 0.741. Another
implementation (`Superwheat/renium`) documents the opposite for the same Studio
version, reporting that a window opened while another is running hangs on the
sign-in mutex. It did not reproduce here; treat it as possible, not established.

The outcome lines land wherever the load finished, so reading this needs the
**whole** log. The 64 KB prefix that makes identity reads cheap is not enough and
would silently report "no outcome". `Running instance count at launch N` is a
real line in the log and is worth reading: it says how many Studios were already
running when this one started.

## Reflect last, and only for what a lookup cannot answer

Roblox's own `rbx-docs-search` skill — the relayed `skill` tool, no plugin —
documents the API surface, and `rbx-scene-analysis` answers most questions about
what is *in* the tree. Consult those **before** writing a probe.

Reflection is for the residue that neither can cover: whether a method exists in
*this* Studio build, which `GetMethodsOfClass` will not list (it omits
`EncodingService:Base64Encode`, so reflection could not have contradicted the
wrong note that this skill opens with), and what a service returns today.

The habit worth keeping from that failure: reflection gave a confident `no`, and
confidence was the problem. When a lookup and a reflection disagree, believe the
lookup.
