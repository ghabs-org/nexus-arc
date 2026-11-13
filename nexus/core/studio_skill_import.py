"""Import an Agent Skill (a folder with SKILL.md) from GitHub into the Studio catalog.

Accepts a repository/folder/file URL, ``owner/repo``, or a ``npx skills add`` command,
which is parsed and never executed. Only GitHub is contacted, through fixed hosts,
so a typed source can never point Studio at another address. A Studio skill is
text: SKILL.md plus the folder's Markdown references are imported while they fit;
scripts, data and media are listed as not imported.
"""

from __future__ import annotations

import posixpath
import re
import shlex
from urllib.parse import quote, urlparse

import requests
import yaml

API = "https://api.github.com"
RAW = "https://raw.githubusercontent.com"
_HOSTS = {"api.github.com", "raw.githubusercontent.com"}
_PART = re.compile(r"[A-Za-z0-9._-]{1,100}\Z")
CONTENT_LIMIT = 131072  # StudioCatalog's text limit for skill content
_MAX_CANDIDATES = 30


def parse_source(text: str) -> dict:
    """Return ``{owner, repo, ref, path, skill}`` from a URL, owner/repo or npx command."""
    if not isinstance(text, str) or not text.strip() or len(text) > 2000:
        raise ValueError("Paste a GitHub link or an `npx skills add` command")
    text = text.strip()
    skill = None
    if not re.match(r"(https?://|github\.com/)", text) and " " in text:
        try:
            words = shlex.split(text)
        except ValueError as exc:
            raise ValueError("Could not read that command") from exc
        if "add" not in words:
            raise ValueError("Only `skills add <source>` commands can be imported")
        rest = words[words.index("add") + 1 :]
        sources = []
        index = 0
        while index < len(rest):
            word = rest[index]
            if word in {"--skill", "-s"} and index + 1 < len(rest):
                skill = rest[index + 1]
                index += 2
                continue
            if word.startswith("--skill="):
                skill = word.split("=", 1)[1]
            elif not word.startswith("-"):
                sources.append(word)
            index += 1
        if len(sources) != 1:
            raise ValueError("The command must name exactly one skill source")
        text = sources[0]
    if text.startswith("github.com/"):
        text = "https://" + text
    if re.fullmatch(r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+", text):
        owner, repo, ref, path = *text.split("/"), None, None
    else:
        parsed = urlparse(text)
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in {
            "github.com",
            "www.github.com",
        }:
            raise ValueError("Only GitHub skill sources can be imported")
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) < 2:
            raise ValueError("The GitHub link must name a repository")
        owner, repo = parts[0], parts[1].removesuffix(".git")
        ref = path = None
        if len(parts) >= 4 and parts[2] in {"tree", "blob"}:
            ref, path = parts[3], "/".join(parts[4:]) or None
    if not (_PART.match(owner) and _PART.match(repo)) or ref is not None and not _PART.match(ref):
        raise ValueError("The GitHub owner, repository or branch name is not valid")
    if path and (".." in path.split("/") or len(path) > 500):
        raise ValueError("The folder path is not valid")
    return {"owner": owner, "repo": repo, "ref": ref, "path": path, "skill": skill_name(skill)}


def skill_name(value):
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9._ -]{1,100}", value.strip()):
        raise ValueError("The skill name is not valid")
    return value.strip()


def frontmatter(text: str) -> dict:
    match = re.match(r"---\s*\n(.*?)\n---\s*(\n|$)", text, re.S)
    if not match:
        return {}
    try:
        data = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


class GitHubSkillSource:
    """Read-only access to public GitHub repositories through fixed hosts."""

    def __init__(self, session=None, timeout=20):
        self.session = session or requests.Session()
        self.session.trust_env = False
        self.timeout = timeout

    def _get(self, url, *, accept="application/vnd.github+json", limit=4 * 1024 * 1024):
        try:
            with self.session.get(
                url,
                headers={"Accept": accept, "User-Agent": "nexus-studio-skill-import"},
                timeout=self.timeout,
                stream=True,
            ) as response:
                if urlparse(response.url).hostname not in _HOSTS:
                    raise ValueError("GitHub redirected outside GitHub")
                if response.status_code == 404:
                    raise ValueError("Not found on GitHub; check the link and that it is public")
                if response.status_code in {403, 429}:
                    raise ValueError("GitHub rate limit reached; try again in a few minutes")
                if response.status_code != 200:
                    raise ValueError(f"GitHub answered {response.status_code}")
                body = bytearray()
                for chunk in response.iter_content(65536):
                    body.extend(chunk)
                    if len(body) > limit:
                        raise ValueError("The GitHub response is too large")
                return bytes(body)
        except requests.RequestException as exc:
            raise ValueError("Could not reach GitHub") from exc

    def json(self, path):
        import json

        return json.loads(self._get(API + path))

    def commit(self, owner, repo, ref):
        if ref is None:
            ref = self.json(f"/repos/{owner}/{repo}")["default_branch"]
        sha = self._get(
            f"{API}/repos/{owner}/{repo}/commits/{quote(ref, safe='')}",
            accept="application/vnd.github.sha",
        ).decode().strip()
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise ValueError("Could not resolve the GitHub branch")
        return ref, sha

    def tree(self, owner, repo, sha):
        data = self.json(f"/repos/{owner}/{repo}/git/trees/{sha}?recursive=1")
        return [entry for entry in data.get("tree", []) if entry.get("type") == "blob"]

    def text(self, owner, repo, sha, path):
        url = f"{RAW}/{owner}/{repo}/{sha}/{quote(path)}"
        return self._get(url, accept="text/plain", limit=CONTENT_LIMIT).decode(
            "utf-8", errors="replace"
        )


class StudioSkillImporter:
    def __init__(self, studio, source_factory=GitHubSkillSource):
        self.studio = studio
        self.source_factory = source_factory

    def import_skill(self, payload: dict) -> dict:
        """Import one skill, or return ``choices`` when the source holds several."""
        spec = parse_source(payload.get("source"))
        if payload.get("skill"):
            spec["skill"] = skill_name(payload["skill"])
        github = self.source_factory()
        ref, sha = github.commit(spec["owner"], spec["repo"], spec["ref"])
        files = github.tree(spec["owner"], spec["repo"], sha)
        prefix = (spec["path"] or "").strip("/")
        if prefix.endswith("SKILL.md"):
            prefix = posixpath.dirname(prefix)
        candidates = [
            entry["path"]
            for entry in files
            if posixpath.basename(entry["path"]) == "SKILL.md"
            and (not prefix or entry["path"] == f"{prefix}/SKILL.md" or entry["path"].startswith(prefix + "/"))
        ]
        if not candidates:
            raise ValueError("No SKILL.md found at that location")
        documents = {}

        def read(path):
            if path not in documents:
                documents[path] = github.text(spec["owner"], spec["repo"], sha, path)
            return documents[path]

        raw_skills = payload.get("skills")
        if isinstance(raw_skills, str):
            raw_skills = [skill_name(s) for s in raw_skills.split(",") if s.strip()]
        elif isinstance(raw_skills, (list, tuple, set)):
            raw_skills = [skill_name(str(s)) for s in raw_skills if str(s).strip()]
        else:
            raw_skills = None

        if raw_skills:
            def resolve_skill_path(wanted):
                wanted = wanted.strip()
                if wanted in candidates:
                    return wanted
                folder = [p for p in candidates if posixpath.basename(posixpath.dirname(p)) == wanted]
                if folder:
                    return folder[0]
                for path in candidates[:_MAX_CANDIDATES]:
                    if frontmatter(read(path)).get("name") == wanted:
                        return path
                return None

            saved = []
            all_imported = []
            all_skipped = []
            any_updated = False
            for wanted in raw_skills:
                skill_path = resolve_skill_path(wanted)
                if not skill_path:
                    continue
                res = self._save(
                    spec, ref, sha, files, skill_path, read(skill_path), github, payload
                )
                saved.append(res["skill"])
                all_imported.extend(res["imported_files"])
                all_skipped.extend(res["skipped_files"])
                if res["updated"]:
                    any_updated = True

            if not saved:
                raise ValueError("None of the requested skills were found in that repository")

            return {
                "skills": saved,
                "skill": saved[0],
                "updated": any_updated,
                "imported_files": all_imported,
                "skipped_files": all_skipped,
                "count": len(saved),
            }

        chosen = None
        if spec["skill"]:
            wanted = spec["skill"].strip()
            folder = [p for p in candidates if posixpath.basename(posixpath.dirname(p)) == wanted]
            for path in folder + candidates[:_MAX_CANDIDATES]:
                if path in folder or frontmatter(read(path)).get("name") == wanted:
                    chosen = path
                    break
            if not chosen:
                raise ValueError(f"No skill named {wanted} in that repository")
        elif len(candidates) == 1:
            chosen = candidates[0]
        else:
            choices = []
            for path in candidates[:_MAX_CANDIDATES]:
                meta = frontmatter(read(path))
                choices.append(
                    {
                        "skill": str(meta.get("name") or posixpath.basename(posixpath.dirname(path))),
                        "description": str(meta.get("description") or "")[:300],
                        "path": path,
                    }
                )
            return {"choices": choices, "truncated": len(candidates) > _MAX_CANDIDATES}
        return self._save(spec, ref, sha, files, chosen, read(chosen), github, payload)

    def _save(self, spec, ref, sha, files, skill_path, skill_text, github, payload):
        meta = frontmatter(skill_text)
        folder = posixpath.dirname(skill_path)
        name = str(meta.get("name") or posixpath.basename(folder) or spec["repo"])[:200]
        description = " ".join(str(meta.get("description") or "").split())[:2000]
        content = skill_text
        imported, skipped = [skill_path], []
        bundled = sorted(
            (
                entry
                for entry in files
                if entry["path"] != skill_path
                and (not folder or entry["path"].startswith(folder + "/"))
            ),
            key=lambda entry: (entry["path"].count("/"), entry["path"]),
        )
        for entry in bundled:
            relative = entry["path"][len(folder) + 1 :] if folder else entry["path"]
            if not entry["path"].lower().endswith((".md", ".txt")):
                skipped.append(relative)
                continue
            section = f"\n\n---\n\n## Bundled file: {relative}\n\n"
            if len((content + section).encode()) + int(entry.get("size") or 0) > CONTENT_LIMIT - 4096:
                skipped.append(relative)
                continue
            content += section + github.text(spec["owner"], spec["repo"], sha, entry["path"])
            imported.append(entry["path"])
        if skipped:
            listed = "\n".join(f"- {path}" for path in skipped[:60])
            more = f"\n- …and {len(skipped) - 60} more" if len(skipped) > 60 else ""
            note = (
                "\n\n---\n\n## Not imported\n\nStudio skills are text. These files from the "
                "original skill were not imported, so steps that run them need a local "
                f"agent with the full skill installed:\n{listed}{more}\n"
            )
            if len((content + note).encode()) <= CONTENT_LIMIT:
                content += note
        source = {
            "type": "github",
            "repository": f"{spec['owner']}/{spec['repo']}",
            "ref": ref,
            "commit": sha,
            "path": skill_path,
        }
        identity = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "skill"
        catalog = self.studio.catalog
        existing = (
            catalog.get("skills", identity, archived=True)
            if catalog._path("skills", identity).exists()
            else None
        )
        record = catalog.save(
            "skills",
            {
                "id": identity,
                "name": name,
                "description": description,
                "data": {"content": content, "source": source},
                "revision": existing["revision"] if existing else 0,
                "requester_nexus_id": payload.get("requester_nexus_id"),
            },
        )
        return {
            "skill": record,
            "updated": bool(existing),
            "imported_files": imported,
            "skipped_files": skipped,
        }
