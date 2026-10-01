/**
 * Reading a Studio process's identity out of its own log file.
 *
 * Ported from `python/src/roblox_studio_mcp/extended/logid.py`, which is the
 * only reliable way to map a `studio_id` to a PID. The obvious join - print a
 * token, then sweep every log for it - turned out to be unnecessary: a Studio log
 * already states its own PID, so `log -> pid` needs no Studio round trip and no
 * console write. Measured over 44 log files: 44 of 44 carried the line, all 44
 * PIDs distinct, none reused across two files.
 *
 * Three groups of field come out of one read, because reading the log twice to get
 * two fields is how the two views drift apart:
 *
 * - `pid` - `FLog::UIThreadNotifier ... for process '16240'`, about a second into
 *   the process's life. This is what replaces the start-time comparison.
 * - `place_path` / `place_id` / `task` / `parent_pid` - the launch command line
 *   is reproduced verbatim in an untimestamped header line, and it names the place
 *   under three different spellings: the Edit task logs `--localPlaceFile`, a
 *   play test logs `-localProjectFile` for both its server and its clients, and a
 *   URI launch logs `roblox-studio:1+task:EditPlace+placeId:N+universeId:M`. The
 *   task tells the role, and `-parentPid` links a play test's client to the server
 *   that started it, so a whole process tree comes out of the logs with no writes.
 * - `session_guid` / `machine_guid` - written once at startup. The session GUID
 *   was distinct in all 45 logs parsed, so it is a per-process value; the machine
 *   GUID was not (2 distinct values), so it is not a dependable host identity and
 *   is not used for joining.
 *
 * What this cannot do
 * -------------------
 * It does not identify a `studio_id`. The mesh names a place and the log records a
 * command line, and those only meet for the file route, where the mesh name is the
 * temp file's basename and the command line contains that basename verbatim.
 *
 * The URI route does not join. Its mesh name is
 * `Template_<placeId>_AutoRecovery_<N>.rbxl` and `N` is a per-launch counter the
 * log never records, so two URI launches of one place are genuinely
 * indistinguishable from log data alone. That case still needs the console token,
 * and {@link ambiguousReason} says so rather than guessing.
 *
 * The one case that is *not* a naming problem is a play test. A `StartServer` and
 * its `StartClient`s report `name: null` because a session member opens no
 * document of its own, so there is no name to match - not a name that is hard to
 * look up. The `-parentPid` on the child's own command line is the edge that
 * reaches them; see {@link resolveUnnamedStudio}.
 *
 * Everything here is host-side. Nothing is written into the DataModel, so none of
 * it can reach the place file, a published place, or a team create.
 *
 * ## On the Python `Dict[str, Any]` this replaces
 *
 * `parse_identity` returned a plain dict, so every key access type-checked and a
 * wrong key failed silently at runtime. That exact bug shipped once already -
 * `extended_watch_output` read a key that did not exist and returned an empty
 * result forever while reporting success. So {@link LogIdentity} here names every
 * field `parseIdentity` can produce, and a misspelled one is a compile error.
 * Where the shape genuinely varies - `liveIdentities` decorates an identity with
 * the log it came from, and a caller may hand back an identity that has none -
 * that variation is modelled with an optional property rather than erased with
 * `Record<string, unknown>`.
 */

import { closeSync, openSync, readFileSync, readSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

import {
  STUDIO_BINARY_MACOS,
  STUDIO_BINARY_WINDOWS,
  basename,
  logDir as platformLogDir,
} from "./platform.js";

// --------------------------------------------------------------------------- //
// Line format recognition
// --------------------------------------------------------------------------- //
//
// Why this exists. The identity fields below are matched on **message text**, not
// on a line prefix, and that is deliberate: the PID line is identified by
// `Constructing UIThreadNotifier for process 'N'` wherever it appears, so it
// survives every prefix change Roblox has ever made. But that robustness has a
// blind spot. When no message matches, {@link parseIdentity} returns `pid: null`,
// and to the caller that is indistinguishable from a Studio that died before
// writing the line. Those are different worlds: one is "this process has no
// identity here", the other is "this log is written in a format I do not read".
// Only one of them is a bug.
//
// So a log's *line format* is recognised separately, and a caller can be told
// which of those two situations they are in. See {@link formatReport} and
// {@link noPidReason}.
//
// What this section changed about the PID, and what it did not
// -----------------------------------------------------------
// The brief this was built from said the gap was the banner regex's `(?!\d{4}-)`
// lookahead assuming type 4, and that an older-format log "would be silently read
// as having no PID". **The lookahead is not the cause and the conclusion does not
// follow from it.** Measured against the pre-change parser: type 1, whose lines
// *do* carry a `[FLog::]` marker and so match the old banner regex's requirements,
// already yielded `pid: 16240` correctly. The formats that actually lost the PID
// were **types 2 and 3**, which carry no channel marker at all - and the old
// `_PID_RE` required a literal `UIThreadNotifier]` before the message, so it could
// not match them.
//
// So the fix is in the PID pattern, not in the banner regex, and the banner regex
// is left exactly as it was. The lookahead's own comment already said it "is not
// doing the job its shape suggests"; measurement agrees, and the correct response
// to a guard that is merely inert is to leave it and document it, not to rewrite
// it on the theory that it was hiding something.
//
// The four shapes, and where the numbering comes from
// -------------------------------------------------
// The numbers are the community convention from the fastlog-viewer's spec
// (`worships/roblox-fastlog-viewer/docs/fastlog.md`). **That document's own author
// flags the naming as his own invention** ("this naming convention isn't
// standard"), and the document disagrees with itself in places: its type-1 heading
// is `UnixTime,Identifier,LogLevel [FLog::...]` while its type-1 *example* is
// `1712972981.05664,7fb4,6 [FLog::Output] ...`, which is three fields, not the two
// the heading names, and matches its type-2 heading. So the numbers are reported as
// that document defines them, and the *shapes* are what this code actually matches
// on.

/** `ISO8601Z , TimeSinceStarted , Thread , ThreadId [, Severity] [Channel] msg` */
export const DATA_FORMAT_TYPE4 = "type4";

/**
 * The only type seen on this machine, and it is not optional: **129,954 of the
 * 130,623** lines across 67 local logs, dominant in all 67.
 *
 * **The severity field is frequently empty, and that is the single most important
 * thing measured here.** 38,462 of those lines - 29.6% of every line in every log
 * measured - are `ISO,0.499384,0edc,6 [FLog::ClientSettings] ...`, with the LogLevel
 * field *absent* rather than named. So the **majority** form is the one the spec
 * never mentions, while the spec's own type-4 example is the minority
 * (`2024-04-13T01:10:24.830Z,0.830709,6c08,6 [FLog::Output]`). A recogniser written
 * from the spec alone would call 30% of real lines unrecognised, and since
 * unrecognised is *loud*, it would then cry wolf on every single log it ever reads
 * - which is the same failure as never crying wolf at all. So the severity field
 * is optional here, and the channel marker is optional too (74 measured lines have
 * a valid type-4 prefix and no channel, because the logger truncated them
 * mid-write).
 *
 * Severity is matched as *any token*, never as a list of names. Measured words:
 * Info 71,688, Warning 14,283, Error 2,716, Debug 1,662, Verbose 393, plus
 * `Critical` on 74 truncated lines. A closed list would reject the seventh word
 * the day it ships.
 */

/** `UnixTime , Thread , ThreadId [, Severity] [Channel] msg` */
export const DATA_FORMAT_TYPE1 = "type1";

/**
 * **Zero** occurrences across 67 logs and 130,623 lines, so the shape here is
 * taken from the spec's example, not from a local measurement. Kept because it is
 * the format an *older* Studio would write, and the whole point of this section is
 * that an older-format log must not be silently read as "no PID".
 */

/**
 * `UnixTime , Thread , ThreadId [, Severity] msg` - the same fields as type 1 with
 * **no** `[FLog::]` channel marker, which is what separates the two in the spec
 * (`1712859371.37087,7b3c,6 Initializing new game`). So the channel marker, not the
 * timestamp, is what tells type 1 from type 2 - see {@link classifyLine}. Zero
 * occurrences measured here, for the same reason as {@link DATA_FORMAT_TYPE1}.
 */
export const DATA_FORMAT_TYPE2 = "type2";

/**
 * `TimeSinceStarted Thread: msg` - `0.01454 7dbc: FetchClientSettingsDataBlocking
 * ...`. Space-separated rather than comma-separated, with a **named** thread
 * instead of an id. Zero occurrences measured here.
 */
export const DATA_FORMAT_TYPE3 = "type3";

/**
 * The untimestamped header block, deliberately **not** treated as a format
 * failure. 197 measured: one Terms-of-Use notice per log (67, always line 0), and a
 * `*******` / `Command line:` block on 65. The notice has record intent but no
 * prefix; the banner has neither. Both are near-constant across the corpus, which
 * is what justifies naming them rather than reporting them as unknown - see
 * {@link PREAMBLE_TERMS_RE} for why position is not used.
 */
export const DATA_PREAMBLE = "preamble";

/**
 * A line that is not a record at all: blank lines, and messages that ran onto
 * more lines - Luau stack frames (`Script 'Foo', Line 65 - function x`), JSON
 * bodies, the `---- Error caught by React ----` banners. 471 measured once blank
 * lines are included, and every one is understood: these are not a format this
 * code fails to read, they are the *absence* of a record. The distinction matters,
 * because the whole point of {@link DATA_UNRECOGNISED} is that it means something -
 * folding "not a record" into it would make it mean "a log line", which is a
 * category of nearly everything.
 */
export const DATA_CONTINUATION = "continuation";

/**
 * Record intent, no recognised shape. **This is the loud one**, and it is the whole
 * reason the other six classifications exist as separate answers.
 *
 * A line lands here only if it *looks like* it was trying to be a record - it opens
 * with a timestamp or a `[Channel]` tag - and then failed every known prefix shape.
 * A caller finding one is looking at a format not in the table above, and cannot
 * assume any prefix-keyed field it got was read correctly.
 *
 * Measured: **1 line**, in `RobloxStudioInstaller_0EA05.log`, and it is a torn
 * write - a bare `2026-09-29T21:56:01.194Z` with no record attached. Zero across
 * all 66 Studio logs. That is the outcome that makes this worth having: one genuine
 * finding in 130,623 lines, and it is the installer log, which the project's own
 * notes already flag as "not a Studio".
 */
export const DATA_UNRECOGNISED = "unrecognised";

/** Every classification {@link classifyLine} can return. */
export const DATA_FORMATS: readonly LineFormat[] = Object.freeze([
  DATA_FORMAT_TYPE1,
  DATA_FORMAT_TYPE2,
  DATA_FORMAT_TYPE3,
  DATA_FORMAT_TYPE4,
  DATA_PREAMBLE,
  DATA_CONTINUATION,
  DATA_UNRECOGNISED,
]);

/**
 * The four *record* formats, as opposed to the header/continuation/unknown
 * classifications, which are not formats of a record.
 *
 * Ordered, and the order is load-bearing: a tie in {@link formatReport}'s `dominant`
 * resolves to the earliest entry here, because Python's `max` over a sorted
 * sequence returns the first maximal element.
 */
export const DATA_RECORD_FORMATS: readonly RecordFormat[] = Object.freeze([
  DATA_FORMAT_TYPE1,
  DATA_FORMAT_TYPE2,
  DATA_FORMAT_TYPE3,
  DATA_FORMAT_TYPE4,
]);

/** One of the four record formats. */
export type RecordFormat =
  | typeof DATA_FORMAT_TYPE1
  | typeof DATA_FORMAT_TYPE2
  | typeof DATA_FORMAT_TYPE3
  | typeof DATA_FORMAT_TYPE4;

/** Any classification {@link classifyLine} can return. */
export type LineFormat = RecordFormat | typeof DATA_PREAMBLE | typeof DATA_CONTINUATION | typeof DATA_UNRECOGNISED;

/**
 * The two timestamp shapes, as *shapes*. These are shape tests, not arithmetic: no
 * value is converted, compared or subtracted, because the timestamp never decides
 * anything in this module. What decides the PID is the message text ({@link PID_RE}),
 * and what decides the format is the channel marker and the punctuation of the
 * prefix. Type 4 is ISO; types 1 and 2 are a Unix epoch in seconds. The epoch is
 * bounded at 9-11 digits so that `0.830208` - which is the *TimeSinceStarted* of a
 * type-4 line, and the whole of a type-3 line - cannot be mistaken for one.
 */
const ISO_TIMESTAMP = String.raw`\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z`;
const EPOCH_TIMESTAMP = String.raw`\d{9,11}\.\d+`;

/**
 * One `,field` of the FLog prefix. Deliberately any non-comma, non-space run: the
 * fields observed are a float, a hex thread id, a small integer, and a severity
 * word, and the one thing all four have in common is that none of them contains a
 * comma. Spelling them out separately would encode today's field meanings into the
 * recogniser, and the meaning of field 3 is already ambiguous in the spec (its
 * type-4 example puts a thread number in the slot the prose calls LogLevel).
 */
const FIELD = String.raw`[^,\s]+`;

/**
 * `ISO,f,f,f[,f][ Channel]` and `Epoch,f,f[,f][ Channel]`.
 *
 * The field counts differ and that is load-bearing, so the two are spelled out
 * rather than shared: type 4 carries `TimeSinceStarted,Thread,ThreadId` (three
 * fields) where the spec's type 1/2 example carries `Thread,ThreadId` (two).
 * Writing one pattern for both and varying only the timestamp is what this module
 * first did, and it mis-read 91,185 of the 129,901 real lines on this machine as
 * unrecognised - every type-4 line carrying a severity word, because the extra
 * field was left unmatched. That is the worst way for this code to be wrong: a
 * recogniser that miscounts fields does not fail loudly on the lines it gets wrong,
 * it fails loudly on *everything*, which trains a caller to ignore the one signal
 * this section exists to raise.
 *
 * The channel tag is `[^\]]*` rather than a `Name::SubName` pattern because of
 * `[LOGCHANNELS + 1]`, measured on 2 lines, which contains a space and a `+`. It is
 * optional because 74 measured lines are records the logger truncated mid-write,
 * losing the channel and, on 57 of them, the whole message.
 *
 * The channel marker is **captured rather than discarded**, under the name
 * `channel`, because the one thing that separates type 1 from type 2 is whether it
 * is there. An earlier version compiled it as a non-capturing `(?:...)?` and then
 * went looking for the channel with a separate search anchored at the start of the
 * line - which never matches, because the channel sits *after* the prefix fields.
 * Every type-1 line was therefore classified as type 2, which is a wrong answer
 * rather than a missing one.
 *
 * Named groups rather than positional ones, because the two patterns above are
 * interleaved with `_DM_ID_RE` and positional indexing is how a two-group pattern
 * quietly becomes a one-group lookup.
 */
const CHANNEL = String.raw`(?<channel>\[[^\]]*\])?`;

const TYPE4_RE = new RegExp(
  `^${ISO_TIMESTAMP},${FIELD},${FIELD},${FIELD}(?:,${FIELD})?\\s+${CHANNEL}`,
);
const TYPE12_RE = new RegExp(`^${EPOCH_TIMESTAMP},${FIELD},${FIELD}(?:,${FIELD})?\\s+${CHANNEL}`);
const TYPE3_RE = /^\d+\.\d+\s+\S+:/;

/**
 * A leading channel marker with no prefix at all. Present on 67 lines - one per
 * log - as the Terms of Use notice, and it is what makes "record intent" a
 * decidable question for a line with no timestamp.
 */
const LEADING_CHANNEL_RE = /^\[[^\]]*\]/;

/**
 * The three header shapes, matched on **content**, never on position. Their
 * position is not stable: the Terms of Use notice is line 0 of every log measured,
 * but the `*******` / `Command line:` / executable-path block lands a few lines
 * later, and whether that is before or after the first type-4 record varies between
 * files. A rule keyed on "before the first record" would therefore classify the
 * same bytes differently in two logs from the same process.
 *
 * Hard-coding these as *known* is what keeps the unrecognised signal usable: they
 * appear on 67 of 67 files, so if they counted as unknown then every log would
 * report a format problem and a caller would learn to ignore the one signal this
 * section exists to raise.
 */
const PREAMBLE_TERMS_RE = /^\[FLog::Output\] All use of Roblox services/;
const PREAMBLE_STARS_RE = /^\*+$/;
const PREAMBLE_COMMAND_RE = /^Command line:\s*$/;

/**
 * Splits out so {@link classifyLine} can ask "does this line *look* timestamped"
 * without also accepting it as a well-formed record. Wider than
 * {@link ISO_TIMESTAMP} on purpose: a torn write can leave half a timestamp
 * (`2026-09-29T21:56` in the installer log), and that is precisely the case worth
 * reporting rather than swallowing.
 */
const ISO_TIMESTAMP_LOOSE_RE = /^\d{4}-\d{2}-\d{2}T/;
const EPOCH_LOOSE_RE = /^\d{9,11}\./;

/**
 * Python's `str.splitlines()`, which JavaScript has no equivalent of.
 *
 * `text.split("\n")` is **not** a substitute, and the difference shows up in the
 * census. Python splits on `\n \r \r\n \v \f \x1c \x1d \x1e \x85 \u2028 \u2029` and
 * does not emit a trailing empty for a trailing break; `split("\n")` splits on `\n`
 * only and *always* leaves a trailing empty string. So a five-line log would count
 * as six, and a CRLF log would count `\r` as a character inside the line rather than
 * as a break. The census is a loud signal, so a census that is off by one per file
 * is a census nobody trusts.
 */
const LINE_BREAK_RE = /[\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029]/;

function splitLines(text: string): string[] {
  if (text === "") return [];
  const out: string[] = [];
  let start = 0;
  for (let i = 0; i < text.length; i += 1) {
    if (!LINE_BREAK_RE.test(text[i] as string)) continue;
    out.push(text.slice(start, i));
    // \r\n is one break, not two.
    if (text[i] === "\r" && text[i + 1] === "\n") i += 1;
    start = i + 1;
  }
  if (start < text.length) out.push(text.slice(start));
  return out;
}

/**
 * Classify one FLog log line into one of {@link DATA_FORMATS}.
 *
 * Type 1 and type 2 differ **only** in whether the `[FLog::]` marker is present,
 * because that is the sole difference between them in the spec. So the channel
 * marker, not the timestamp, separates them - which is why an epoch line with a
 * channel is type 1 and the same line without is type 2.
 *
 * A record is recognised on its **prefix shape and channel marker**, never on the
 * value of its timestamp. No arithmetic is done on any timestamp anywhere in this
 * module, because the field this whole file exists to produce is found by its
 * message text, and a timestamp-keyed rule would be a second, weaker,
 * silently-failing copy of the same lookup.
 *
 * Position plays no part: the header block is recognised by its content, not by
 * "before the first record", because the `Command line:` block lands before the
 * first record in some logs and after it in others, and a position-keyed rule would
 * classify identical bytes differently in two files.
 */
export function classifyLine(line: string): LineFormat {
  const stripped = line.replace(/[\r\n]+$/, "");
  if (stripped.trim() === "") return DATA_CONTINUATION;

  // Ordered cheapest-and-most-common first, because this runs once per line of
  // every log a census touches: 129,954 of the 130,623 lines measured take the
  // first or second branch below.
  if (TYPE4_RE.test(stripped)) return DATA_FORMAT_TYPE4;

  const epoch = TYPE12_RE.exec(stripped);
  if (epoch) {
    // The captured channel is the only difference between type 1 and type 2.
    return epoch.groups?.["channel"] ? DATA_FORMAT_TYPE1 : DATA_FORMAT_TYPE2;
  }

  if (TYPE3_RE.test(stripped)) return DATA_FORMAT_TYPE3;

  // The three header shapes, matched on content. Two patterns, three shapes: the
  // terms notice and the asterisks are separate, because both are fixed prefixes
  // of a line the engine wrote verbatim at startup and each is clearer alone.
  if (PREAMBLE_TERMS_RE.test(stripped) || PREAMBLE_STARS_RE.test(stripped)) {
    return DATA_PREAMBLE;
  }
  if (PREAMBLE_COMMAND_RE.test(stripped)) return DATA_PREAMBLE;

  // Record intent: a timestamp-shaped start with fields that do not form any known
  // prefix, or a channel marker with no prefix at all. Both mean the log is written
  // in a shape not in the table, which is the one thing here that must never be
  // silent.
  if (
    LEADING_CHANNEL_RE.test(stripped) ||
    ISO_TIMESTAMP_LOOSE_RE.test(stripped) ||
    EPOCH_LOOSE_RE.test(stripped)
  ) {
    return DATA_UNRECOGNISED;
  }

  return DATA_CONTINUATION;
}

function emptyCounts(): Record<LineFormat, number> {
  return {
    [DATA_FORMAT_TYPE1]: 0,
    [DATA_FORMAT_TYPE2]: 0,
    [DATA_FORMAT_TYPE3]: 0,
    [DATA_FORMAT_TYPE4]: 0,
    [DATA_PREAMBLE]: 0,
    [DATA_CONTINUATION]: 0,
    [DATA_UNRECOGNISED]: 0,
  };
}

/** A census of every line in a log, by format, plus the loud verdict. */
export interface FormatReport {
  /** One count per {@link DATA_FORMATS} member, always present even at zero. */
  counts: Record<LineFormat, number>;
  /** The record format that appears most, or null if the log holds no records. */
  dominant: RecordFormat | null;
  /** How many lines had record intent and no recognised shape. */
  unrecognised: number;
  /** A short sample of those lines, so the report says *which* bytes. */
  unrecognised_lines: string[];
}

/**
 * Census of every line in a log, by format, plus the loud verdict.
 *
 * The distinction this exists for: `unrecognised === 0` means **"every line in this
 * log was read as a format we know"**, and `unrecognised > 0` means **"part of this
 * log is in a format we do not understand"**. Read alongside `pid === null` from
 * {@link parseIdentity}, that separates the two situations a caller cannot
 * otherwise tell apart - a Studio that died before writing its PID line, and a
 * parser that cannot read the log in front of it. They call for opposite responses:
 * the first is a dead process, the second needs the parser extended. Reporting both
 * as `pid: null` is the silent-wrong-answer shape this project keeps running into.
 *
 * Whole text, not a prefix: a format problem is not guaranteed to be at the top of
 * the file, and a census of a prefix would certify a log it never read the rest of.
 * Cheap enough - measured 0.130 ms per 256 KB, the same figure {@link PREFIX_BYTES} is
 * sized on.
 */
export function formatReport(text: string): FormatReport {
  const counts = emptyCounts();
  const unrecognisedLines: string[] = [];

  for (const line of splitLines(text)) {
    const kind = classifyLine(line);
    counts[kind] += 1;
    if (kind === DATA_UNRECOGNISED && unrecognisedLines.length < 5 && line.trim() !== "") {
      unrecognisedLines.push(line.slice(0, 200));
    }
  }

  let dominant: RecordFormat | null = null;
  let best = 0;
  for (const name of DATA_RECORD_FORMATS) {
    const count = counts[name];
    // Strictly greater, so a tie keeps the earlier format - which is what
    // `max(sorted(...), key=...)` does in Python.
    if (count > best) {
      best = count;
      dominant = name;
    }
  }

  return {
    counts,
    dominant,
    unrecognised: counts[DATA_UNRECOGNISED],
    unrecognised_lines: unrecognisedLines,
  };
}

/**
 * The PID, as the log states it. Anchored on `process '` with digits only, so a
 * longer process name cannot be truncated into a plausible-looking PID.
 *
 * **The channel marker is deliberately absent from this pattern, and that is a fix
 * rather than a loosening.** It used to require a literal `UIThreadNotifier]`
 * ahead of the message, on the assumption that every FLog line carries an
 * `[FLog::...]` tag. That is true of type 4 and false of **types 2 and 3**, which
 * carry no channel marker at all (`0.01454 7dbc: Constructing UIThreadNotifier for
 * process '16240'`). So a type-2 or type-3 log - precisely the older formats this
 * change exists to survive - was read as having **no PID**, with no error, and the
 * process dropped from the join. Measured against the pre-change pattern: type 1
 * found 16240, type 2 and type 3 both found `null`.
 *
 * The remaining guards are the verb, the class name and the quoted digits, which
 * together are specific enough: re-run over all 67 logs on this machine, the
 * loosened pattern yields the **same 64 PIDs** as the anchored one, with no false
 * positive. The line that must never match is the teardown half,
 * `Destructing UIThreadNotifier for process 'N'`, and it cannot: `Destructing` does
 * not contain `Constructing`.
 */
const PID_RE = /Constructing UIThreadNotifier for process '(\d+)'/;

const SESSION_GUID_RE = /Session GUID is ([0-9A-Fa-f-]{36})/;
const MACHINE_GUID_RE = /Machine GUID is ([0-9A-Fa-f-]{36})/;

/**
 * A Studio banner line: untimestamped, ending in the Studio binary.
 *
 * The binary name is alternated rather than suffixed so both spellings are
 * first-class, and it is built from `platform.ts`'s constants so the `.exe` suffix
 * cannot be hard-coded in two places and drift. It used to be suffixed, and a Mac
 * banner then matched nothing - which surfaces as **"no identity", with no error**,
 * the exact failure shape this project has been repeatedly bitten by.
 *
 * The negative lookahead for an ISO date only excludes type-4 FLog lines; a type-1
 * line (`1712972981.05664,...`) is *tested* and rejected on the binary-name
 * requirement instead. Harmless, but the guard is not doing the job its shape
 * suggests, so it is documented rather than trusted.
 *
 * `m` but **not** `g`: this is searched repeatedly, and a sticky `lastIndex` from a
 * `g` flag would make the second call on the same text return nothing. Ported
 * identically from `platform.BANNER_RE`.
 */
export const BANNER_RE = new RegExp(
  `^(?!\\d{4}-).*?(?:${STUDIO_BINARY_WINDOWS}|${STUDIO_BINARY_MACOS})(?:\\s+(.*\\S))?\\s*$`,
  "m",
);

/**
 * Place identity, per launch route. Three spellings, all measured:
 *
 * - `--localPlaceFile` the Edit task, this module's file route
 * - `-localProjectFile` StartServer and StartClient, i.e. a play test
 * - `roblox-studio:...` the URI route, with the ids inline
 *
 * Missing the middle one is why a play test's server and clients looked
 * unidentifiable, and the task it names is also how the role is known.
 */
export const PLACE_PATH_FLAGS: readonly string[] = Object.freeze([
  "--localPlaceFile",
  "-localProjectFile",
]);

/**
 * A following `-flag` token, used to find where a path argument ends. `/` and `\`
 * are deliberately absent from the class: a Windows path may well start `-` after a
 * drive letter is stripped, and a path is far more likely to contain a slash than to
 * begin with a dash.
 */
const NEXT_FLAG_RE = /\s-{1,2}[A-Za-z]/;

/**
 * The path argument following `flag`, spaces and all.
 *
 * **This function exists because a `(\S+)` capture truncates at the first space.**
 * These two used to be `(--localPlaceFile|-localProjectFile)\s+(\S+)`, and that is
 * wrong on macOS. The macOS AutoSaves directory Roblox documents is
 * `~/Library/Application Support/Roblox/RobloxStudio/AutoSaves` - the space in
 * `Application Support` is not incidental, it is a fixed component of the path.
 * `(\S+)` stopped at it, so a Mac launch parsed as
 * `place_path='/Users/me/Library/Application'`, a path that exists nowhere. The
 * failure was silent: a truncated path still produced a basename, so the name-based
 * join went looking for a document called `Application` and simply never matched.
 * Found by running the parser against a macOS-shaped banner; Windows cannot produce
 * this input.
 *
 * Three rules, in order, because a command line gives no single answer:
 *
 * 1. **Quoted** - take everything up to the closing quote. Unambiguous.
 * 2. **Another flag follows** - cut at that flag. Safe, because a real path does not
 *    contain ` -word`.
 * 3. **Nothing follows** - take the whole remainder. This is the macOS case: the
 *    path is last on the line and contains a space, so rule 1 does not apply and
 *    rule 2 must not fire on a space that is *inside* the value.
 */
function pathAfter(args: string, flag: string): string | null {
  const index = args.indexOf(flag);
  if (index < 0) return null;
  // Python's str.lstrip() with no argument strips all leading whitespace.
  const rest = args.slice(index + flag.length).replace(/^\s+/, "");
  if (rest === "") return null;
  if (rest.startsWith('"')) {
    const closing = rest.indexOf('"', 1);
    if (closing > 0) return rest.slice(1, closing);
    return rest.slice(1);
  }
  const following = NEXT_FLAG_RE.exec(rest);
  return following ? rest.slice(0, following.index) : rest;
}

/**
 * The URI route, with the universe id that actually fetches. `universeId:0` is what
 * File > New does and what this project sends; the id is still required as a key,
 * because dropping it left the Studio with no place open.
 */
const URI_PLACE_RE = /\+placeId:(\d+)\+universeId:(\d+)/;

/**
 * The place as **separate flags**, which is what a child Studio launched by
 * File > New gets:
 *
 * ```
 * -task EditPlace -universeId 0 -placeId 95206881 -userid 1183256136 \
 *   -parentPid 9352 -parentSessionGuid 1B7B85EC-...
 * ```
 *
 * Missing this form reports `place_id: null` for a process that plainly has a place,
 * because {@link URI_PLACE_RE} only matches the inline `+placeId:N` shape. The
 * `-parentPid` in the same line is the cleanest identity edge available: it names
 * the launching Studio's process outright, so a child needs no name matching at all.
 */
const FLAG_PLACE_RE = /-placeId\s+(\d+)/;
const FLAG_UNIVERSE_RE = /-universeId\s+(\d+)/;
const FLAG_PARENT_GUID_RE = /-parentSessionGuid\s+([0-9A-Fa-f-]{36})/;
const TASK_RE = /-{1,2}task\s+(\S+)/;
const URI_TASK_RE = /\+task:(\S+?)\+/;
const INTENT_TASK_RE = /-launchIntentString\s+\{.*?"task"\s*:\s*"(\w+)"/;
const PARENT_PID_RE = /-parentPid\s+(\d+)/;
const TRANSPORT_RE = /-rbxTransportToken\s+(\S+)/;

/**
 * The mesh's name for a URI launch. `N` is a per-launch counter, present in the
 * mesh and absent from the log, which is exactly why that route cannot join.
 *
 * No `g` flag, because it is anchored at both ends and is used with `exec` on
 * candidate names, where a sticky `lastIndex` would be a trap rather than a
 * speed-up. Python's `$` also matches before a trailing newline and JavaScript's
 * does not; the difference cannot arise for a mesh name, which has no newline in
 * it.
 */
const MESH_URI_NAME_RE = /^Template_(\d+)_AutoRecovery_(\d+)\.rbxl$/;

/** Filename stamp, e.g. `0.741.19.7411056_20260930T091820Z_Studio_D4ED5_last.log`. */
const LOG_STAMP_RE = /_(\d{8}T\d{6})Z_/;

/**
 * `[telemetryLog] PlaceSessionId: <guid>-<suffix>` where the suffix is either the
 * numeric place id or, when Studio opened an autorecovery copy, the full path to it.
 * Two shapes, one line, and the shape is the whole point:
 *
 * - `PlaceSessionId: A3A73B66-0B94-4C5E-AA26-5F44551274BC-95206881`
 * - `PlaceSessionId: 77C06B69-0B74-4C0B-B082-39E5D0EC16BB-C:/Users/.../AutoSaves\Template_95206881_AutoRecovery_3.rbxl`
 *
 * **The second form is the only record of a URI launch's `AutoRecovery_N` counter**,
 * which is the one thing that separates two concurrent URI launches of the same
 * place - the case this project recorded as unresolvable. It appears only in the 1
 * of 16 URI logs where Studio created an autorecovery document at all; the rest
 * opened a plain unsaved "Place1" and have no path to report.
 *
 * The GUID itself is per place-open and distinct in 13 of 13 logs that opened a
 * place, but the mesh never reports it, so it cannot close the join on its own.
 */
const GUID_SOURCE =
  "[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}";
const PLACE_SESSION_RE = new RegExp(
  String.raw`PlaceSessionId:\s*(?<guid>${GUID_SOURCE})-(?<tail>.*)$`,
  "gm",
);
// `g` because `sessionGuids` iterates *every* DmId, the way Python's `finditer`
// does. Without it the first `matchAll` call throws outright - which is a
// crash, not a wrong answer, so it cannot go unnoticed the way a missed match
// would.
const DM_ID_RE = new RegExp(String.raw`DmId:\s*(?<guid>${GUID_SOURCE})-`, "g");

/**
 * Every identity field one log can carry.
 *
 * This interface is the point of the port. The Python side returned
 * `Dict[str, Any]`, so `identity["place_paths"]` type-checked and evaluated to
 * `None` at runtime - and that exact shape of bug shipped once already, in
 * `extended_watch_output`, which read a key that did not exist and returned an empty
 * result forever while reporting success.
 *
 * Three deliberate choices:
 *
 * - **Absent is `null`, never a missing key.** "The log does not say" and "not
 *   parsed yet" must not look the same, and `undefined` for a field the parser
 *   always sets would be the second shape of the same bug.
 * - **`log` and `started` are optional, not `| null`.** They are genuinely not part
 *   of what the *parse* produces: `readIdentity` adds `log` from the filename, and
 *   {@link liveIdentities} adds `started` from it too. A caller can also hand back
 *   an identity it built itself, as the resolver tests do, and there is no log to
 *   name. `parseIdentity` returns an object with those keys absent, and the type says
 *   so.
 * - **`unrecognised_lines` is a *count*, not lines.** Ported under that name for
 *   key parity with the Python dict, which is read by key elsewhere. The *samples*
 *   live on {@link FormatReport.unrecognised_lines}, which is a `string[]`. This is a
 *   Python naming wart rather than a design, and it is preserved deliberately: two
 *   fields of the same name meaning different things is worth reporting, not worth
 *   breaking parity over.
 */
export interface LogIdentity {
  /** From `Constructing UIThreadNotifier for process 'N'`, or null. */
  pid: number | null;
  /** The opened file's full path, `--localPlaceFile` / `-localProjectFile` form. */
  place_path: string | null;
  /** A published place id, from the URI route or a separate `-placeId` flag. */
  place_id: number | null;
  /** The universe id that fetches. Required as a URI key even when it is 0. */
  universe_id: number | null;
  /** `EditFile`, `EditPlace`, `StartServer`, `StartClient`, `None`. */
  task: string | null;
  /** `-parentPid`, the launching process. `null`, never 0: 0 is a real pid shape. */
  parent_pid: number | null;
  /**
   * `-parentSessionGuid`, **uppercased at parse**.
   *
   * The inconsistency with {@link session_guid}, which is *not* uppercased, is
   * load-bearing: every comparison between the two is therefore case-insensitive.
   * Do not "fix" one without fixing the other, or a log that spelled its own GUID in
   * lower case starts disagreeing with a correct edge.
   */
  parent_session_guid: string | null;
  /** `-rbxTransportToken`, the base64 blob a play-test client is handed. */
  transport_token: string | null;
  /** From `Session GUID is <guid>`, verbatim - not case-normalised. */
  session_guid: string | null;
  /**
   * From `Machine GUID is <guid>`. Measured 2 distinct values across 45 logs, so it
   * is **not** a per-process value and must not be used to join.
   */
  machine_guid: string | null;
  /**
   * The dominant record format, populated **only when `pid` is null**.
   *
   * `null` here means "not checked, and not needed", deliberately distinct from a
   * format string meaning "checked, and the log is type 4". A log whose PID line
   * parsed is one this module demonstrably *can* read, so the census has nothing
   * left to explain, and the cost is not hypothetical: measured over the local
   * corpus, {@link formatReport} costs **2.210 ms** per 256 KB against **0.004 ms**
   * for the PID search it would accompany - **622x**, rising to **3504x** at 1 MB,
   * because the census is per-line and the search is one pass. A whole-file sweep of
   * 67 logs (17.7 MB) costs **144 ms** of census, which is fine once and ruinous on
   * every pass of a sweep that walks thousands of files. Collapsing "not checked"
   * into "checked and clean" is this file's favourite failure mode wearing a
   * different hat.
   */
  log_format: RecordFormat | null;
  /**
   * How many lines had record intent and no recognised shape. Populated only when
   * `pid` is null, for the same reason and the same cost. `0` means "checked, and
   * every line was recognised"; `null` means "not checked".
   *
   * A count, despite the name - see {@link LogIdentity}.
   */
  unrecognised_lines: number | null;
  /** The log's basename. Set by {@link readIdentity}, absent from a bare parse. */
  log?: string;
  /**
   * The filename stamp as epoch seconds. Set by {@link liveIdentities} only.
   *
   * `null` here means the filename carried no parseable stamp, which is a real
   * state (`RobloxStudioInstaller_0EA05.log`) rather than an absent one.
   */
  started?: number | null;
}

/** A fresh identity with every field explicitly absent. */
export function newLogIdentity(): LogIdentity {
  return {
    pid: null,
    place_path: null,
    place_id: null,
    universe_id: null,
    task: null,
    parent_pid: null,
    parent_session_guid: null,
    transport_token: null,
    session_guid: null,
    machine_guid: null,
    log_format: null,
    unrecognised_lines: null,
  };
}

/**
 * Pull every identity field out of one log's text.
 *
 * Fields the log does not state come back `null` rather than omitted, so a caller
 * can tell "the log does not say" from "not parsed yet".
 *
 * {@link LogIdentity.log_format} and {@link LogIdentity.unrecognised_lines} answer
 * "is this log in a format I understand?", and they are populated **only when `pid`
 * is null** - see the field docs for the measurement behind that.
 */
export function parseIdentity(text: string): LogIdentity {
  const found = newLogIdentity();

  const pid = PID_RE.exec(text);
  if (pid) {
    found.pid = Number(pid[1]);
  } else {
    // No PID line anywhere in the text. That is either a Studio that died before
    // writing one, or a log whose format this module cannot read - and the two
    // demand opposite responses, so the log is censused rather than reported as a
    // bare null.
    const report = formatReport(text);
    found.log_format = report.dominant;
    found.unrecognised_lines = report.unrecognised;
  }

  const session = SESSION_GUID_RE.exec(text);
  if (session) found.session_guid = session[1] as string;

  const machine = MACHINE_GUID_RE.exec(text);
  if (machine) found.machine_guid = machine[1] as string;

  // The banner is the untimestamped line holding the executable path. The negative
  // lookahead for a date in {@link BANNER_RE} keeps ordinary type-4 log lines out.
  //
  // That lookahead was long blamed for older-format logs losing their PID. It is not
  // the cause, and the measurement is worth keeping next to the code: a type-1 line
  // starts with an epoch, so the lookahead never excluded it, and the binary-name
  // requirement that does the real work rejected it correctly. The formats that
  // genuinely lost the PID were 2 and 3, for a different reason entirely - see
  // {@link PID_RE}. The lookahead is inert rather than harmful, so it stays.
  const banner = BANNER_RE.exec(text);
  if (!banner) return found;

  const args = banner[1] ?? "";

  for (const flag of PLACE_PATH_FLAGS) {
    const place = pathAfter(args, flag);
    if (place) {
      found.place_path = place;
      break;
    }
  }

  const uri = URI_PLACE_RE.exec(args);
  if (uri) {
    found.place_id = Number(uri[1]);
    found.universe_id = Number(uri[2]);
  } else {
    // Separate `-placeId N` / `-universeId M` flags, the File > New form.
    const flagPlace = FLAG_PLACE_RE.exec(args);
    if (flagPlace) found.place_id = Number(flagPlace[1]);
    const flagUniverse = FLAG_UNIVERSE_RE.exec(args);
    if (flagUniverse) found.universe_id = Number(flagUniverse[1]);
  }

  const parentGuid = FLAG_PARENT_GUID_RE.exec(args);
  // Uppercased here and *not* uppercased for session_guid; see the field doc.
  if (parentGuid) found.parent_session_guid = (parentGuid[1] as string).toUpperCase();

  // Task from the flags, then the URI's own `+task:`, then the launcher's intent
  // JSON. Any one of the three may be the only one present.
  for (const pattern of [TASK_RE, URI_TASK_RE, INTENT_TASK_RE]) {
    const task = pattern.exec(args);
    if (task) {
      found.task = task[1] as string;
      break;
    }
  }

  const parent = PARENT_PID_RE.exec(args);
  if (parent) found.parent_pid = Number(parent[1]);

  const transport = TRANSPORT_RE.exec(args);
  if (transport) found.transport_token = transport[1] as string;
  return found;
}

/**
 * Every per-place-open GUID in a log, in order, de-duplicated.
 *
 * A log can carry more than one: measured, one had 2 GUIDs across 29
 * `PlaceSessionId` lines, because the numeric and path forms are emitted by
 * different subsystems. Both are returned rather than picking the first, since which
 * one appears first is not stable enough to depend on.
 *
 * Uppercased, so the values compare cleanly; note this makes them *differently
 * cased* from {@link LogIdentity.session_guid}, which comes from a different line
 * and is left verbatim.
 */
export function sessionGuids(text: string): string[] {
  const seen: string[] = [];
  for (const match of text.matchAll(PLACE_SESSION_RE)) {
    const guid = (match.groups?.["guid"] as string).toUpperCase();
    if (!seen.includes(guid)) seen.push(guid);
  }
  for (const match of text.matchAll(DM_ID_RE)) {
    const guid = (match.groups?.["guid"] as string).toUpperCase();
    if (!seen.includes(guid)) seen.push(guid);
  }
  return seen;
}

/**
 * The place path a log's `PlaceSessionId` names, if it named one.
 *
 * `null` for the numeric-suffix form, which names a published place rather than a
 * file. This is the field that carries a URI launch's `AutoRecovery_N` counter, and
 * therefore the field that tells two concurrent URI launches of one place apart.
 *
 * Needs the **whole** log: these lines were measured at 282,739 bytes in an 868 KB
 * file, past any prefix worth reading on every file. Call it only on candidates,
 * never in a sweep.
 */
export function placeSessionPath(text: string): string | null {
  for (const match of text.matchAll(PLACE_SESSION_RE)) {
    const tail = (match.groups?.["tail"] as string).trim();
    if (tail.includes(".rbxl")) return tail;
  }
  return null;
}

/**
 * Decode a log buffer the way Python's `open(..., encoding="utf-8",
 * errors="ignore")` does.
 *
 * `Buffer.toString("utf8")` inserts U+FFFD for undecodable bytes where Python
 * *drops* them, so the replacement characters are stripped to match. Without this a
 * single bad byte in a multi-megabyte log leaves a character behind that Python
 * never produced.
 */
function decodeLog(buffer: Buffer): string {
  const text = buffer.toString("utf8");
  return text.includes("�") ? text.replaceAll("�", "") : text;
}

/**
 * Drop a trailing partial UTF-8 sequence from a byte-exact prefix read.
 *
 * Python opens these logs in **text** mode, so `read(262144)` returns 262144
 * *characters* and can never split a multi-byte one. A Node read of 262144 *bytes*
 * can, and a split sequence becomes U+FFFD - which the decoder above would then
 * delete, quietly losing the last character of the log as well. So the incomplete
 * tail is dropped before decoding, which reproduces Python's "never splits" property
 * with a byte-sized read.
 */
function trimPartialUtf8(buffer: Buffer): Buffer {
  const n = buffer.length;
  if (n === 0) return buffer;
  for (let back = 1; back <= Math.min(4, n); back += 1) {
    const byte = buffer[n - back] as number;
    if ((byte & 0x80) === 0) return buffer; // ASCII: nothing partial
    if ((byte & 0xc0) === 0x80) continue; // continuation byte: keep looking left
    const need = byte >= 0xf0 ? 4 : byte >= 0xe0 ? 3 : byte >= 0xc0 ? 2 : 1;
    return back < need ? buffer.subarray(0, n - back) : buffer;
  }
  return buffer;
}

function readPrefixBytes(fd: number, bytes: number): Buffer {
  const buffer = Buffer.allocUnsafe(bytes);
  let read = 0;
  while (read < bytes) {
    const got = readSync(fd, buffer, read, bytes - read, null);
    if (got <= 0) break; // EOF
    read += got;
  }
  return trimPartialUtf8(buffer.subarray(0, read));
}

function readRestBytes(fd: number): Buffer {
  const chunks: Buffer[] = [];
  const buffer = Buffer.allocUnsafe(64 * 1024);
  for (;;) {
    const got = readSync(fd, buffer, 0, buffer.length, null);
    if (got <= 0) break;
    chunks.push(Buffer.from(buffer.subarray(0, got)));
  }
  return Buffer.concat(chunks);
}

/**
 * How much of a log to read before giving up and reading the rest.
 *
 * The identity lines sit at the very top - command line at byte 507, the two GUIDs
 * at ~3.3 KB, the PID at ~3.7 KB, the largest of 43 logs being 3,809 bytes in a
 * 1.7 MB file. The `PlaceSessionId` lines are much deeper: measured at 64,520 to
 * 74,960 bytes across 13 logs, so a 64 KB prefix missed **every one of them**. 256 KB
 * is the measured knee - per-file cost is 0.100 ms at 64 KB and 0.130 ms at 256 KB, a
 * 1.3x cost for 4x the bytes, because the cost is mostly the open rather than the
 * read. Past that it stops being cheap: 1 MB costs 0.626 ms, 6.3x for 16x the bytes.
 *
 * What still does *not* fit is the path-suffixed `PlaceSessionId` at 282,739 bytes
 * in an 868 KB log, which is the only record of a URI launch's `AutoRecovery_N`
 * counter. That is read on demand by {@link placeSessionPath} rather than by
 * widening this, because only a handful of logs ever need it.
 *
 * The margin is what makes this safe to *try*, not what makes it correct: a log
 * still being written may not have reached its PID line yet, so a prefix with no
 * PID falls back to a full read rather than reporting a process as unidentified.
 *
 * Mutable so a test can shrink it rather than writing 256 KB of padding. Python
 * assigned to the module global directly; an imported ES binding is read-only, so
 * there is a setter instead.
 */
export let PREFIX_BYTES = 256 * 1024;

/** Override {@link PREFIX_BYTES}. Pass `null` to restore the measured 256 KB. */
export function setPrefixBytes(bytes: number | null): void {
  PREFIX_BYTES = bytes ?? 256 * 1024;
}

/** Read one log's place-session path, or null if unreadable or absent. */
export function readPlaceSessionPath(path: string): string | null {
  try {
    return placeSessionPath(decodeLog(readFileSync(path)));
  } catch {
    return null;
  }
}

/** Where Studio writes its logs. See `platform.logDir`. */
export function logDir(): string {
  return logDirectory();
}

// Test seam, standing in for the Python tests' `logid.log_dir = lambda: ...`.
// Restored to the real platform path by passing `null`.
let logDirectory: () => string = platformLogDir;

/** Point {@link logDir} somewhere else, or restore it with `null`. */
export function setLogDirectory(fn: (() => string) | null): void {
  logDirectory = fn ?? platformLogDir;
}

/**
 * Read one log file's identity, or `null` if it is unreadable.
 *
 * Reads a prefix, and only escalates to the whole file when the prefix has no PID.
 * Everything needed is within 4 KB in practice, but "in practice" is not a guarantee
 * for a log that is still being written, and silently reporting no PID for a live
 * process would drop it from the join entirely.
 *
 * `null` on failure is deliberate: a log that is locked, mid-rotation, or unreadable
 * is a normal condition, and a resolver that threw here would fail the whole call
 * over one file. In Node that is a thrown `Error` from `openSync`, not Python's
 * `OSError`, and the two are the same condition here: the difference is that Python
 * *raises* while Node *throws*, so the shape of the guard differs and the outcome
 * does not.
 */
export function readIdentity(path: string): LogIdentity | null {
  let text: string;
  try {
    const fd = openSync(path, "r");
    try {
      text = decodeLog(readPrefixBytes(fd, PREFIX_BYTES));
      if (!PID_RE.test(text)) {
        const rest = decodeLog(readRestBytes(fd));
        if (rest !== "") text += rest;
      }
    } finally {
      closeSync(fd);
    }
  } catch {
    return null;
  }

  const identity = parseIdentity(text);
  identity.log = basename(path);
  return identity;
}

/**
 * Why a log's PID could not be read, or `null` if it was.
 *
 * The counterpart to {@link ambiguousReason}, for the other half of the join. A
 * caller that lands on `pid === null` needs to know which of two very different
 * situations it is in before deciding what to do:
 *
 * - **the log is fine and simply has no PID** - a Studio that died before writing
 *   the notifier line. Measured twice on this machine: the installer log, and a
 *   1,335-byte Studio log from a process that lived 0.36 s. Nothing is wrong and the
 *   right answer is "this process is gone".
 * - **the log is in a format this module does not read** - a parser gap, which needs
 *   extending, and which would otherwise be indistinguishable from the first case and
 *   quietly read as "this process is gone" forever.
 *
 * Returns a sentence, because a caller that has to reconstruct this itself from a
 * bare integer is a caller that will get it wrong. `null` when the PID *was* read,
 * or when the format is not the reason - the caller has a PID to work with and no
 * explanation is owed.
 */
export function noPidReason(identity: LogIdentity): string | null {
  if (identity.pid !== null) return null;
  const unknown = identity.unrecognised_lines;
  if (!unknown) {
    // Either the format checked out (so the log is well-formed and simply carries no
    // PID line), or no census ran. Both mean "not a format problem", which is the
    // half of the answer that is reassuring.
    return null;
  }
  const dominant = identity.log_format;
  const log = identity.log;
  const where = log ? ` in ${log}` : "";
  const shape =
    dominant === null
      ? // No record in any known format anywhere in the log. Saying "predominant
        // format is null" would be precise and useless, so name what that actually
        // means: nothing here was readable at all.
        "No line in the log matched any of the four known record formats"
      : `The log's predominant record format is '${dominant}'`;
  return (
    `${unknown} line(s)${where} are in an FLog format this parser does not recognise, ` +
    `so no PID line could be read from them. ${shape}. This is a parser gap, not a ` +
    `missing PID`
  );
}

/**
 * How far apart a log's filename stamp and its process's creation time may be and
 * still count as the same process.
 *
 * Measured over 47 real logs: the worst gap between a filename's stamp and that
 * log's own first timestamped line is 1.9 s. 60 s is a ~30x margin, and the cost of
 * being generous is only that a few more files are read.
 *
 * This is deliberately *not* how a process is identified. It narrows which logs are
 * worth reading; the PID inside the log is what decides. A window that is too narrow
 * costs a slow fallback, never a wrong answer.
 */
export const START_WINDOW_SECONDS = 60.0;

/**
 * PowerShell's `ConvertTo-Json` renders CIM datetimes as `/Date(1790729920871)/`, a
 * millisecond epoch. Every other guess at the format failed on it, and when the
 * Python parse failed silently the start-time filter skipped every file.
 */
const CIM_JSON_DATE = /\/Date\((-?\d+)\)\//;

/**
 * Epoch seconds from a process creation time, or `null` if unparseable.
 *
 * Handles the `/Date(ms)/` form that `Get-CimInstance | ConvertTo-Json` actually
 * produces, plus the plain ISO and US-shaped strings, because a caller that only has
 * one of them should not have to know which.
 *
 * **Unzoned forms are read as local time, deliberately.** Python's
 * `datetime.strptime(...).timestamp()` on a naive datetime uses the host's local
 * zone, and the filter this feeds compares against {@link START_WINDOW_SECONDS} from
 * a *UTC* filename stamp. Making these UTC here instead would silently shift the
 * window by the host's offset - three hours on the machine this was measured on -
 * and every file would fall outside it. `Date` parses a date-time form without an
 * offset as local time too, so the two agree; a `Z` or `±HH:MM` suffix, which
 * Python's `fromisoformat` honours, is honoured here.
 */
export function parseProcessStarted(value: number | string | null | undefined): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  const text = String(value ?? "").trim();
  if (text === "") return null;

  const match = CIM_JSON_DATE.exec(text);
  if (match) return Number(match[1]) / 1000.0;

  // "%m/%d/%Y %H:%M:%S", then "%Y-%m-%dT%H:%M:%S", then
  // "%m/%d/%Y %I:%M:%S %p" - the same order Python tries, because the formats
  // overlap only in ways that would change the answer if the order were not kept.
  const us = /^(\d{2})\/(\d{2})\/(\d{4})\s+(\d{1,2}):(\d{2}):(\d{2})$/.exec(text);
  if (us) return localEpoch(Number(us[3]), Number(us[1]), Number(us[2]), Number(us[4]), Number(us[5]), Number(us[6]));
  const iso = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})$/.exec(text);
  if (iso) return localEpoch(Number(iso[1]), Number(iso[2]), Number(iso[3]), Number(iso[4]), Number(iso[5]), Number(iso[6]));
  const us12 = /^(\d{2})\/(\d{2})\/(\d{4})\s+(\d{1,2}):(\d{2}):(\d{2})\s+([AP]M)$/i.exec(text);
  if (us12) {
    let hour = Number(us12[4]) % 12;
    if (us12[7]!.toUpperCase() === "PM") hour += 12;
    return localEpoch(Number(us12[3]), Number(us12[1]), Number(us12[2]), hour, Number(us12[5]), Number(us12[6]));
  }

  return parseIsoLike(text);
}

/** Build an epoch from local-time components, or null if the date does not exist. */
function localEpoch(
  year: number,
  month: number,
  day: number,
  hour: number,
  minute: number,
  second: number,
): number | null {
  if (year < 1 || year > 9999) return null;
  if (month < 1 || month > 12) return null;
  if (day < 1 || day > 31) return null;
  if (hour > 23 || minute > 59 || second > 61) return null;
  const at = new Date(0);
  // setUTCFullYear rather than the Date.UTC(...) constructor, which maps 0-99 onto
  // 1900-1999 - so year 1 would come back as 1901.
  at.setUTCFullYear(year, month - 1, day);
  at.setUTCHours(hour, minute, Math.min(second, 59), 0);
  if (at.getUTCDate() !== day) return null; // 31 February, which Python rejects too
  // Re-read the components as *local* time and convert. `at` currently holds them
  // as UTC; the same wall-clock reading in the host zone is the naive datetime
  // Python's strptime produces.
  return new Date(
    year,
    month - 1,
    day,
    hour,
    minute,
    Math.min(second, 59),
    0,
  ).getTime() / 1000;
}

/**
 * The ISO forms Python's `datetime.fromisoformat` accepts on 3.11+:
 * `YYYY-MM-DD`, `YYYY-MM-DD[T ]HH:MM[:SS[.fff]]`, and the same with a `Z`, `±HH`,
 * `±HH:MM` or `±HHMM` offset, plus the basic `YYYYMMDD` and `YYYYMMDDTHHMMSS`.
 *
 * Scoped to those because a caller of this function only ever has one of the forms
 * the process list produces. Anything else returns `null`, which is the honest
 * answer: the alternative is a plausible wrong epoch, and a wrong epoch here is
 * every file falling outside the start-time window.
 *
 * The zone handling is the part worth reading twice. An **absent** zone is naive, and
 * Python's `.timestamp()` on a naive datetime reads it as *local* - so the unzoned
 * branch goes through {@link localEpoch}. A **present** zone is aware, so the same
 * wall clock is read as if it were UTC and then shifted by the offset: that is
 * `09:18:20+03:00` = `06:18:20Z`, and reading the `+03:00` case as local instead
 * would be off by the host's offset on top of the offset itself.
 */
function parseIsoLike(text: string): number | null {
  const m =
    /^(\d{4})-?(\d{2})-?(\d{2})(?:[T ](\d{2}):?(\d{2})(?::?(\d{2})(?:\.(\d{1,6})\d*)?)?(Z|z|[+-]\d{2}:?\d{2}|[+-]\d{2})?)?$/.exec(
      text,
    );
  if (!m) return null;
  const year = Number(m[1]);
  const month = Number(m[2]);
  const day = Number(m[3]);
  const hour = m[4] === undefined ? 0 : Number(m[4]);
  const minute = m[5] === undefined ? 0 : Number(m[5]);
  const second = m[6] === undefined ? 0 : Number(m[6]);
  const micros = m[7] === undefined ? 0 : Number(m[7].padEnd(6, "0"));
  const zone = m[8];

  if (zone === undefined) {
    const naive = localEpoch(year, month, day, hour, minute, second);
    if (naive === null) return null;
    return m[4] === undefined ? naive : naive + micros / 1e6;
  }

  const asUtc = utcEpoch(year, month, day, hour, minute, second);
  if (asUtc === null) return null;
  if (zone === "Z" || zone === "z") return asUtc + micros / 1e6;
  // A numeric offset shifts the moment the *other* way: a wall clock reading
  // 09:18:20 at +03:00 is 06:18:20 UTC.
  const negative = zone.startsWith("-");
  const digits = zone.slice(1).replace(":", "");
  const offsetHours = Number(digits.slice(0, 2));
  const offsetMinutes = digits.length > 2 ? Number(digits.slice(2, 4)) : 0;
  const offset = (offsetHours * 3600 + offsetMinutes * 60) * (negative ? 1 : -1);
  return asUtc + micros / 1e6 + offset;
}

// --------------------------------------------------------------------------- //
// Why a place did not open
// --------------------------------------------------------------------------- //

/**
 * The place-open state machine, in the `[telemetryLog]` channel. Measured over 47
 * logs: 4 ended in `OpenPlaceFailure`, and every one gave a reason.
 *
 * 3 of the 4 said "Error fetching latest place version", and all 3 were URI
 * launches. That is the cause of the signature this project had been recording as an
 * unexplained "failed launch" - a Studio that attaches to the mesh but never gets a
 * place name. It is a fetch of the published place failing, not a mesh or transport
 * fault, and no amount of retrying the transport fixes it.
 *
 * The 4th said "Connection error 279" and was a play test's StartClient.
 */
const STATE_RE = /\[telemetryLog\]\s*State:\s*(\w+)/;
const ERROR_TYPE_RE = /\[telemetryLog\]\s*ErrorType:\s*(\S+)/;
const ERROR_MSG_RE = /\[telemetryLog\]\s*ErrorMessage:\s*(.*\S)/;
const SIGNED_IN_RE = /\[FLog::LoginController\] Login got Standalone DM ready/;
const INSTANCES_AT_LAUNCH_RE = /Running instance count at launch (\d+)/;

/**
 * The states that *end* an attempt, as opposed to the ones that report progress.
 *
 * The full sequence measured over 21 successful Edit launches:
 * `OpenPlaceInitialization -> OpenPlaceCreateDataModel -> OpenPlaceLoadDataModel ->
 * OpenPlaceWaitForStreaming -> OpenPlacePostLoadDataModel -> OpenPlaceEnterDataModelScope
 * -> OpenPlacePreSuccess -> OpenPlaceSuccess` and then it keeps going, to
 * `PlaceIdle`.
 *
 * Two traps in that list, both hit here:
 *
 * - taking the **last** state reports `PlaceIdle`, so 21 successful launches looked
 *   like 0 opened. Only the terminal states decide the outcome.
 * - `OpenPlacePreSuccess` **ends with "Success"** and is a progress state. A suffix
 *   test would call a half-finished load a success.
 *
 * So the terminal set is spelled out rather than pattern-matched.
 */
const TERMINAL_SUCCESS: ReadonlySet<string> = new Set(["OpenPlaceSuccess", "PlaySoloSuccess"]);

/** How a Studio's attempt to open a place ended. */
export interface OpenOutcome {
  /** The last terminal state seen, or null. Progress states never set it. */
  state: string | null;
  /** `ErrorType` from the telemetry channel, or null. */
  error_type: string | null;
  /** The first `ErrorMessage` after a failure, or null. */
  error_message: string | null;
  /** Whether the log shows the login controller reaching user scope. */
  signed_in: boolean;
  /** `Running instance count at launch N`, or null. */
  instances_at_launch: number | null;
  /** Whether a terminal *success* state was the last terminal state. */
  opened: boolean;
  /** The log's basename. Set by {@link readOpenOutcome} only. */
  log?: string;
}

/**
 * How a Studio's attempt to open a place ended.
 *
 * Only the terminal states move the outcome, so a later success clears an earlier
 * failure: Studio retries, and reporting the first failure would blame a launch that
 * recovered. A failure's `ErrorMessage` arrives on the lines after it, so the parser
 * waits for exactly one message rather than grabbing the last one in the file.
 *
 * `Hang In Progress` is deliberately not treated as a hang. It appears in logs that
 * went on to open their place normally, including one with 5 instances already
 * running. Only `Hang Detected` means the hang escalated, and that string appears in
 * none of the 47 logs measured here - so on this machine, at Studio 0.741, a startup
 * hang is not a thing that happened.
 */
export function openOutcome(text: string): OpenOutcome {
  let state: string | null = null;
  let errorType: string | null = null;
  let errorMessage: string | null = null;
  const signedIn = SIGNED_IN_RE.test(text);
  let instances: number | null = null;
  const countMatch = INSTANCES_AT_LAUNCH_RE.exec(text);
  if (countMatch) {
    const parsed = Number(countMatch[1]);
    instances = Number.isFinite(parsed) ? parsed : null;
  }

  let awaitingMessage = false;
  for (const line of splitLines(text)) {
    const found = STATE_RE.exec(line);
    if (found) {
      const candidate = found[1] as string;
      const terminal = TERMINAL_SUCCESS.has(candidate) || candidate.endsWith("Failure");
      if (!terminal) continue; // a progress state says nothing about the outcome
      state = candidate;
      awaitingMessage = candidate.endsWith("Failure");
      if (!awaitingMessage) {
        errorType = null;
        errorMessage = null;
      }
      continue;
    }
    const error = ERROR_TYPE_RE.exec(line);
    if (error) {
      errorType = error[1] as string;
      continue;
    }
    if (awaitingMessage) {
      const message = ERROR_MSG_RE.exec(line);
      if (message) {
        errorMessage = message[1] as string;
        awaitingMessage = false;
      }
    }
  }

  return {
    state,
    error_type: errorType,
    error_message: errorMessage,
    signed_in: signedIn,
    instances_at_launch: instances,
    opened: state !== null && TERMINAL_SUCCESS.has(state),
  };
}

/**
 * Read one log's place-open outcome, or `null` if unreadable.
 *
 * This reads the **whole** file, unlike {@link readIdentity}. The identity lines sit
 * in the first 4 KB, but the outcome lines appear whenever the load finished, which
 * for a slow place is deep into the log. So the prefix that makes the identity sweep
 * cheap would silently report "no outcome" here, and the outcome is the whole point
 * of this function. Kept off the hot path for that reason: only a failure diagnosis
 * pays for it.
 */
export function readOpenOutcome(path: string): OpenOutcome | null {
  let outcome: OpenOutcome;
  try {
    outcome = openOutcome(decodeLog(readFileSync(path)));
  } catch {
    return null;
  }
  outcome.log = basename(path);
  return outcome;
}

/**
 * Epoch seconds of a log filename's UTC stamp, or `null`.
 *
 * **The `Z` in the filename is load-bearing, so it is parsed as UTC.** Naive
 * `strptime(...).timestamp()` reads it as local time, which on the measuring machine
 * (UTC+3) was three hours early - and the field this feeds, a process start time, is
 * exactly where a three-hour error hides rather than shows.
 *
 * A stamp whose day is out of range for its month is rejected rather than rolled
 * over: Python's `_strptime` ends by constructing a `date`, so `20260231` is a
 * `ValueError` and becomes `null`, while `Date.UTC` would silently become 3 March.
 * That is the same class of silent-three-hour-or-three-day error, one layer down.
 */
export function stampSeconds(name: string): number | null {
  const match = LOG_STAMP_RE.exec(name);
  if (!match) return null;
  const digits = match[1] as string;
  const year = Number(digits.slice(0, 4));
  const month = Number(digits.slice(4, 6));
  const day = Number(digits.slice(6, 8));
  const hour = Number(digits.slice(9, 11));
  const minute = Number(digits.slice(11, 13));
  const second = Number(digits.slice(13, 15));
  return utcEpoch(year, month, day, hour, minute, second);
}

/** Epoch seconds for UTC components, or null if that date does not exist. */
function utcEpoch(
  year: number,
  month: number,
  day: number,
  hour: number,
  minute: number,
  second: number,
): number | null {
  if (year < 1 || year > 9999) return null;
  if (month < 1 || month > 12) return null;
  if (day < 1 || day > 31) return null;
  if (hour > 23 || minute > 59 || second > 61) return null;
  const at = new Date(0);
  at.setUTCFullYear(year, month - 1, day);
  at.setUTCHours(hour, minute, Math.min(second, 59), 0);
  if (at.getUTCMonth() !== month - 1 || at.getUTCDate() !== day) return null;
  return at.getTime() / 1000;
}

/** One log file, as `_list_logs` produces it. */
export interface LogEntry {
  name: string;
  path: string;
  /**
   * The filename stamp, or `-Infinity` when the name carries none, so an
   * unparseable name sorts **last** and is read only after every log that can be
   * ordered.
   */
  sort_key: number;
}

/**
 * Every log in a directory, newest first, as `{name, path, sort_key}`.
 *
 * Ordered by the **filename stamp**, not by mtime. The stamp is in the name, so
 * sorting on it needs no `stat` call, and that matters: with thousands of logs the
 * stats cost more than the reads. A log still being written has an mtime that moves
 * under the sort and can land in the wrong place; its stamp is fixed at creation, so
 * the order is stable while the file grows.
 *
 * `readdirSync` throws where Python's `os.listdir` would also raise, so the caller
 * guards with {@link isDirectory}.
 */
export function listLogs(directory: string): LogEntry[] {
  const entries: LogEntry[] = [];
  for (const name of readdirSync(directory)) {
    if (!name.endsWith(".log")) continue;
    const stamp = stampSeconds(name);
    entries.push({
      name,
      path: join(directory, name),
      sort_key: stamp ?? Number.NEGATIVE_INFINITY,
    });
  }
  // Stable in both languages, so equal stamps keep directory order either way.
  entries.sort((a, b) => b.sort_key - a.sort_key);
  return entries;
}

function isDirectory(path: string): boolean {
  try {
    return statSync(path).isDirectory();
  } catch {
    return false;
  }
}

/** How long ago a name's stamp may be from *any* of the given process starts. */
function nearAnyStart(name: string, started: ReadonlyMap<number, number>): boolean {
  const stamp = stampSeconds(name);
  if (stamp === null) return true; // an unparseable stamp is not evidence of a mismatch
  for (const when of started.values()) {
    if (Math.abs(stamp - when) <= START_WINDOW_SECONDS) return true;
  }
  return false;
}

function readCandidates(entries: LogEntry[], wanted: Set<number>): Map<number, LogIdentity> {
  const found = new Map<number, LogIdentity>();
  for (const entry of entries) {
    if (found.size === wanted.size) break;
    const identity = readIdentity(entry.path);
    if (!identity) continue;
    const pid = identity.pid;
    if (pid === null || !wanted.has(pid) || found.has(pid)) continue;
    identity.started = stampSeconds(entry.name);
    found.set(pid, identity);
  }
  return found;
}

/**
 * Map each live PID to its log's identity.
 *
 * Scans the log directory and keeps the entries whose stated PID is one of `pids`.
 * This is the step that needs no console write: the log names the process, so nothing
 * has to be injected to find out which log is whose.
 *
 * `started` maps a PID to its creation time in epoch seconds, and narrows which
 * files are read to those within {@link START_WINDOW_SECONDS} of it.
 *
 * **The filter is not a clear win, and it was nearly cut for that reason.** Measured
 * at 3,000 logs with 3 Studios:
 *
 * - live Studios are the newest, the normal case: filter **0.098 s**, no filter
 *   **0.056 s**. The filter loses, because stamp ordering already puts the live logs
 *   first and the read loop stops as soon as it has them all.
 * - a live Studio is old and 3,000 newer logs from dead processes follow it: filter
 *   **0.097 s**, no filter **1.285 s**. The filter wins by 13x, because without it the
 *   read loop has to walk every newer log first.
 *
 * So it is kept for the second case and tolerated as slight overhead in the first.
 * The window only chooses which files to *read*: the PID inside the log is still
 * what decides, and a narrowed pass that finds nothing falls back to the full
 * directory, so a wrong window costs time rather than a missing identity.
 */
export function liveIdentities(
  pids: number[],
  started: ReadonlyMap<number, number> | null = null,
): Map<number, LogIdentity> {
  const wanted = new Set(pids.map((p) => Math.trunc(p)));
  if (wanted.size === 0) return new Map();
  const directory = logDir();
  if (!isDirectory(directory)) return new Map();

  let entries = listLogs(directory);
  if (started) {
    const window = entries.filter((e) => nearAnyStart(e.name, started));
    // A window that excludes everything means the window is wrong, not that the
    // processes have no logs.
    if (window.length > 0) {
      const found = readCandidates(window, wanted);
      if (found.size === wanted.size) return found;
      // Everything outside the window first, then the window again, so a partial
      // hit does not cost a second full pass over what the window already read.
      const inWindow = new Set(window.map((e) => e.name));
      entries = [...entries.filter((e) => !inWindow.has(e.name)), ...window];
    }
  }
  return readCandidates(entries, wanted);
}

/**
 * Python's `repr()` for a plain string, so a reason string quotes a name the same
 * way on both sides. Uses double quotes when the string contains a single quote and
 * no double quote, which is Python's own rule and the only case that shows up in a
 * place name.
 */
function pyRepr(value: string): string {
  const quote = value.includes("'") && !value.includes('"') ? '"' : "'";
  let out = quote;
  for (const ch of value) {
    if (ch === quote || ch === "\\") out += `\\${ch}`;
    else if (ch === "\n") out += "\\n";
    else if (ch === "\t") out += "\\t";
    else if (ch === "\r") out += "\\r";
    else out += ch;
  }
  return out + quote;
}

/**
 * Explain why a mesh name could not be pinned to one log, or `null`.
 *
 * A caller that gets a non-null answer here needs the console-token join, and should
 * say so rather than pick one.
 */
export function ambiguousReason(
  meshName: string | null,
  matches: readonly Pick<LogIdentity, "place_path" | "place_id">[],
): string | null {
  if (matches.length === 1) return null;
  if (!meshName) {
    return (
      "this Studio reports no place name, so the log cannot be matched to it; " +
      "the log only records a command line"
    );
  }
  if (MESH_URI_NAME_RE.test(meshName)) {
    return (
      `mesh name ${pyRepr(meshName)} is a URI launch, and the logs of the candidate ` +
      "processes do not record an autorecovery path, so the AutoRecovery counter " +
      "cannot be compared. They opened a plain unsaved document rather than an " +
      "autorecovery copy, so there is no counter to compare"
    );
  }
  if (matches.length > 1) {
    return (
      `${matches.length} logs report place ${pyRepr(meshName)}, so the name does not ` +
      "identify one process"
    );
  }
  return `no Studio log mentions place ${pyRepr(meshName)}`;
}

/**
 * PIDs whose log could correspond to `meshName`.
 *
 * Two rules, in order of directness:
 *
 * 1. The mesh name is the opened file's **basename** for a file launch, and the
 *    log's command line holds the full path, so a basename match is an exact string
 *    comparison between two things that already exist.
 * 2. The mesh name is `Template_<placeId>_AutoRecovery_<N>.rbxl` for a URI launch.
 *    Only the placeId is in both, which identifies the *place* but not the
 *    *instance*, so this rule is allowed to return several PIDs.
 */
export function matchMeshName(
  meshName: string | null,
  identities: ReadonlyMap<number, LogIdentity>,
): number[] {
  if (!meshName) return [];
  const exact: number[] = [];
  for (const [pid, identity] of identities) {
    if (identity.place_path && basename(identity.place_path) === meshName) exact.push(pid);
  }
  if (exact.length > 0) return exact;
  return meshRowsForPlaceId(meshName, identities);
}

function meshRowsForPlaceId(
  meshName: string,
  identities: ReadonlyMap<number, LogIdentity>,
): number[] {
  const uri = MESH_URI_NAME_RE.exec(meshName);
  if (!uri) return [];
  const placeId = Number(uri[1]);
  const candidates: number[] = [];
  for (const [pid, identity] of identities) {
    if (identity.place_id === placeId) candidates.push(pid);
  }
  if (candidates.length < 2) return candidates;

  // Two or more URI launches of one place. The `AutoRecovery_N` in the mesh name is
  // the only thing that separates them, and the log records it on the
  // path-suffixed `PlaceSessionId` line - so read that one field, on this handful of
  // logs, rather than declaring the case unresolvable.
  const refined = refineByAutorecoveryCounter(meshName, candidates, identities);
  return refined.length > 0 ? refined : candidates;
}

/**
 * Narrow URI candidates by the autorecovery path their logs record.
 *
 * Returns the narrowed list, or `[]` when the log data cannot decide - in which case
 * the caller keeps the full candidate list rather than narrowing to a guess. A log
 * with no path-suffixed `PlaceSessionId` never opened an autorecovery document, so it
 * has no counter to offer and simply does not narrow.
 */
function refineByAutorecoveryCounter(
  meshName: string,
  candidates: readonly number[],
  identities: ReadonlyMap<number, LogIdentity>,
): number[] {
  const wanted = basename(meshName);
  const matched: number[] = [];
  for (const pid of candidates) {
    const log = identities.get(pid)?.log;
    if (!log) continue;
    const path = readPlaceSessionPath(join(logDir(), log));
    if (path && basename(path) === wanted) matched.push(pid);
  }
  return matched;
}

/**
 * Every mesh name that could belong to this log's identity.
 *
 * The mirror of {@link matchMeshName}, for the launch direction: a process is known
 * and the `studio_id` is wanted. Yields the same strings the mesh would report, so
 * both directions agree on what a name means.
 */
export function meshNamesForIdentity(identity: LogIdentity): string[] {
  const names: string[] = [];
  if (identity.place_path) names.push(basename(identity.place_path));
  if (identity.place_id !== null) {
    // The AutoRecovery counter is not knowable from the log, so the prefix is what
    // can be asserted; a caller matches it as a prefix.
    names.push(`Template_${identity.place_id}_AutoRecovery_`);
  }
  return names;
}

/** Whether one mesh name belongs to one log identity. */
export function nameMatchesIdentity(meshName: string | null, identity: LogIdentity): boolean {
  if (!meshName) return false;
  if (identity.place_path && basename(identity.place_path) === meshName) return true;
  if (identity.place_id !== null) {
    return meshName.startsWith(`Template_${identity.place_id}_AutoRecovery_`);
  }
  return false;
}

// --------------------------------------------------------------------------- //
// The parent edge: reaching a Studio that reports no place name
// --------------------------------------------------------------------------- //
//
// A play test's server and clients report `name: null`, so {@link matchMeshName}
// returns nothing for them and the whole chain `studio_id -> mesh name -> log -> pid`
// stops at its first step. They are not reachable by name at all, because a session
// member opens no document of its own - `name: null` here is not a missing value, it
// is the truth.
//
// What does exist is an edge the child states about itself in its own command line:
//
// ```
// -task StartServer -localProjectFile <path> -parentPid 19028
// ```
//
// `-parentPid` names the launching Studio's process outright. No name matching, no
// log sweep, no console write: a child is one hop from a process whose place is
// already known, because the parent is identified by the existing
// log-stated-PID chain ({@link liveIdentities}) exactly like any other.
//
// **What it does not do is separate a server from its clients.** The edge says which
// Studio started the test; nothing in any log says which of the unnamed mesh rows is
// the server and which is a client. {@link resolveUnnamedStudio} therefore resolves
// only when one anchored child and one unnamed row account for each other exactly,
// and otherwise says so - see the note on the counting rule there for why guessing
// here would be the worst possible failure.

/**
 * The tasks only a play test's child processes carry, lowercased because the task
 * is lowercased before comparison.
 *
 * Deliberately narrow, and the narrowness is load-bearing. Plenty of Studio
 * processes carry a `-parentPid`: the Roblox launcher process points at whatever
 * started Studio, and a File > New child points at its launcher and then *does* open
 * a place of its own. Keying on `-parentPid` alone would put a named Studio into the
 * candidate pool for an unnamed one, which is the exact confusion the name join
 * exists to avoid. The task is what distinguishes "a session member with no document
 * of its own" from "a Studio that opened one".
 */
export const PLAYTEST_TASKS: ReadonlySet<string> = new Set(["startserver", "startclient"]);

/** Whether a `-task` value is a play test's server or client. */
export function isPlaytestTask(task: string | null): boolean {
  return Boolean(task) && PLAYTEST_TASKS.has(String(task).toLowerCase());
}

/**
 * PIDs from `pid`'s parent outward, as far as the logs carry the edge.
 *
 * A play test is two deep - client to server to the Edit Studio that pressed Play -
 * so one hop identifies the session but not the document. Walking the whole chain is
 * what lets a report say *which place* an unnamed Studio is running.
 *
 * Every step is the child's own recorded `-parentPid`, and the walk is bounded twice:
 * by `livePids` (a parent that is not running is not an ancestor of anything) and by
 * `seen` (two logs claiming each other as parent would otherwise loop forever, and a
 * loop in a resolver is a hang, not an error).
 */
export function ancestorChain(
  pid: number,
  identities: ReadonlyMap<number, LogIdentity>,
  livePids: ReadonlySet<number>,
): number[] {
  const chain: number[] = [];
  const seen = new Set<number>([pid]);
  let current = pid;
  for (;;) {
    const parent = identities.get(current)?.parent_pid;
    if (parent === null || parent === undefined) return chain;
    if (seen.has(parent) || !livePids.has(parent)) return chain;
    chain.push(parent);
    seen.add(parent);
    current = parent;
  }
}

/** Every live play-test process, with its parent edge resolved as far as it goes. */
export interface PlaytestChild {
  pid: number;
  task: string | null;
  place_path: string | null;
  log?: string;
  parent_pid: number | null;
  parent_task: string | null;
  parent_place_path: string | null;
  parent_place_id: number | null;
  parent_log?: string;
  /** The parent log's `Session GUID is`, or null. */
  parent_session_guid: string | null;
  /** The child's stated `-parentSessionGuid`, or null. */
  stated_parent_session_guid: string | null;
  /**
   * Whether the stated GUID matches the parent log's, or `null` for "not checked".
   *
   * **The premise is unverified**: that `-parentSessionGuid` is the parent
   * *process's* session GUID is read off the flag's name and one command line, and
   * was never observed to equal a real parent's `Session GUID is`. So a disagreement
   * is reported and does not veto the pid edge, which is stated outright on both
   * sides rather than quietly assumed true. "Not checked" and "checked, disagreed"
   * must not look the same, hence the three states rather than a boolean.
   */
  parent_guid_confirms: boolean | null;
  /** The parent chain outward, as far as the logs carry it. */
  ancestors: number[];
  anchored: boolean;
  /** Why it is not anchored, or "" when it is. */
  anchor_note: string;
}

/**
 * Every live play-test process, with its parent edge resolved as far as it goes.
 *
 * Returned in pid order, and *every* play-test process is returned whether or not
 * the edge anchored - a process whose `-parentPid` names nothing live is evidence,
 * and swallowing it is how the missing edge would go unreported.
 */
export function playtestChildren(
  identities: ReadonlyMap<number, LogIdentity>,
  livePids: ReadonlySet<number>,
): PlaytestChild[] {
  const rows: PlaytestChild[] = [];
  for (const pid of [...identities.keys()].sort((a, b) => a - b)) {
    const identity = identities.get(pid) as LogIdentity;
    const task = identity.task;
    if (!isPlaytestTask(task)) continue;

    const parentPid = identity.parent_pid;
    const parent = parentPid === null || parentPid === undefined ? undefined : identities.get(parentPid);
    const statedGuid = identity.parent_session_guid;
    const parentGuid = parent?.session_guid ?? null;
    const confirms =
      statedGuid && parentGuid ? statedGuid.toUpperCase() === parentGuid.toUpperCase() : null;

    let anchored = true;
    let note = "";
    if (parentPid === null || parentPid === undefined) {
      anchored = false;
      note = "no -parentPid in this process's own command line";
    } else if (parentPid === pid) {
      anchored = false;
      note = `-parentPid ${parentPid} names this process`;
    } else if (!livePids.has(parentPid)) {
      anchored = false;
      note = `-parentPid ${parentPid} names a process that is not running`;
    } else if (parent === undefined) {
      anchored = false;
      note =
        `-parentPid ${parentPid} is running but has no Studio log yet, ` +
        "so its place is unknown";
    }

    const row: PlaytestChild = {
      pid,
      task,
      place_path: identity.place_path,
      parent_pid: parentPid ?? null,
      parent_task: parent?.task ?? null,
      parent_place_path: parent?.place_path ?? null,
      parent_place_id: parent?.place_id ?? null,
      parent_session_guid: parentGuid,
      stated_parent_session_guid: statedGuid,
      parent_guid_confirms: confirms,
      ancestors: ancestorChain(pid, identities, livePids),
      anchored,
      anchor_note: note,
    };
    if (identity.log !== undefined) row.log = identity.log;
    if (parent?.log !== undefined) row.parent_log = parent.log;
    rows.push(row);
  }
  return rows;
}

/** One row of {@link resolveUnnamedStudio}'s `tree`. */
export interface UnnamedTreeRow {
  pid: number;
  task: string | null;
  parent_pid: number | null;
  anchored: boolean;
  note: string;
}

/**
 * Identify a mesh row that reports `name: null`, or say why it cannot be.
 *
 * Modelled as a discriminated union on `resolved` rather than one shape with
 * everything optional, so `child` is reachable **only** on the branch that has one.
 * In the Python dict every key is `Any` and every access type-checks, so `got["child"]`
 * on an unresolved answer raised at runtime - in the tool's caller, not in its test.
 */
export type UnnamedResolution =
  | {
      resolved: true;
      child: PlaytestChild;
      reason: null;
      candidates: number[];
      tree: UnnamedTreeRow[];
    }
  | {
      resolved: false;
      reason: string;
      candidates: number[];
      tree: UnnamedTreeRow[];
    };

/**
 * Identify a mesh row that reports `name: null`, or say why it cannot be.
 *
 * `unnamedRows` is how many connected mesh rows carry no name. It is **required**,
 * and it is the whole decision: the mesh row carries only `id` and `name`, so the
 * number of unnamed rows is the only thing on the mesh side that distinguishes a
 * server from its clients. Without it the caller would be asking "is this the only
 * unnamed Studio?", and answering that with "yes" because the pool happened to hold
 * one process would be the exact confidently-wrong kill this resolver exists to
 * prevent.
 *
 * On refusal it returns a `reason` naming what is missing **and** a `tree` of every
 * play-test process seen, so an unresolved answer still says what the logs *do*
 * know.
 */
export function resolveUnnamedStudio(
  identities: ReadonlyMap<number, LogIdentity>,
  livePids: ReadonlySet<number>,
  unnamedRows: number,
): UnnamedResolution {
  const children = playtestChildren(identities, livePids);
  const anchored = children.filter((child) => child.anchored);
  const tree: UnnamedTreeRow[] = children.map((child) => ({
    pid: child.pid,
    task: child.task,
    parent_pid: child.parent_pid,
    anchored: child.anchored,
    note: child.anchor_note,
  }));

  if (anchored.length === 0) {
    const detail =
      children.length > 0
        ? children.map((child) => `pid ${child.pid}: ${child.anchor_note}`).join("; ")
        : "no running Studio process carries -task StartServer or -task " +
          "StartClient, so there is no -parentPid edge to follow";
    return {
      resolved: false,
      reason:
        "this Studio reports no place name and the logs offer no anchored " +
        "play-test child to match it against: " +
        detail +
        ". A Studio that attached with no place open also reports name: null, and " +
        "no log field separates that case from a session member",
      candidates: [],
      tree,
    };
  }

  const pids = anchored.map((child) => child.pid).join(", ");
  if (anchored.length === 1 && unnamedRows === 1) {
    return {
      resolved: true,
      child: anchored[0] as PlaytestChild,
      reason: null,
      candidates: [(anchored[0] as PlaytestChild).pid],
      tree,
    };
  }

  if (unnamedRows < 1) {
    // The caller asked about a row with no name while reporting that none are
    // connected. Something upstream miscounted, and answering from this side would
    // bury that rather than surface it.
    return {
      resolved: false,
      reason:
        "asked to identify a Studio reporting no place name, but " +
        `${unnamedRows} connected rows report no name, so the mesh answer ` +
        "and the question disagree; refusing rather than guessing which reading is wrong",
      candidates: anchored.map((child) => child.pid),
      tree,
    };
  }

  const reason =
    anchored.length === 1
      ? `${unnamedRows} connected mesh rows report no place name and exactly one ` +
        `live play-test process is anchored by -parentPid (pid ${pids}). The row ` +
        "being asked about is not necessarily that process: the remaining unnamed " +
        "rows belong to Studios that opened no place, and no log field says which " +
        "row is which"
      : `${unnamedRows} connected mesh rows report no place name and ` +
        `${anchored.length} live play-test processes are anchored by -parentPid ` +
        `(pids ${pids}). The parent edge says which Studio started the test; it ` +
        "does not say which unnamed mesh row is the server and which are its " +
        "clients, so this is a choice between candidates rather than a join";

  return {
    resolved: false,
    reason,
    candidates: anchored.map((child) => child.pid),
    tree,
  };
}
