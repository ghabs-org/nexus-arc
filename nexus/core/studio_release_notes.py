"""Bundled, versioned Studio history; never inferred from a live workspace or Git."""

import json
import re
from datetime import date
from pathlib import Path

CATALOG = Path(__file__).resolve().parents[1] / "studio_static" / "release-notes.json"
VERSION = re.compile(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?")


def release_history():
    """Read only shipped public product notes, including explicitly unverified history."""
    data = json.loads(CATALOG.read_text(encoding="utf-8"))
    if data.get("format") != "nexus-studio-release-notes/v1":
        raise ValueError("Unsupported Studio release-notes format")
    seen = set()
    entries = data.get("entries", [])
    if not 1 <= len(entries) <= 100:
        raise ValueError("Studio release notes require 1–100 version entries")
    for entry in entries:
        version = entry.get("version", "")
        if (version != "Unreleased" and not VERSION.fullmatch(version)) or version in seen:
            raise ValueError("Release-note versions must be valid and unique")
        seen.add(version)
        if entry.get("date") is not None:
            date.fromisoformat(entry["date"])
        for field in ("changes", "limitations", "evidence"):
            values = entry.get(field)
            if (
                not isinstance(values, list)
                or len(values) > 30
                or any(
                    not isinstance(value, str) or not value.strip() or len(value) > 1000
                    for value in values
                )
            ):
                raise ValueError("Release-note lists require bounded nonempty text")
        if (
            entry.get("status")
            not in {
                "unreleased",
                "local-release",
                "local-install",
                "local-build",
                "beta-checkpoint",
                "unverified",
            }
            or not isinstance(entry.get("title"), str)
            or not entry["title"].strip()
        ):
            raise ValueError("Release notes require a title and explicit evidence status")
    if data.get("source_version") not in seen:
        raise ValueError("Studio source version needs a release-note entry")
    return data


def entry_markdown(entry):
    heading = f"## {entry['version']} — {entry['title']}"
    timing = entry["date"] or "Date not established"
    lines = [heading, f"{timing} · {entry['status']}", ""]
    lines.extend(f"- {change}" for change in entry["changes"])
    if entry["limitations"]:
        lines.extend(["", "Recorded limits:", ""])
        lines.extend(f"- {limit}" for limit in entry["limitations"])
    return "\n".join(lines)


def require_release_notes(version):
    """Fail before packaging/signing an undocumented version; return updater-ready notes."""
    entry = next((e for e in release_history()["entries"] if e["version"] == version), None)
    if not entry or entry["status"] in {"unreleased", "unverified"} or not entry["changes"]:
        raise ValueError(
            f"Add source-controlled Studio release notes for {version} before building"
        )
    return entry_markdown(entry)


def is_release_question(question):
    return bool(
        re.search(
            r"release|changelog|\bversions?\b|\d+\.\d+\.\d+|what.{0,3}s new|new features|newest|latest|novit|aggiornament",
            question,
            re.I,
        )
    )


def release_answer(question):
    data = release_history()
    versions = set(re.findall(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?", question))
    entries = (
        [e for e in data["entries"] if e["version"] in versions]
        if versions
        else data["entries"][:3]
    )
    if re.search(r"history|past|previous|all releases|storico", question, re.I) and not versions:
        entries = data["entries"]
    missing = versions - {e["version"] for e in entries}
    text = [data["notice"]]
    text.extend(f"No recorded release notes for {version}." for version in sorted(missing))
    text.extend(entry_markdown(entry) for entry in entries)
    return {
        "answer": "\n\n".join(text),
        "sources": [
            {
                "id": "release-" + e["version"],
                "title": "Studio " + e["version"],
                "url": "/release-notes.html#" + e["version"],
            }
            for e in entries
        ]
        or [{"id": "releases", "title": "Studio release history", "url": "/release-notes.html"}],
        "release_notes": {
            "source_version": data["source_version"],
            "installed_version": None,
            "entries": entries,
        },
    }
