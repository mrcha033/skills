#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, unquote, urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


EXIT_NOT_EXHAUSTED = 20
EXIT_TERMINAL = 21
CHALLENGE_MARKERS = (
    "access denied",
    "just a moment",
    "checking your browser",
    "verify you are human",
    "cf-turnstile",
    "recaptcha",
    "hcaptcha",
    "sec-if-cpt",
)
AUTH_MARKERS = (
    "login required",
    "sign in to continue",
    "request access",
    "로그인이 필요",
    "로그인 후 이용",
    "액세스 권한 요청",
)


class DisallowedRedirectError(URLError):
    pass

BRIDGE = r"""
import json
import sys
from pathlib import Path

skill_dir = Path(sys.argv[1]).resolve()
url = sys.argv[2]
config = json.loads(sys.argv[3])
content_path = Path(sys.argv[4])
sys.path.insert(0, str(skill_dir))

from engine import fetch

result = fetch(
    url,
    success_selectors=config.get("selectors") or None,
    device_class=config.get("device", "auto"),
    timeout=int(config.get("timeout", 25)),
    max_attempts=None,
    enable_playwright=True,
    enable_phase0=True,
)
content_path.write_text(result.content or "", encoding="utf-8")
print(json.dumps(result.to_dict(), ensure_ascii=False))
"""


def _version_key(path: Path) -> tuple[int, ...]:
    numbers = re.findall(r"\d+", path.name)
    return tuple(int(number) for number in numbers) or (0,)


def _valid_engine_dir(path: Path) -> bool:
    return (path / "engine" / "__init__.py").is_file() and (path / "SKILL.md").is_file()


def locate_insane_search(explicit: str | None = None) -> tuple[Path | None, list[str]]:
    checked: list[str] = []
    direct = explicit or os.environ.get("INSANE_SEARCH_SKILL_DIR")
    if direct:
        candidate = Path(direct).expanduser().resolve()
        checked.append(str(candidate))
        return (candidate, checked) if _valid_engine_dir(candidate) else (None, checked)

    home = Path.home()
    claude_first = bool(os.environ.get("CLAUDECODE") or os.environ.get("CLAUDE_CODE_ENTRYPOINT"))
    codex_patterns = [
        home / ".codex/plugins/cache/gptaku-codex/insane-search-codex",
        home / ".codex/plugins/cache/gptaku-plugins/insane-search",
    ]
    claude_patterns = [home / ".claude/plugins/cache/gptaku-plugins/insane-search"]
    roots = (claude_patterns + codex_patterns) if claude_first else (codex_patterns + claude_patterns)
    for root in roots:
        checked.append(str(root))
        if not root.is_dir():
            continue
        versions = sorted((item for item in root.iterdir() if item.is_dir()), key=_version_key, reverse=True)
        for version in versions:
            candidate = version / "skills" / "insane-search"
            checked.append(str(candidate))
            if _valid_engine_dir(candidate):
                return candidate, checked

    for candidate in (
        home / ".codex/skills/insane-search",
        home / ".agents/skills/insane-search",
        home / ".claude/skills/insane-search",
    ):
        checked.append(str(candidate))
        if _valid_engine_dir(candidate):
            return candidate, checked
    return None, checked


def _candidate_interpreters(explicit: str | None = None) -> list[str]:
    raw = [
        explicit,
        os.environ.get("INSANE_SEARCH_PYTHON"),
        sys.executable,
        shutil.which("python3.14"),
        shutil.which("python3.13"),
        shutil.which("python3.12"),
        shutil.which("python3.11"),
        shutil.which("python3"),
    ]
    seen: set[str] = set()
    results: list[str] = []
    for item in raw:
        if not item:
            continue
        path = str(Path(item).expanduser())
        if path not in seen:
            seen.add(path)
            results.append(path)
    return results


def select_engine_python(explicit: str | None = None) -> tuple[str | None, list[dict[str, str]]]:
    checks: list[dict[str, str]] = []
    probe = (
        "import curl_cffi,bs4,yaml;"
        "v=tuple(int(x) for x in curl_cffi.__version__.split('.')[:2]);"
        "assert v >= (0,15), curl_cffi.__version__"
    )
    for interpreter in _candidate_interpreters(explicit):
        try:
            result = subprocess.run(
                [interpreter, "-c", probe],
                text=True,
                capture_output=True,
                timeout=15,
                check=False,
            )
        except Exception as exc:
            checks.append({"interpreter": interpreter, "error": f"{type(exc).__name__}: {exc}"})
            continue
        if result.returncode == 0:
            checks.append({"interpreter": interpreter, "status": "compatible"})
            return interpreter, checks
        detail = (result.stderr or result.stdout).strip().splitlines()
        checks.append(
            {
                "interpreter": interpreter,
                "status": "incompatible",
                "error": detail[-1][:300] if detail else f"exit {result.returncode}",
            }
        )
    return None, checks


class CandidateExtractor(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.candidates: list[dict[str, str]] = []
        self._anchor: dict[str, Any] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value or "" for key, value in attrs}
        if tag == "a" and values.get("href"):
            self._anchor = {"url": urljoin(self.base_url, values["href"]), "text": []}
        if tag == "meta" and values.get("content"):
            key = values.get("property") or values.get("name") or "meta"
            for url in extract_raw_urls(values["content"]):
                self.candidates.append({"url": urljoin(self.base_url, url), "label": key, "kind": "meta"})
        drive_id = values.get("data-id")
        if drive_id and re.fullmatch(r"[A-Za-z0-9_-]{15,}", drive_id):
            label = values.get("data-tooltip") or values.get("aria-label") or drive_id
            is_folder = "folder" in label.lower()
            if is_folder:
                url = f"https://drive.google.com/drive/folders/{drive_id}"
            else:
                url = f"https://drive.google.com/open?id={drive_id}"
            self.candidates.append({"url": url, "label": label, "kind": "drive_item"})

    def handle_data(self, data: str) -> None:
        if self._anchor is not None:
            self._anchor["text"].append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._anchor is not None:
            label = " ".join("".join(self._anchor["text"]).split())
            self.candidates.append(
                {"url": self._anchor["url"], "label": label, "kind": "anchor"}
            )
            self._anchor = None


class SelectorProbe(HTMLParser):
    def __init__(self, selectors: list[str]) -> None:
        super().__init__(convert_charrefs=True)
        self.selectors = selectors
        self.matched: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value or "" for key, value in attrs}
        for selector in self.selectors:
            match = re.fullmatch(
                r"([A-Za-z0-9_-]+)(?:\[([A-Za-z0-9_:-]+)(?:=[\"']?([^\"'\]]+)[\"']?)?\])?",
                selector.strip(),
            )
            if not match or tag.lower() != match.group(1).lower():
                continue
            attribute, expected = match.group(2), match.group(3)
            if attribute is None:
                self.matched.add(selector)
            elif attribute in values and (expected is None or values[attribute] == expected):
                self.matched.add(selector)


def selector_matches(content: str, selectors: list[str]) -> list[str]:
    if not selectors:
        return []
    probe = SelectorProbe(selectors)
    try:
        probe.feed(content)
    except Exception:
        pass
    return sorted(probe.matched)


def url_host_allowed(url: str, allowed_hosts: list[str]) -> bool:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    try:
        port = parsed.port
    except ValueError:
        return False
    return bool(
        parsed.scheme == "https"
        and port in {None, 443}
        and host
        and host in {item.lower() for item in allowed_hosts}
    )


class AllowlistedRedirectHandler(HTTPRedirectHandler):
    def __init__(self, allowed_hosts: list[str]) -> None:
        super().__init__()
        self.allowed_hosts = allowed_hosts

    def redirect_request(
        self,
        request: Request,
        file_pointer: Any,
        code: int,
        message: str,
        headers: Any,
        new_url: str,
    ) -> Request | None:
        target = urljoin(request.full_url, new_url)
        if not url_host_allowed(target, self.allowed_hosts):
            raise DisallowedRedirectError(
                f"redirect target is outside the HTTPS allowlist: {target}"
            )
        return super().redirect_request(
            request,
            file_pointer,
            code,
            message,
            headers,
            new_url,
        )


def encoded_public_url(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            quote(parsed.path, safe="/%:@"),
            quote(parsed.query, safe="=&%/:?"),
            parsed.fragment,
        )
    )


def stdlib_fetch(
    url: str,
    selectors: list[str],
    timeout: int,
    allowed_hosts: list[str] | None = None,
) -> tuple[dict[str, Any], str]:
    requested = encoded_public_url(url)
    allowed_hosts = allowed_hosts or [urlsplit(requested).hostname or ""]
    if not url_host_allowed(requested, allowed_hosts):
        raise ValueError(f"requested URL host is outside allowed_hosts: {requested}")
    request = Request(
        requested,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8",
        },
    )
    opener = build_opener(AllowlistedRedirectHandler(allowed_hosts))
    try:
        with opener.open(request, timeout=timeout) as response:
            data = response.read(12 * 1024 * 1024)
            status = int(getattr(response, "status", 200))
            final_url = response.geturl()
            charset = response.headers.get_content_charset() or "utf-8"
    except HTTPError as exc:
        data = exc.read(2 * 1024 * 1024)
        status = exc.code
        final_url = exc.geturl()
        charset = exc.headers.get_content_charset() or "utf-8"
    except DisallowedRedirectError as exc:
        metadata = {
            "ok": False,
            "final_url": requested,
            "verdict": "disallowed_redirect",
            "trace": [
                {
                    "phase": "phase0-public",
                    "executor": "urllib",
                    "url": requested,
                    "status": 0,
                    "body_size": 0,
                    "verdict": "disallowed_redirect",
                    "error": str(exc),
                }
            ],
            "summary": str(exc),
            "content_length": 0,
            "planned_attempts": 1,
            "executed_attempts": 1,
            "grid_exhausted": True,
            "stop_reason": "disallowed_redirect",
            "untried_routes": [],
            "must_invoke_playwright_mcp": False,
            "policy_terminal": True,
        }
        return metadata, ""
    except URLError as exc:
        metadata = {
            "ok": False,
            "final_url": requested,
            "verdict": "network_error",
            "profile_used": None,
            "trace": [
                {
                    "phase": "phase0-public",
                    "executor": "urllib",
                    "url": requested,
                    "status": 0,
                    "body_size": 0,
                    "verdict": "network_error",
                    "error": str(exc),
                }
            ],
            "summary": f"stdlib public probe failed: {exc}",
            "content_length": 0,
            "planned_attempts": 1,
            "executed_attempts": 1,
            "grid_exhausted": True,
            "stop_reason": "network_error",
            "untried_routes": ["insane-search adaptive engine"],
            "must_invoke_playwright_mcp": False,
        }
        return metadata, ""
    content = data.decode(charset, errors="replace")
    matched = selector_matches(content, selectors)
    selector_ok = not selectors or bool(matched)
    verdict = "strong_ok" if selectors and selector_ok else "weak_ok"
    ok = 200 <= status < 300 and bool(content)
    metadata = {
        "ok": ok,
        "final_url": final_url,
        "verdict": verdict if ok else f"http_{status}",
        "profile_used": None,
        "trace": [
            {
                "phase": "phase0-public",
                "executor": "urllib",
                "url": requested,
                "status": status,
                "body_size": len(data),
                "verdict": verdict if ok else f"http_{status}",
                "matched_selectors": matched,
            }
        ],
        "summary": (
            f"stdlib public route status={status} verdict={verdict} "
            f"final={final_url}"
        ),
        "content_length": len(content),
        "planned_attempts": 1,
        "executed_attempts": 1,
        "grid_exhausted": True,
        "stop_reason": "success" if ok else f"http_{status}",
        "untried_routes": [] if ok else ["insane-search adaptive engine"],
        "must_invoke_playwright_mcp": False,
    }
    return metadata, content


def decode_embedded_text(value: str) -> str:
    decoded = html.unescape(value).replace("\\/", "/")

    def unicode_replacement(match: re.Match[str]) -> str:
        return chr(int(match.group(1), 16))

    return re.sub(r"\\u([0-9a-fA-F]{4})", unicode_replacement, decoded)


def extract_raw_urls(value: str) -> list[str]:
    decoded = decode_embedded_text(value)
    matches = re.findall(r"https?://[^\s\"'<>\\\\]+", decoded)
    return [match.rstrip(".,);]}") for match in matches]


def marker_in_url(marker: str, url: str) -> bool:
    decoded_marker = unquote(decode_embedded_text(marker)).rstrip("/")
    decoded_url = unquote(decode_embedded_text(url))
    parsed = urlsplit(decoded_url)
    if decoded_marker.startswith(("http://", "https://")):
        expected = urlsplit(decoded_marker)
        return (
            parsed.scheme == expected.scheme
            and parsed.hostname == expected.hostname
            and parsed.path.rstrip("/") == expected.path.rstrip("/")
        )
    if re.fullmatch(r"[A-Za-z0-9_-]{15,}", decoded_marker):
        tokens = re.findall(r"[A-Za-z0-9_-]{15,}", parsed.path)
        tokens.extend(
            value
            for values in parse_qs(parsed.query).values()
            for value in values
            if re.fullmatch(r"[A-Za-z0-9_-]{15,}", value)
        )
        return decoded_marker in tokens
    if "=" in decoded_marker and "/" not in decoded_marker:
        key, expected_value = decoded_marker.split("=", 1)
        return expected_value in parse_qs(parsed.query).get(key, [])
    if "/" in decoded_marker and "." in decoded_marker.split("/", 1)[0]:
        expected = urlsplit(f"https://{decoded_marker}")
        return (
            parsed.hostname == expected.hostname
            and parsed.path.rstrip("/") == expected.path.rstrip("/")
        )
    if "/" not in decoded_marker and "." in decoded_marker:
        return parsed.hostname == decoded_marker
    return decoded_marker in decoded_url


def _terms(value: str) -> list[str]:
    return [term.lower() for term in re.findall(r"[0-9A-Za-z가-힣_·-]{2,}", value)]


def extract_candidates(content: str, base_url: str, query: str = "") -> list[dict[str, Any]]:
    parser = CandidateExtractor(base_url)
    try:
        parser.feed(content)
    except Exception:
        pass
    for url in extract_raw_urls(content):
        parser.candidates.append({"url": urljoin(base_url, url), "label": "", "kind": "raw_url"})
    if base_url:
        parser.candidates.append({"url": base_url, "label": "requested URL", "kind": "request"})

    tokens = _terms(query)
    unique: dict[str, dict[str, Any]] = {}
    for candidate in parser.candidates:
        url = decode_embedded_text(candidate["url"]).replace("&amp;", "&")
        if not url.startswith(("http://", "https://")):
            continue
        key = url.rstrip("/")
        label = " ".join(candidate.get("label", "").split())[:500]
        haystack = f"{label} {url}".lower()
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        score = sum(3 for token in tokens if token in haystack)
        score += 2 if "drive.google.com" in haystack or "docs.google.com" in haystack else 0
        score += 1 if candidate.get("kind") in {"anchor", "drive_item", "meta"} else 0
        score += 4 if host in {"bit.ly", "linktr.ee"} else 0
        score -= 1 if candidate.get("kind") == "raw_url" else 0
        if host.endswith(("cdninstagram.com", "gstatic.com", "googleusercontent.com")):
            score -= 6
        if re.search(r"\.(?:avif|gif|jpe?g|png|svg|webp)(?:$|[?#])", parsed.path, re.I):
            score -= 4
        row = {"url": url, "label": label, "kind": candidate.get("kind", "unknown"), "score": score}
        current = unique.get(key)
        if current is None or row["score"] > current["score"]:
            unique[key] = row
    return sorted(unique.values(), key=lambda row: (-row["score"], row["url"]))[:1000]


def filter_candidates(
    candidates: list[dict[str, Any]],
    allowed_hosts: list[str],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    accepted: list[dict[str, Any]] = []
    rejected_hosts: dict[str, int] = {}
    allowed = {host.lower() for host in allowed_hosts}
    for candidate in candidates:
        row = dict(candidate)
        parsed = urlsplit(str(row.get("url") or ""))
        host = (parsed.hostname or "").lower()
        try:
            port = parsed.port
        except ValueError:
            port = -1
        if (
            parsed.scheme == "http"
            and port in {None, 80}
            and host in allowed
        ):
            row["original_url"] = row["url"]
            row["url"] = urlunsplit(
                ("https", host, parsed.path, parsed.query, parsed.fragment)
            )
            row["normalized_to_https"] = True
        if url_host_allowed(str(row.get("url") or ""), allowed_hosts):
            accepted.append(row)
        else:
            rejected_hosts[host or "<invalid>"] = rejected_hosts.get(host or "<invalid>", 0) + 1
    accepted.sort(
        key=lambda row: (
            -int(row.get("score") or 0),
            0 if row.get("kind") in {"anchor", "drive_item", "meta"} else 1,
            str(row.get("url") or ""),
        )
    )
    return accepted[:250], dict(sorted(rejected_hosts.items()))


def run_engine(
    engine_dir: Path,
    interpreter: str,
    url: str,
    selectors: list[str],
    device: str,
    timeout: int,
) -> tuple[dict[str, Any], str]:
    config = json.dumps(
        {"selectors": selectors, "device": device, "timeout": timeout},
        ensure_ascii=False,
    )
    with tempfile.TemporaryDirectory(prefix="yonsei-adaptive-fetch-") as temporary:
        content_path = Path(temporary) / "content.txt"
        result = subprocess.run(
            [interpreter, "-c", BRIDGE, str(engine_dir), url, config, str(content_path)],
            text=True,
            capture_output=True,
            timeout=min(90, max(30, timeout * 4)),
            check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise RuntimeError(
                f"Insane Search bridge exited {result.returncode}: {detail[-2000:]}"
            )
        try:
            metadata = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Insane Search bridge returned invalid JSON: {exc}") from exc
        content = content_path.read_text(encoding="utf-8") if content_path.exists() else ""
    return metadata, content


def evaluate_result(
    metadata: dict[str, Any],
    content: str,
    required_any_text: list[str],
    allow_weak: bool,
    expected_final_url_contains: list[str],
) -> dict[str, Any]:
    decoded = decode_embedded_text(content)
    lowered = decoded.lower()
    challenge_observations = [marker for marker in CHALLENGE_MARKERS if marker in lowered]
    text_matches = [text for text in required_any_text if text.lower() in lowered]
    final_url = decode_embedded_text(str(metadata.get("final_url") or ""))
    final_matches = [
        expected
        for expected in expected_final_url_contains
        if marker_in_url(expected, final_url)
    ]
    engine_ok = bool(metadata.get("ok"))
    verdict = str(metadata.get("verdict") or "")
    verdict_ok = verdict == "strong_ok" or (allow_weak and verdict == "weak_ok")
    required_ok = not required_any_text or bool(text_matches)
    final_ok = not expected_final_url_contains or len(final_matches) == len(expected_final_url_contains)
    positive_proof = verdict_ok and required_ok and final_ok
    blocking_challenges = [] if positive_proof else challenge_observations
    accepted = engine_ok and verdict_ok and required_ok and final_ok and not blocking_challenges
    reasons: list[str] = []
    if not engine_ok:
        reasons.append("engine did not produce an accepted response")
    if engine_ok and not verdict_ok:
        reasons.append(f"verdict {verdict!r} is insufficient without --allow-weak")
    if not required_ok:
        reasons.append("none of the required governance text markers was present")
    if not final_ok:
        reasons.append("final redirect URL did not match every expected marker")
    if blocking_challenges:
        reasons.append("challenge markers remained in the response")
    return {
        "accepted": accepted,
        "engine_ok": engine_ok,
        "verdict": verdict,
        "required_text_matches": text_matches,
        "expected_final_url_matches": final_matches,
        "challenge_markers_observed": challenge_observations,
        "blocking_challenge_markers": blocking_challenges,
        "reasons": reasons,
    }


def failure_complete(metadata: dict[str, Any], validation: dict[str, Any]) -> bool:
    if validation["accepted"]:
        return True
    if metadata.get("ok"):
        return False
    return bool(
        metadata.get("grid_exhausted")
        and not metadata.get("untried_routes")
        and not metadata.get("must_invoke_playwright_mcp")
    )


def auth_is_terminal(
    metadata: dict[str, Any],
    content: str,
    validation: dict[str, Any],
) -> bool:
    statuses = [
        int(row.get("status") or 0)
        for row in metadata.get("trace", [])
        if str(row.get("status") or "").isdigit()
    ]
    if any(status in {401, 407} for status in statuses):
        return True
    if validation.get("required_text_matches"):
        return False
    lowered = decode_embedded_text(content).lower()
    return any(marker in lowered for marker in AUTH_MARKERS)


def write_text(path: str | None, value: str) -> None:
    if not path:
        return
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(value, encoding="utf-8")


def write_receipt(path: str | None, value: dict[str, Any]) -> None:
    if not path:
        return
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch and validate public governance sources with a deterministic public "
            "route and optional Insane Search fallback."
        )
    )
    parser.add_argument("--url")
    parser.add_argument("--selector", action="append", default=[])
    parser.add_argument("--require-any-text", action="append", default=[])
    parser.add_argument("--expected-final-url-contains", action="append", default=[])
    parser.add_argument("--query", default="")
    parser.add_argument("--device", choices=("auto", "desktop", "mobile"), default="auto")
    parser.add_argument("--timeout", type=int, default=25)
    parser.add_argument("--allow-weak", action="store_true")
    parser.add_argument("--output")
    parser.add_argument("--receipt")
    parser.add_argument("--engine-dir")
    parser.add_argument("--engine-python")
    parser.add_argument(
        "--allowed-host",
        action="append",
        default=[],
        help="Restrict the requested URL, redirects, and accepted final URL to this host.",
    )
    parser.add_argument("--locate-only", action="store_true")
    args = parser.parse_args()

    engine_dir, searched = locate_insane_search(args.engine_dir)
    interpreter, interpreter_checks = select_engine_python(args.engine_python)
    runtime = {
        "stdlib_ready": True,
        "engine_dir": str(engine_dir) if engine_dir else None,
        "engine_paths_checked": searched,
        "interpreter": interpreter,
        "interpreter_checks": interpreter_checks,
        "insane_search_ready": bool(engine_dir and interpreter),
    }
    if args.locate_only:
        receipt = {
            "schema_version": 1,
            "passed": True,
            "runtime": runtime,
            "degraded": not runtime["insane_search_ready"],
        }
        write_receipt(args.receipt, receipt)
        print(json.dumps(receipt, ensure_ascii=False, indent=2))
        return 0
    if not args.url:
        parser.error("--url is required unless --locate-only is used")
    requested_host = urlsplit(encoded_public_url(args.url)).hostname
    effective_allowed_hosts = list(
        dict.fromkeys(args.allowed_host or ([requested_host] if requested_host else []))
    )
    if not url_host_allowed(args.url, effective_allowed_hosts):
        parser.error(
            "--url must use HTTPS on the default port and its host must be allowed"
        )
    runtime["allowed_hosts"] = effective_allowed_hosts
    attempts: list[dict[str, Any]] = []
    try:
        metadata, content = stdlib_fetch(
            args.url,
            args.selector,
            args.timeout,
            effective_allowed_hosts,
        )
    except Exception as exc:
        metadata = {
            "ok": False,
            "grid_exhausted": True,
            "untried_routes": ["insane-search adaptive engine"],
            "must_invoke_playwright_mcp": False,
            "stop_reason": "stdlib_error",
            "summary": f"stdlib public probe failed: {type(exc).__name__}: {exc}",
        }
        content = ""
    validation = evaluate_result(
        metadata,
        content,
        args.require_any_text,
        args.allow_weak,
        args.expected_final_url_contains,
    )
    attempts.append(
        {
            "route": "stdlib-public",
            "metadata": metadata,
            "validation": validation,
        }
    )
    access_route = "stdlib-public"
    terminal_auth = auth_is_terminal(metadata, content, validation)
    terminal_policy = bool(metadata.get("policy_terminal"))
    engine_error: str | None = None
    if (
        not validation["accepted"]
        and not terminal_auth
        and not terminal_policy
        and engine_dir
        and interpreter
    ):
        try:
            engine_metadata, engine_content = run_engine(
                engine_dir,
                interpreter,
                args.url,
                args.selector,
                args.device,
                args.timeout,
            )
            engine_validation = evaluate_result(
                engine_metadata,
                engine_content,
                args.require_any_text,
                args.allow_weak,
                args.expected_final_url_contains,
            )
            if not url_host_allowed(
                str(engine_metadata.get("final_url") or ""),
                effective_allowed_hosts,
            ):
                engine_metadata = dict(engine_metadata)
                engine_metadata["ok"] = False
                engine_metadata["stop_reason"] = "disallowed_final_host"
                engine_metadata["policy_terminal"] = True
                engine_metadata["grid_exhausted"] = True
                engine_metadata["untried_routes"] = []
                engine_metadata["must_invoke_playwright_mcp"] = False
                engine_validation = evaluate_result(
                    engine_metadata,
                    engine_content,
                    args.require_any_text,
                    args.allow_weak,
                    args.expected_final_url_contains,
                )
            attempts.append(
                {
                    "route": "insane-search",
                    "metadata": engine_metadata,
                    "validation": engine_validation,
                }
            )
            metadata, content, validation = engine_metadata, engine_content, engine_validation
            access_route = "insane-search"
            terminal_auth = auth_is_terminal(metadata, content, validation)
            terminal_policy = bool(metadata.get("policy_terminal"))
        except Exception as exc:
            engine_error = f"{type(exc).__name__}: {exc}"

    if (terminal_auth or terminal_policy) and not validation["accepted"]:
        metadata = dict(metadata)
        metadata.update(
            {
                "ok": False,
                "grid_exhausted": True,
                "stop_reason": (
                    "auth_required" if terminal_auth else "disallowed_redirect"
                ),
                "untried_routes": [],
                "must_invoke_playwright_mcp": False,
            }
        )
    raw_candidates = extract_candidates(
        content,
        str(metadata.get("final_url") or args.url),
        " ".join([args.query, *args.require_any_text]),
    )
    candidates, rejected_candidate_hosts = filter_candidates(
        raw_candidates,
        effective_allowed_hosts,
    )
    complete = failure_complete(metadata, validation)
    untried = list(metadata.get("untried_routes") or [])
    if metadata.get("must_invoke_playwright_mcp"):
        untried.append("unauthenticated public-browser reconnaissance; never inject cookies")
    if metadata.get("ok") and not validation["accepted"]:
        untried.append("supply a positive selector/expected text or inspect a different official hop")
    if (
        not validation["accepted"]
        and not terminal_auth
        and not terminal_policy
        and not runtime["insane_search_ready"]
    ):
        untried.append("optional Insane Search fallback is unavailable")
        untried.append("unauthenticated public-browser reconnaissance")
    if engine_error:
        untried.append(f"repair or inspect optional Insane Search fallback: {engine_error}")
    receipt = {
        "schema_version": 1,
        "requested_url": args.url,
        "accepted": validation["accepted"],
        "runtime": runtime,
        "access_route": access_route,
        "attempts": attempts,
        "access": metadata,
        "engine": metadata,
        "validation": validation,
        "content": {
            "length": len(content),
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "output_path": str(Path(args.output).expanduser()) if args.output else None,
        },
        "candidates": candidates,
        "candidate_filter": {
            "allowed_hosts": effective_allowed_hosts,
            "raw_candidate_count": len(raw_candidates),
            "accepted_candidate_count": len(candidates),
            "rejected_hosts": rejected_candidate_hosts,
        },
        "failure_gate": {"complete": complete, "untried_routes": untried},
    }
    if validation["accepted"]:
        write_text(args.output, content)
    write_receipt(args.receipt, receipt)
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    if validation["accepted"]:
        return 0
    return EXIT_TERMINAL if complete else EXIT_NOT_EXHAUSTED


if __name__ == "__main__":
    sys.exit(main())
