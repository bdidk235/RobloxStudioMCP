---
name: rsx-transport
description: Size ceilings and format traps: the 100,015-char return truncation, the scratch-module limit, dot-free names. Read before moving bulk data.
---

# Transport limits

These fail quietly. A tool call that hits one of them returns a well-formed
reply that is missing data, or an error that names the wrong cause. Every number
here is measured, not documented.

## `return` loses array-ness, and it cannot be put back

**This one is not ours to fix, and knowing that saves a lot of wasted effort.**
`execute_luau` is a *relayed* Studio tool — this project does not implement it,
and `extended_execute_luau_from_file` is a thin pass-through. The array→object
conversion happens inside **Studio's own plugin serialiser**, so no change here
can make it preserve array-ness. If you are looking for the fix, it is upstream.

Measured live:

| Luau returned | arrived as |
|---|---|
| `{10,20,30}` | `{"1":10,"2":20,"3":30}` |
| `{ rows = { {n=1,v=10}, {n=2,v=20} } }` | `{"rows":{"1":{"n":1,"v":10},"2":{"n":2,"v":20}}}` |
| `{ p = Vector2.new(3,4) }` | `{"p":"3, 4"}` |
| `{ json = HttpService:JSONEncode({10,20,30}) }` | `{"json":"[10,20,30]"}` — **intact** |

**Why it cannot be repaired, which is the part that is easy to get wrong:**

- `{[1]=x,[2]=y}` is a genuine Luau **array**, and should have arrived as a list.
- `{["1"]=x,["2"]=y}` is a genuine **string-keyed map**, and is *already correct*
  as `{"1":x,"2":y}`.

Both produce **identical bytes**. So a heuristic that "repairs" the first silently
corrupts the second, and a hard failure fires on the second — including on every
legitimate table that happens to use numeric-looking string keys. There is no
signal on this wire that distinguishes them.

**What to do instead:** serialise at the source. `HttpService:JSONEncode` returns
a *string*, which nothing downstream reinterprets, so it survives intact — that is
the last row of the table above, and it is the whole answer. `rsx-console` covers
the same ground from the console side.

**What the tooling does now:** `extended_execute_luau_from_file` **appends a note**
to the result when it sees an object whose keys are exactly `"1".."n"` with `n
>= 2` and nothing else. It never rewrites the payload, for the ambiguity reason
above. If you see that note, the shape is present; deciding whether it was
meant to be an array is a question only the source can answer.

This came from `REQUEST-luau-return-shapes.md` in this repo, where a 165-row
driver returned `{}` with **no error** - caught only because the next probe
compared a row count and noticed zero. That is the failure mode this project
exists to prevent: a well-formed, confidently-delivered, structurally-wrong
answer.

## The return channel truncates at exactly 100,015 characters

`execute_luau` and `extended_execute_luau_from_file` both return values, and
both truncate the response at **100,015 characters**. Measured by bisecting with
a marker of known length: 50 K returns complete, 100 K arrives truncated with
the tail missing.

This is a hard ceiling, not a soft warning, and nothing in the reply says it
happened. So a large `return` is the single most dangerous thing you can write.

**It cannot be marked, and that is a design constraint rather than an omission.**
There is no length constant anywhere in this codebase: truncation happens on the
return path above this project, so a truncated reply arrives indistinguishable
from a complete one. Do not wait for a marker and do not treat its absence as
evidence the payload was small. `extended_watch_output`'s `truncated` field is
*not* one — that is per-source and `max_lines`, a different channel entirely.
The only defence is to check the length yourself before returning.

| payload | base64 chars | fits? |
|---|---|---|
| 1233x754 RGBA capture | 4,958,304 | no, 50x over |
| 45 KiB of text | ~61 K | yes |
| 100 KiB of text | ~136 K | **no** |

Do not conclude "returns are dropped" from a `nil`. Check the length. A `nil`
result is usually an artifact of the caller, not the engine.

## Bulk data goes through a scratch ModuleScript

When the payload cannot fit in a return, park it in the DataModel and read it
back. `CaptureService` plus `EditableImage:ReadPixelsBuffer` plus
`script_read` is the worked example, and the same shape works for any blob.

Three API details that are easy to get wrong:

- the method is **`ReadPixelsBuffer`**. `ReadPixels` is **not** a member of
  `EditableImage`, and asking for it throws.
- the write is **`ScriptEditorService:UpdateSourceAsync(target, cb)`**.
  `UpdateSourceAsync` is **not** a member of `ModuleScript`.
- **`ReadPixelsBuffer` has no 1024x1024 cap.** 2048x1024, which is 8,388,608
  bytes across 2 M pixels, reads back exactly. Do not port a 1024px
  tile-reassembly loop; one call is correct and simpler.

## The scratch-module ceiling is 6,291,456 bytes

Above that, the write fails with `bad allocation`. A 1233x754 capture is
4,958,304 chars, so one module holds it with about 1.2x headroom.

Check the size *before* writing and refuse the call rather than truncating. A
partial image is worse than an error, because it looks like a valid one.

**Do not slice to fit.** The per-append slice is a speed knob, not a capacity
knob: every append rewrites the whole module, so N appends of a payload P write
about P*N/2 bytes. Splitting never raises the ceiling — an over-ceiling payload
fails at any slice size — so write in one call and let the code pick the slice.
Measured on a 1233x754 viewport: 31 appends of 120,000 wrote 59.5 MB in 3.60 s
against 1 append of 4,958,304 writing 5.0 MB in 1.70 s. `capture.py` sets its
slice to the ceiling for exactly this reason, and `rsx-capture` carries the full
table.

Lua silently drops a leading newline in long-bracket strings, so write with a
guard newline if the payload starts with one.

## Instance names addressed by `script_read` must be dot-free

`script_read` addresses instances by dot-notation path, so a dot in a name
truncates the lookup and reports "Script not found". If you are generating a
scratch instance name, keep it to `[A-Za-z0-9_]`.

## `buffer.tostring` is not base64

It returns a byte-string the same length as the input buffer. For a
3,718,728-byte buffer you get a 3,718,728-character string, not the ~4.96 M
base64 characters you need. Encode base64 explicitly.

`EncodingService:Base64Encode` **accepts a buffer.** Call it:

```lua
local ES = game:GetService("EncodingService")
return ES:Base64Encode(buffer)   -- then buffer.tostring() to move ASCII out
```

Measured on a real 3,718,728-byte frame: **7.5 ms + 5 ms**, against **527 ms**
for a hand-rolled per-3-byte Luau loop on the same input. That is a 70x tax, and
it was paid for a misdiagnosis — an earlier note in this repo said the service
rejected a buffer, and a capture module carried the loop for a whole session
because nothing contradicted it. Byte-for-byte identical at every sampled
offset, so the swap carries no behavioural risk. See `rsx-discovery` for the
reflection caveat: `GetMethodsOfClass("EncodingService")` does **not** list
`Base64Encode`, so this has to be called, not looked up.

## Prefer file-based execution for anything long

`extended_execute_luau_from_file` runs a `.luau` file from disk. It is the right
tool for anything too long to inline, it keeps the code under version control,
and it returns values reliably on both paths. Note that its `execute_luau`
parser rejects `declare` blocks, so `.d.luau` definition files belong in the
typechecker, not the DataModel.

## Writing past a size limit fails, assigning does not

Direct `Script.Source` assignment fails past a few hundred KB, while writes over
about 200 K are chunked. `extended_write_script` handles this; a hand-rolled
`execute_luau` that assigns `Source` directly will fail on a large script and
the error will not mention size.

## What to do when a call returns less than you expect

1. Measure the reply length before parsing it.
2. If it is at or near 100,015, the return channel truncated it. Move the data
   through a scratch instance instead.
3. If a write failed with `bad allocation`, you are over 6,291,456 bytes. Refuse
   the call, or chunk across several scratch instances.
4. If a `script_read` reports "Script not found" for an instance you just
   created, check for a dot in its name.
