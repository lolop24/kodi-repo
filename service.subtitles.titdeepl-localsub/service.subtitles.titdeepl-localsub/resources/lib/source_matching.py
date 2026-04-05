# -*- coding: utf-8 -*-
import os


def normalize_name(name):
    keep = []
    for char in (name or "").lower():
        if char.isalnum():
            keep.append(char)
        else:
            keep.append(" ")
    return " ".join("".join(keep).split())


def looks_like_related_subtitle(sub_name, video_name):
    sub_stem = os.path.splitext(sub_name)[0]
    video_stem = os.path.splitext(video_name)[0]
    norm_sub = normalize_name(sub_stem)
    norm_video = normalize_name(video_stem)
    if not norm_sub or not norm_video:
        return False
    if norm_sub == norm_video:
        return True
    if norm_sub.startswith(norm_video + " "):
        return True
    return False


def build_source_score(sub_name, video_name="", current_names=None):
    norm_sub = normalize_name(os.path.splitext(sub_name)[0])
    current_tokens = []
    for value in current_names or []:
        token = normalize_name(os.path.splitext(value)[0])
        if token:
            current_tokens.append(token)

    current_match = any(
        token == norm_sub or token in norm_sub or norm_sub in token
        for token in current_tokens
        if norm_sub
    )
    related = looks_like_related_subtitle(sub_name, video_name)
    has_cs_sk_hint = any(token in norm_sub.split() for token in ("cs", "cz", "sk", "czech", "slovak"))
    return (
        1 if current_match else 0,
        1 if related else 0,
        1 if has_cs_sk_hint else 0,
        norm_sub,
    )
