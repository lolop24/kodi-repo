# -*- coding: utf-8 -*-
"""Helpers for exporting Ukrainian lines from dual ASS subtitles."""

import os


class UploadError(Exception):
    pass


def extract_srt_from_ass(ass_path, output_path=None):
    """Extract the Ukrainian (bottom-style) lines from dual ASS back to SRT for upload.

    OpenSubtitles expects plain SRT, not dual ASS format.
    Returns path to generated SRT file.
    """
    if output_path is None:
        base = os.path.splitext(ass_path)[0]
        output_path = base + ".upload.srt"

    events = []
    with open(ass_path, "r", encoding="utf-8") as fh:
        for line in fh:
            if not line.startswith("Dialogue:"):
                continue
            # Only bottom-style lines (Ukrainian translation)
            if ",bottom-style," not in line:
                continue
            parts = line.split(",", 9)
            if len(parts) < 10:
                continue
            start = parts[1].strip()
            end = parts[2].strip()
            text = parts[9].strip()
            # Remove color override tags
            import re
            text = re.sub(r"\{[^}]*\}", "", text)
            # Convert \N back to newlines
            text = text.replace("\\N", "\n").replace("\\n", "\n")
            events.append((start, end, text))

    if not events:
        raise UploadError("No Ukrainian subtitle lines found in ASS file")

    with open(output_path, "w", encoding="utf-8") as fh:
        for idx, (start, end, text) in enumerate(events, 1):
            # Convert ASS timestamps (0:00:00.00) to SRT (00:00:00,000)
            srt_start = _ass_to_srt_timestamp(start)
            srt_end = _ass_to_srt_timestamp(end)
            fh.write("%d\n%s --> %s\n%s\n\n" % (idx, srt_start, srt_end, text))

    return output_path


def _ass_to_srt_timestamp(ass_ts):
    """Convert '0:01:23.45' to '00:01:23,450'."""
    parts = ass_ts.split(":")
    if len(parts) == 3:
        h = parts[0].zfill(2)
        m = parts[1].zfill(2)
        s_parts = parts[2].split(".")
        s = s_parts[0].zfill(2)
        cs = s_parts[1] if len(s_parts) > 1 else "0"
        ms = cs.ljust(3, "0")[:3]
        return "%s:%s:%s,%s" % (h, m, s, ms)
    return ass_ts
