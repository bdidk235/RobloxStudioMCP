---
name: rsx-capture
description: screen_capture vs extended_capture, and the format argument screen_capture silently ignores. Read before measuring pixels.
---

# Capturing the viewport

## Pick by what you are doing

| you want | use | cost |
|---|---|---|
| to see the scene | `screen_capture` | instant, small JPEG inline |
| to measure pixels | `extended_capture` | seconds, megabytes, lossless PNG |

## `screen_capture` is JPEG only, and silently ignores `format`

There is no format parameter. Eight plausible names were tried:

```
format  image_format  output_format  mime_type  type  quality  png  ...
```

Every one returned **byte-identical `image/jpeg` with no error**. They are
silently ignored rather than rejected.

This matters beyond PNG. A tool that ignores an argument is indistinguishable
from one that accepts it, so "no error" is not evidence that an option applied.
Do not conclude a parameter works because the call succeeded.

## The DCT codec smears exactly what rendering work measures

JPEG is fine for a hard edge, measured to localise to 0.002 px. But it smears
1 px high-contrast features, which is precisely what sub-pixel and edge work
turns on.

Two conditions have to hold for JPEG to be usable at that precision, and both
must be checked:

- the ink edges must be **hard steps**, not antialiased ramps.
- the row profile must be built from the **darkest single pixel per row**. A row
  mean dilutes a 1 to 2 px stroke until the crossing never triggers, and a
  darkest-four mean plateaus on thin stems and biases the edge by several pixels.

If either fails, use `extended_capture`.

## `extended_capture` is byte-exact

It reads the framebuffer as raw RGBA and encodes PNG host-side, with nothing
compressed in between. Verified on 1233x754:

- raw 3,718,728 bytes, exactly `w*h*4`
- **one** append through a scratch `ModuleScript` (was 42)
- every PNG chunk CRC valid
- inflated `IDAT` is exactly `h*(w*4+1)`
- **0 mismatches** across the rows compared
- scratch module destroyed afterwards
- **1.6-1.7 s**, down from 3.60 s

The byte-exactness list above and the chunk table below are Python's, recorded
in `extended/capture.py`.

Pixel checks, re-verified after a Studio version bump and again after the base64
change: sky `(149,199,219)`, ground `(86,96,118)`, alpha 255.

Prefer `save_path` over the inline base64 response. The base64 form is large
enough to dominate a context window on its own.

**When the `save_path` write fails, the image is gone.** The call raises
`INVALID_ARGUMENT` with a `CAPTURE_OK_RECOVERY` message (`capture.py:377`): the capture succeeded and the scratch module
was destroyed in a `finally` the moment the base64 was read back, and the RGBA
buffer dies with the call. **Re-capture — do not retry the write**, there is
nothing left to write. Either point `save_path` at a parent directory that exists
and is writable, or omit it and take `png_base64` in the result, which has no
filesystem dependency but rides this transport's return channel: it truncates at
exactly 100,015 characters **with no error**, and a 1233x754 viewport needs about
4,958,304 base64 characters, so that route is silently truncated garbage for any
real capture. It is safe only while `png_bytes` stays under roughly 75,000.

## Two things that made it 2.1x slower than it needed to be

Both were wrong notes that survived because nothing contradicted them.

**1. Hand-rolled base64.** `EncodingService:Base64Encode` accepts a buffer and is
buffer-native. A previous note here said it rejected one, and the code carried a
per-3-byte Luau loop because of it: 527 ms against the service's 7.5 ms for a
3,718,728-byte frame, byte-for-byte identical output. Use the service. See
`rsx-discovery` for why reflection cannot tell you this.

**2. A chunk size that was a speed bug wearing a capacity costume.** Each append
rewrites the whole module, so N appends write about `P*N/2` bytes - and splitting
one module **never raises the 6,291,456 ceiling**, so a payload over it fails at
any chunk size. Appending more than once therefore only ever costs time:| chunk | appends | approx written | elapsed |
|---|---|---|---|
| 120,000 | 31 | 59.5 MB | 3.60 s |
| 1,000,000 | 4 | 10.0 MB | 1.85 s |
| one append | 1 | 5.0 MB | 1.70 s |

So: write it in one call, and if it does not fit, the answer is `downscale`, not a
smaller slice.

**Chunk size is not a caller knob.** The table above is a measurement of
internals: `DEFAULT_CHUNK` and an `opts.chunk` field the Luau
reads, but not one the tool exposes. `extended_capture`'s schema has exactly two
properties, `save_path` and `studio_id`, and unknown arguments are refused before
dispatch rather than ignored. So there is no smaller slice to ask for at the tool
level — do not reach for one, and do not read this table as a parameter to tune.

## Where the remaining 1.6 s goes, so you do not chase the wrong part

Measured in-Luau, 1233x754:

| stage | time |
|---|---|
| waiting for the `CaptureScreenshot` callback | **801 ms** |
| `CreateEditableImageAsync` | 42 ms |
| `ReadPixelsBuffer` (3,718,728 B) | 2 ms |
| `Base64Encode` | 6 ms |
| `buffer.tostring` | 2 ms |
| **in-Luau total** | **855 ms** |

So ~855 ms is the floor with this API and the callback wait is nearly all of it;
the rest is the mesh round trip. The engine-side alternative is
`StudioCaptureService:CaptureScreenshot` with `OutputSize`, which downsamples
in-engine, but its "which DataModel can capture" semantics are **unresolved and
feature-gated** in the current build. Do not adopt it on an unresolved gate, and
do not assume a smaller frame is cheaper here - the callback wait is fixed.

## Saving a relayed screenshot to a file

The agent-facing `screen_capture` returns the image **inline and writes nothing
to disk**. That is the tool wrapper, not a protocol limit: the JSON-RPC response
carries the image, so a programmatic client can persist it. If you are reading
`content` expecting strings you will find nothing, which is why the agent tool
looks empty.

```python
import base64, pathlib
from roblox_studio_mcp import RobloxStudio

studio = await RobloxStudio.connect()          # or command=/args= for the extended server
result = await studio.screen_capture(capture_id="shot1")
item = result.content[0]                        # a dict, NOT a string
blob = base64.b64decode(item["data"])           # {type, data, mimeType}
ext = {"image/jpeg": ".jpg", "image/png": ".png"}[item["mimeType"]]
pathlib.Path("shot" + ext).write_bytes(blob)
```

`content[0]` is a dict with `type`, `data` (base64) and `mimeType`. That detail
is the whole reason this looks broken when it is working.

## A host-side `PrintWindow` capture is 8-35x faster — measured, not built here

**The measurement exists; the code does not.** No `PrintWindow` call is in
`python/src`. The chain up to the PID is built (`logid.resolve`),
but the window tail is not written, so adopting it is new code, not wiring. Every
number and trap below comes from `verify_printwindow.ps1` and
`verify_occlusion.ps1`, not from a shipped capture path.

`CaptureService` costs **1,700 ms** end to end, of which **801 ms is the
`CaptureScreenshot` callback wait** - an engine floor you cannot get under. The
way past it is to not use the engine, and the shape that was measured is:

```python
# PS_RENDERFULLCONTENT, i.e. flag 2. Verified here at 48-205 ms, and it
# captures a window that is behind other windows. This is the call that
# would be made; no tool here makes it.
PrintWindow(hwnd, hdc, 2)
```

`hwnd` comes from `GetWindowThreadProcessId` over `EnumWindows`, filtered to
`RobloxStudioBeta` PIDs - and those PIDs come from `logid.resolve`, so a caller
today has to join the two by hand. Three traps, each of which produces a
*plausible wrong image* rather than an error:

- **Allocate the bitmap `Format24bppRgb`.** `PrintWindow` leaves alpha at 0 on
  composited surfaces and PNG keeps that as full transparency - a completely
  transparent image, no error. Verified: a correct capture comes back
  `colortype=2`; a 32bpp one would be `colortype=6` with alpha 0 everywhere.
- **Call `SetProcessDPIAware` before `GetWindowRect`**, or the rect is
  DPI-virtualised and the bitmap does not match the window.
- **Set `ImageAttributes.WrapMode = TileFlipXY`** on any bicubic resize, or
  sampling bleeds a black border into the edges.

To un-minimize without disturbing the user, `ShowWindow(SW_SHOWNOACTIVATE)` then
`SetWindowPos` to `HWND_BOTTOM` with `SWP_NOACTIVATE`. Do not use a plain
`ShowWindow(SW_RESTORE)`: it takes focus.

Filtering: `DwmGetWindowAttribute(DWMWA_CLOAKED)` drops virtual-desktop windows,
and within one PID the **largest** window is the main window rather than a
floating dock. Prefer the foreground window, else topmost in Z-order.

This is the whole-window image, Studio chrome included, so it is for looking at
rather than for measuring pixels - `extended_capture` stays the exact path.

## The window title is a check on the capture, not a way to find the PID

Studio's title bar carries the **full place path**:

```
C:\Users\User\AppData\Local\Temp\...\Baseplate-35375535.rbxl - Roblox Studio
```

The mesh reports only the **basename**; the log's command line reports the full
path. So there are three records of the same identity.

It is worth having as a **check**, and not as a link. The PID already comes from
the log's own `UIThreadNotifier` line, and the title is derived *from* a PID - so
it cannot sit in the `studio_id` -> PID chain. What it buys is this: once a
PID's HWND is found, check the title names the place you expected before
trusting the pixels. That is precisely the check that catches a mis-targeted
capture, and it costs one string compare.

## `screen_capture` needs a `studio_id`, or it captures the wrong window

**Measured 2026-10-01, three Studios attached: `studio_id` is required.** The
schema refuses the call rather than guessing.

| call | result |
|---|---|
| `{}` | `capture_id: Missing key`, `studio_id: Missing key` |
| `{capture_id}` only | `studio_id: Missing key` |
| `{capture_id, studio_id}` | captures, and echoes the id it used |

So a call that omits `studio_id` cannot reach the wrong window **over MCP** —
schema validation runs first. Two paths still can, and neither is guarded by
the schema:

- a direct `client.call_tool` that skips validation. This repo's own
  `client.py` does no schema validation, so it is a real path here.
- an older Studio build, where the id was optional. The wording below was
  written against one.

**Diagnosing a wrong-window capture, if you get one.** The pixels alone are
expensive to read, because a wrong window looks like a compositing bug. The
tell that it is not: **3D content goes missing too**. A magenta `Part` in
`workspace` is unambiguously 3D, so if that is invisible the answer is the
wrong window, not the 2D layer.

**Settled 2026-10-01: this is not a live hazard, and the evidence is now
stronger than the warning used to be.** Two Studios were each flooded with a
different dominant colour and then captured by `studio_id` — four captures across
both tools, **4 of 4 correct**. Both route to exactly the Studio you name. So the
paragraph above is no longer a claim about this build; it is the *diagnostic* for
a mis-target, which is worth keeping because it is how you would recognise one.

What the warning was originally written from — *"probes ran against one place
while every capture came back as an empty baseplate"* — cannot have been this
mechanism. The likelier explanation is the one worth internalising: **an empty
baseplate looks the same whether it is the wrong place or merely has nothing in
it.** Before concluding a capture went to the wrong Studio, put something visibly
distinct in the viewport. Without that, "wrong place" and "nothing set up yet"
are one picture, and the diagnosis is a guess.

**`extended_capture` reports the `studio_id` it captured**, which
`screen_capture` does only by echoing the required argument. The handler sets
`studio_id` on the result from `studio_id or studio.studio_id` — the *resolved*
id, so it also tells you which Studio an implicit call actually reached
(`extended_server.py:1223`). That is the cheap post-hoc check for everything
above, and it costs nothing: read the field on every capture instead of
re-deriving the target from a name.

Resolve the target by PID, not by name. See `rsx-targeting` for why name
matching collides when two Studios share a place.

## Getting the image to something that can look at it

Three destinations, three different mechanisms. Confusing them is how an agent
spends a capture and then cannot use the result.

| you want the image seen by | do this | measured? |
|---|---|---|
| **you**, as the agent | see below — honestly, this is the open one | partly |
| **a tool inside Studio** | `store_image(filePath=…)` → `IMAGEID_<id>` → pass as `attachedImageUri` | yes, it is in the tool list |
| **the user**, on screen | `extended_capture(save_path=…)` | yes |

### Studio already has an image intake — use it before inventing one

`store_image` loads a **local file** and returns an `IMAGEID_<id>` URI, and
`generate_procedural_model` takes that URI as `attachedImageUri`. So the round
trip "capture to a file, then let Studio consume it" already exists end to end,
and it needs no base64 through any context:

```
extended_capture(save_path="C:/tmp/shot.png")
store_image(filePath="C:/tmp/shot.png")   # -> IMAGEID_<id>
generate_procedural_model(attachedImageUri="IMAGEID_<id>", prompt="…")
```

Note the direction that is **not** supported: `store_image` reads a path, so
there is no way to hand it bytes straight from a capture. Write the file first.

### Can `extended_capture` return an image block instead of base64 text?

**The protocol says yes. Whether it helps *you* is not measured.**

What is established: MCP tool results carry image content blocks -
`{"type": "image", "data": <base64>, "mimeType": "image/png"}` - and the relayed
`screen_capture` already returns exactly that (see the `content[0]` example
above). So `extended_capture` is free to emit one instead of a
`png_base64` **string**, and that is a small, honest change.

What is **not** established, and is the whole question: whether a client harness
renders such a block to the *model* as an image, or flattens it into text —
which would be no better than today's string and would hide the difference behind
a successful call. This has not been tested here, so do not assume it. If you
want to know, test it on a throwaway place.

The asymmetry to keep in mind while deciding: **for the user, a file is strictly
better** — it costs no context, stays on disk to re-examine, and cannot blow a
context window. An inline image block wins only if
the model genuinely gets pixels back. So the useful shape is probably *both*:
`save_path` by default, an image block on request, never base64-as-text.

Note also the context cost that makes the current default unwise on its own: a
1233x754 viewport is roughly 640 KB of PNG, about **850 KB of base64 as text**.
That is not a payload, it is most of a context window.

### Related, from Roblox's side

`rbx-scene-analysis` is the better tool for *deciding what to point a camera at*
— instance composition, adornments, what is actually in the viewport — rather
than hand-rolled Luau to find something to look at. And `rbx-docs-search` is
the authority on `CaptureService` and the `StudioCaptureService:CaptureScreenshot`
gate discussed above; do not settle that from memory.

## ViewportFrame readback is a separate, genuine limit

It exposes neither `ReadPixels` nor `ReadPixelsAsync` in Edit **or** Client, and
`ScreenCaptureService` is not a service name. Readback is plugin-only and this
transport does not expose it.

So pixel measurement goes through `screen_capture` or `extended_capture` and a
file, never through a `ViewportFrame`.

## Never print pixel data

A roughly 5 MB `print` permanently wedged `get_console_output` and
`extended_watch_output` for a Studio instance. See `rsx-console`.
