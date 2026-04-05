# -*- coding: utf-8 -*-
import os
import re


SUPPORTED_EXTENSIONS = (".srt", ".vtt", ".ass", ".ssa")
READ_ENCODINGS = (
    "utf-8-sig",
    "utf-16",
    "windows-1250",
    "iso-8859-2",
    "windows-1251",
    "iso-8859-5",
    "utf-8",
    "latin-1",
)


LANGUAGE_NAME_HINTS = {
    "cs": ("czech", "ces", "cze", ".cs.", "_cs_", "-cs-", " cz "),
    "sk": ("slovak", "slk", "slo", ".sk.", "_sk_", "-sk-", " sk "),
    "uk": ("ukrainian", "ukr", ".uk.", "_uk_", "-uk-", " ua ", ".ua."),
}


CS_MARKERS = ("\u011b", "\u0159", "\u016f")
SK_MARKERS = ("\u00e4", "\u013a", "\u013e", "\u0155", "\u00f4", "d\u017e", "dz")
PROTECTED_RE = re.compile(r"(\{[^}]*\}|<[^>]+>|\\[Nnh]|&[A-Za-z0-9#]+;)")


def guess_language_from_name(name):
    lowered = " %s " % (name or "").lower()
    for code, hints in LANGUAGE_NAME_HINTS.items():
        if any(hint in lowered for hint in hints):
            return code
    return None


def detect_language(text):
    sample = (text or "").lower()
    cs_score = sum(sample.count(marker) for marker in CS_MARKERS)
    sk_score = sum(sample.count(marker) for marker in SK_MARKERS)
    if sk_score > cs_score:
        return "sk"
    return "cs"


def safe_stem(path):
    stem = os.path.splitext(os.path.basename(path))[0]
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-")
    return stem or "subtitle"


def read_text_file(path):
    with open(path, "rb") as handle:
        raw = handle.read()
    for encoding in READ_ENCODINGS:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _is_srt_counter_line(line):
    return bool(re.fullmatch(r"\d+", line.strip()))


def _is_timestamp_line(line):
    return "-->" in line


def _has_translatable_text(text):
    return any(char.isalpha() for char in text or "")


def _split_padding(text):
    match = re.match(r"^(\s*)(.*?)(\s*)$", text, re.S)
    if not match:
        return "", text, ""
    return match.group(1), match.group(2), match.group(3)


def _split_line_ending(text):
    if text.endswith("\r\n"):
        return text[:-2], "\r\n"
    if text.endswith("\n") or text.endswith("\r"):
        return text[:-1], text[-1]
    return text, ""


def _queue_protected_translation(text, jobs, source_language):
    parts = PROTECTED_RE.split(text)
    plan = []
    for part in parts:
        if not part:
            continue
        if PROTECTED_RE.fullmatch(part):
            plan.append(("literal", part))
            continue

        prefix, core, suffix = _split_padding(part)
        if prefix:
            plan.append(("literal", prefix))
        if core and _has_translatable_text(core):
            jobs.append({"text": core})
            plan.append(("job", len(jobs) - 1))
        elif core:
            plan.append(("literal", core))
        if suffix:
            plan.append(("literal", suffix))
    return plan


def _render_plan(plan, jobs):
    out = []
    for kind, value in plan:
        if kind == "literal":
            out.append(value)
        else:
            out.append(jobs[value]["translated"])
    return "".join(out)


def _translate_jobs(jobs, translate_batch, source_language):
    if not jobs:
        return
    translated = translate_batch([job["text"] for job in jobs], source_language)
    if len(translated) != len(jobs):
        raise ValueError("DeepL returned an unexpected translation count")
    for job, value in zip(jobs, translated):
        job["translated"] = value


def _parse_srt_timestamp(ts):
    """Parse SRT/VTT timestamp like '00:01:23,456' or '00:01:23.456' to milliseconds."""
    ts = ts.strip().replace(",", ".")
    parts = ts.split(":")
    if len(parts) == 3:
        h, m, rest = parts
        s_parts = rest.split(".")
        s = s_parts[0]
        ms = s_parts[1] if len(s_parts) > 1 else "0"
        ms = ms.ljust(3, "0")[:3]
        return int(h) * 3600000 + int(m) * 60000 + int(s) * 1000 + int(ms)
    return 0


def _parse_srt_events(content):
    """Parse SRT/VTT content into list of (start_ms, end_ms, text) events."""
    # Normalize line endings (handles \r\r\n, \r\n, \r)
    content = content.replace("\r\n", "\n").replace("\r", "\n")

    # Detect double-spaced SRT (blank line between every line)
    # by checking if there's a blank line between timestamp and text
    lines_raw = content.split("\n")
    double_spaced = False
    for k, ln in enumerate(lines_raw):
        if _is_timestamp_line(ln.strip()):
            if k + 1 < len(lines_raw) and not lines_raw[k + 1].strip():
                if k + 2 < len(lines_raw) and lines_raw[k + 2].strip():
                    double_spaced = True
            break

    if double_spaced:
        # Remove every other blank line to normalize structure
        compacted = []
        prev_blank = False
        for ln in lines_raw:
            if not ln.strip():
                if not prev_blank:
                    prev_blank = True
                    continue  # skip first blank
                else:
                    compacted.append("")  # keep double blank (block separator)
                    prev_blank = False
            else:
                compacted.append(ln)
                prev_blank = False
        lines = compacted
    else:
        lines = lines_raw

    events = []
    i = 0
    # Skip VTT header
    while i < len(lines) and not _is_timestamp_line(lines[i].strip()):
        i += 1

    while i < len(lines):
        line = lines[i].strip()
        if not _is_timestamp_line(line):
            i += 1
            continue

        # Parse timestamp
        arrow_idx = line.index("-->")
        start_ts = line[:arrow_idx].strip()
        end_ts = line[arrow_idx + 3:].strip()
        start_ms = _parse_srt_timestamp(start_ts)
        end_ms = _parse_srt_timestamp(end_ts)
        i += 1

        # Collect text lines until empty line or next counter/timestamp
        text_lines = []
        while i < len(lines):
            tl = lines[i]
            if not tl.strip():
                break
            if _is_timestamp_line(tl):
                break
            if _is_srt_counter_line(tl) and i + 1 < len(lines) and _is_timestamp_line(lines[i + 1]):
                break
            text_lines.append(tl.strip())
            i += 1

        if text_lines:
            events.append((start_ms, end_ms, "\n".join(text_lines)))

    return events


def _parse_ass_events(content):
    """Parse ASS/SSA content into list of (start_ms, end_ms, text) events."""
    events = []
    for line in content.splitlines():
        if not line.startswith("Dialogue:"):
            continue
        try:
            _, payload = line.split(":", 1)
        except ValueError:
            continue
        parts = payload.split(",", 9)
        if len(parts) < 10:
            continue
        start_ms = _parse_ass_timestamp(parts[1].strip())
        end_ms = _parse_ass_timestamp(parts[2].strip())
        text = parts[9].strip()
        # Strip ASS tags for translation but keep text
        if text:
            events.append((start_ms, end_ms, text))
    return events


def _parse_ass_timestamp(ts):
    """Parse ASS timestamp like '0:01:23.45' to milliseconds."""
    parts = ts.split(":")
    if len(parts) == 3:
        h, m, rest = parts
        s_parts = rest.split(".")
        s = s_parts[0]
        cs = s_parts[1] if len(s_parts) > 1 else "0"
        cs = cs.ljust(2, "0")[:2]
        return int(h) * 3600000 + int(m) * 60000 + int(s) * 1000 + int(cs) * 10
    return 0


def _ms_to_ass_timestamp(ms):
    """Convert milliseconds to ASS timestamp format '0:00:00.00'."""
    if ms < 0:
        ms = 0
    h = ms // 3600000
    ms %= 3600000
    m = ms // 60000
    ms %= 60000
    s = ms // 1000
    cs = (ms % 1000) // 10
    return "%d:%02d:%02d.%02d" % (h, m, s, cs)


def _strip_tags(text):
    """Strip ASS override tags and HTML tags from text, keep \\N as newline."""
    text = re.sub(r"\\[Nn]", "\n", text)
    text = re.sub(r"\{[^}]*\}", "", text)
    text = re.sub(r"<[^>]+>", "", text)
    return text.strip()


def _escape_ass_text(text):
    """Escape text for ASS dialogue: newlines become \\N."""
    return text.replace("\n", "\\N").replace("\r", "")


# ASS header template for dual subtitle output
_ASS_HEADER = """\
[Script Info]
Title: DeepL Bilingual Subtitles
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: top-style,Arial,54,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,2,1,8,20,20,25,1
Style: bottom-style,Arial,54,&H0000FFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,2,1,2,20,20,25,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _generate_dual_ass(events, translated_events):
    """Generate ASS content with original on top (white) and translation on bottom (yellow)."""
    out = [_ASS_HEADER]
    # Color override tags to force colors even with Kodi system overrides
    top_ctag = "{\\1c&HFFFFFF&}"     # white (BGR)
    bottom_ctag = "{\\1c&H00FFFF&}"  # yellow (BGR)

    for (start_ms, end_ms, original), translated in zip(events, translated_events):
        start = _ms_to_ass_timestamp(start_ms)
        end = _ms_to_ass_timestamp(end_ms)
        orig_ass = _escape_ass_text(original)
        trans_ass = _escape_ass_text(translated)

        # Original text on top (white)
        out.append("Dialogue: 0,%s,%s,top-style,,0,0,0,,%s%s\n" % (
            start, end, top_ctag, orig_ass))
        # Translation on bottom (yellow)
        out.append("Dialogue: 0,%s,%s,bottom-style,,0,0,0,,%s%s\n" % (
            start, end, bottom_ctag, trans_ass))

    return "".join(out)


def translate_subtitle_to_dual_ass(content, extension, translate_batch, source_language="auto"):
    """Parse any subtitle format, translate, and produce dual ASS output."""
    # Parse events from source
    if extension in (".ass", ".ssa"):
        events = _parse_ass_events(content)
    else:
        events = _parse_srt_events(content)

    if not events:
        return content  # fallback: return as-is

    # Prepare texts for translation (strip tags for clean translation)
    jobs = []
    for start_ms, end_ms, text in events:
        clean = _strip_tags(text)
        jobs.append(clean if _has_translatable_text(clean) else "")

    # Translate all at once
    translatable = [t for t in jobs if t]
    if not translatable:
        return content

    translated_texts = translate_batch(translatable, source_language)

    # Map translations back
    trans_iter = iter(translated_texts)
    translated_events = []
    for text in jobs:
        if text:
            translated_events.append(next(trans_iter))
        else:
            translated_events.append("")

    return _generate_dual_ass(events, translated_events)


def detect_language_for_file(path, content, forced_language):
    if forced_language in ("cs", "sk"):
        return forced_language
    name_guess = guess_language_from_name(os.path.basename(path))
    if name_guess:
        return name_guess
    return detect_language(content)


def generate_translated_subtitle(
    source_path,
    output_dir,
    translator,
    source_language="auto",
    target_language="UK",
    source_name_hint=None,
    output_name_hint=None,
    output_path=None,
):
    extension = os.path.splitext(source_path)[1].lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise ValueError("Unsupported subtitle format: %s" % extension)

    content = read_text_file(source_path)
    detected_language = detect_language_for_file(source_name_hint or source_path, content, source_language)

    # Always produce dual ASS output (original top + translation bottom)
    translated = translate_subtitle_to_dual_ass(
        content,
        extension,
        lambda texts, lang: translator.translate_texts(
            texts,
            source_lang=lang.upper() if lang else None,
            target_lang=target_language,
        ),
        detected_language,
    )

    os.makedirs(output_dir, exist_ok=True)
    target_path = output_path
    if not target_path:
        target_name = "%s.uk.deepl.ass" % safe_stem(output_name_hint or source_path)
        target_path = os.path.join(output_dir, target_name)
    # Force .ass extension on output path
    if not target_path.lower().endswith(".ass"):
        target_path = os.path.splitext(target_path)[0] + ".ass"
    with open(target_path, "w", encoding="utf-8", newline="") as handle:
        handle.write(translated)

    return {
        "path": target_path,
        "detected_language": detected_language,
        "extension": ".ass",
    }


def _parse_any_events(path):
    """Read a subtitle file and parse events from any supported format."""
    content = read_text_file(path)
    ext = os.path.splitext(path)[1].lower()
    if ext in (".ass", ".ssa"):
        return _parse_ass_events(content)
    return _parse_srt_events(content)


def merge_two_subtitles_to_dual_ass(
    original_path,
    ukrainian_path,
    output_path,
):
    """Merge two subtitle files into a dual ASS: original on top (white), Ukrainian on bottom (yellow).

    This is used when Ukrainian subtitles are downloaded from OpenSubtitles
    and need to be combined with the original CZ/SK subtitles.

    Args:
        original_path: Path to the original CZ/SK subtitle file.
        ukrainian_path: Path to the Ukrainian subtitle file (from OS or DeepL).
        output_path: Path where the dual ASS output will be written.

    Returns:
        Path to the output ASS file.
    """
    top_events = _parse_any_events(original_path)
    bottom_events = _parse_any_events(ukrainian_path)

    if not top_events and not bottom_events:
        raise ValueError("Both subtitle files are empty or unparseable")

    out = [_ASS_HEADER]
    top_ctag = "{\\1c&HFFFFFF&}"     # white
    bottom_ctag = "{\\1c&H00FFFF&}"  # yellow

    # Write top (original) events
    for start_ms, end_ms, text in top_events:
        start = _ms_to_ass_timestamp(start_ms)
        end = _ms_to_ass_timestamp(end_ms)
        clean = _strip_tags(text)
        ass_text = _escape_ass_text(clean)
        out.append("Dialogue: 0,%s,%s,top-style,,0,0,0,,%s%s\n" % (
            start, end, top_ctag, ass_text))

    # Write bottom (Ukrainian) events
    for start_ms, end_ms, text in bottom_events:
        start = _ms_to_ass_timestamp(start_ms)
        end = _ms_to_ass_timestamp(end_ms)
        clean = _strip_tags(text)
        ass_text = _escape_ass_text(clean)
        out.append("Dialogue: 0,%s,%s,bottom-style,,0,0,0,,%s%s\n" % (
            start, end, bottom_ctag, ass_text))

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    if not output_path.lower().endswith(".ass"):
        output_path = os.path.splitext(output_path)[0] + ".ass"
    with open(output_path, "w", encoding="utf-8", newline="") as handle:
        handle.write("".join(out))

    return output_path
