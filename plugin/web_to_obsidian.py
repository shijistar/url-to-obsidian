"""Safe vault-writing and Git synchronization for the ``/clip`` command."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import signal
import socket
import ssl
import subprocess
import tempfile
import threading
import tomllib
import unicodedata
from urllib.parse import quote, urljoin, urlsplit, urlunsplit
from typing import Mapping, Sequence

import yaml


EXTRACTOR_TIMEOUT = 90
GIT_TIMEOUT = 120
MAX_STDOUT_BYTES = 12 * 1024 * 1024
MAX_STDERR_BYTES = 64 * 1024
MAX_GIT_OUTPUT_BYTES = 2 * 1024 * 1024
MAX_FILENAME_BYTES = 180
MAX_DESTINATION_SCAN_ENTRIES = 10_000
MAX_IMAGE_BYTES = 15 * 1024 * 1024
PENDING_TTL_SECONDS = 60 * 60
DOS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_FORBIDDEN_FILENAME = set('<>:"/\\|?*')
_SAFE_ERROR_CODE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_SAFE_BRANCH_NAME = re.compile(r"^[A-Za-z0-9._/-]{1,200}$")
_REMOTE_MARKDOWN_IMAGE = re.compile(r"!\[[^\]]*\]\((https?://[^\s)]+)(?:\s+\"[^\"]*\")?\)", re.IGNORECASE)
_GITHUB_REMOTE_HTTPS = re.compile(r"^https://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$")
_GITHUB_REMOTE_SSH = re.compile(r"^git@github\.com:([^/]+)/([^/]+?)(?:\.git)?$")
_REMOTE_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_REMOTE_FETCH_POLICY_MESSAGE = "The remote image URL is blocked by network policy."
_BLOCKED_IMAGE_IPV4_NETWORKS = tuple(
    ipaddress.ip_network(cidr)
    for cidr in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "100.64.0.0/10",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.0.0.0/24",
        "192.0.2.0/24",
        "192.88.99.0/24",
        "192.168.0.0/16",
        "198.18.0.0/15",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "224.0.0.0/4",
        "240.0.0.0/4",
    )
)
_BLOCKED_IMAGE_IPV6_NETWORKS = tuple(
    ipaddress.ip_network(cidr)
    for cidr in (
        "::/96",
        "::/128",
        "::1/128",
        "64:ff9b::/96",
        "64:ff9b:1::/48",
        "100::/64",
        "2001::/23",
        "2001:2::/48",
        "2001:10::/28",
        "2001:20::/28",
        "2001:db8::/32",
        "2002::/16",
        "3fff::/20",
        "5f00::/16",
        "fc00::/7",
        "fe80::/10",
        "fec0::/10",
        "ff00::/8",
    )
)


class ClipError(Exception):
    """An expected error whose message is safe to show to the user."""


@dataclass(frozen=True)
class ClipOptions:
    url: str
    no_browser: bool
    no_git: bool
    refresh: bool
    save_images: str = "ask"


@dataclass(frozen=True)
class ClipConfig:
    vault: Path
    destination: Path
    images: Path
    sync_branch: str
    lock_file: Path

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "ClipConfig":
        values = os.environ if env is None else env
        vault = Path(
            values.get("WEB_TO_OBSIDIAN_VAULT", "~/obsidian/shijistar")
        ).expanduser().resolve()
        if not vault.is_dir():
            raise ClipError("The configured Obsidian vault does not exist.")

        destination = _resolve_vault_path(
            vault, values.get("WEB_TO_OBSIDIAN_DEST", "Inbox")
        )
        images = _resolve_vault_path(
            vault, values.get("WEB_TO_OBSIDIAN_IMAGES", "images")
        )
        sync_branch = values.get("WEB_TO_OBSIDIAN_SYNC_BRANCH", "master").strip()
        if not sync_branch or not _SAFE_BRANCH_NAME.fullmatch(sync_branch):
            raise ClipError("The configured Git sync branch is unsafe.")
        default_lock = Path(
            "~/.hermes/workspace/cache/url-to-obsidian/vault.lock"
        ).expanduser()
        lock_file = Path(
            values.get("WEB_TO_OBSIDIAN_LOCK_FILE", str(default_lock))
        ).expanduser().resolve()
        if lock_file == vault or vault in lock_file.parents:
            raise ClipError("The shared lock file must be outside the Obsidian vault.")
        return cls(
            vault=vault,
            destination=destination,
            images=images,
            sync_branch=sync_branch,
            lock_file=lock_file,
        )

    @classmethod
    def from_file(cls, path: Path) -> "ClipConfig":
        if not path.is_file():
            return cls.from_env()
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
            raise ClipError("The plugin config.toml is invalid.") from exc
        section = data.get("clip")
        if not isinstance(section, dict):
            raise ClipError("The plugin config.toml must contain a [clip] table.")
        supported = {"vault", "destination", "images", "sync_branch", "lock_file", "pending_root"}
        if set(section) - supported or any(
            not isinstance(value, str) for value in section.values()
        ):
            raise ClipError("The plugin config.toml contains unsupported values.")
        mapping = {
            "WEB_TO_OBSIDIAN_VAULT": section.get("vault", "~/obsidian/shijistar"),
            "WEB_TO_OBSIDIAN_DEST": section.get("destination", "Inbox"),
            "WEB_TO_OBSIDIAN_IMAGES": section.get("images", "images"),
            "WEB_TO_OBSIDIAN_SYNC_BRANCH": section.get("sync_branch", "master"),
        }
        if "lock_file" in section:
            mapping["WEB_TO_OBSIDIAN_LOCK_FILE"] = section["lock_file"]
        if "pending_root" in section:
            mapping["WEB_TO_OBSIDIAN_PENDING_ROOT"] = section["pending_root"]
        return cls.from_env(mapping)


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    stdout: bytes
    stderr: bytes


@dataclass(frozen=True)
class GitOutcome:
    commit_state: str
    push_state: str
    detail: str = ""


@dataclass(frozen=True)
class ClipResult:
    path: str
    commit_state: str
    push_state: str
    github_url: str | None = None

    def user_message(self) -> str:
        if self.commit_state == "disabled":
            message = f"Saved clip: {self.path} (Git synchronization disabled)."
            return f"{message} GitHub: {self.github_url}" if self.github_url else message
        if self.commit_state == "committed" and self.push_state == "pushed":
            message = f"Saved clip: {self.path} (committed and pushed)."
            return f"{message} GitHub: {self.github_url}" if self.github_url else message
        if self.commit_state == "unchanged":
            message = f"Saved clip: {self.path} (content unchanged; no Git commit needed)."
            return f"{message} GitHub: {self.github_url}" if self.github_url else message
        if self.commit_state == "committed":
            message = f"Saved clip: {self.path} (committed; push failed)."
            return f"{message} GitHub: {self.github_url}" if self.github_url else message
        if self.commit_state == "committed_unverified":
            return (
                f"Saved clip: {self.path} "
                "(local commit created, but post-commit verification failed; not pushed)."
            )
        if self.commit_state == "commit_failed":
            return f"Saved clip: {self.path} (Git commit failed; not pushed)."
        if self.commit_state == "stage_failed":
            return f"Saved clip: {self.path} (Git staging failed; not committed or pushed)."
        return (
            f"Saved clip: {self.path} "
            "(Git safety verification refused synchronization; not committed or pushed)."
        )


@dataclass(frozen=True)
class PendingClipResult:
    title: str
    image_count: int

    def user_message(self) -> str:
        noun = "image" if self.image_count == 1 else "images"
        return (
            f"Found {self.image_count} remote {noun} in '{self.title}'. "
            "Reply yes or no: yes downloads and localizes them, no keeps the remote image URLs."
        )


@dataclass(frozen=True)
class PendingClipState:
    pending_id: str
    created_at: str
    expires_at: str
    article: Mapping[str, object]
    refresh: bool
    no_git: bool
    vault: str
    destination: str
    images: str
    sync_branch: str

    def to_json(self) -> dict[str, object]:
        return {
            "pending_id": self.pending_id,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "article": dict(self.article),
            "refresh": self.refresh,
            "no_git": self.no_git,
            "vault": self.vault,
            "destination": self.destination,
            "images": self.images,
            "sync_branch": self.sync_branch,
        }

    def matches_config(self, config: ClipConfig) -> bool:
        return (
            self.vault == str(config.vault)
            and self.destination == str(config.destination)
            and self.images == str(config.images)
            and self.sync_branch == config.sync_branch
        )


@dataclass(frozen=True)
class _ApprovedRemoteImageUrl:
    url: str
    scheme: str
    hostname: str
    port: int
    host_header: str
    request_target: str
    address: str
    family: int


@dataclass(frozen=True)
class _PinnedRemoteResponse:
    status_code: int
    headers: Mapping[str, str]
    body: bytes


def _resolve_vault_path(vault: Path, configured: str) -> Path:
    raw = Path(configured).expanduser()
    candidate = raw if raw.is_absolute() else vault / raw
    resolved = candidate.resolve()
    _require_within(resolved, vault, "Configured path must remain inside the vault.")
    return resolved


def _require_within(path: Path, parent: Path, message: str) -> None:
    try:
        path.relative_to(parent)
    except ValueError as exc:
        raise ClipError(message) from exc


def _validate_http_url(value: str) -> None:
    if len(value) > 8192:
        raise ClipError("The URL is too long.")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ClipError("A valid HTTP or HTTPS URL is required.") from exc
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port is not None and not 0 < port < 65536
    ):
        raise ClipError("A valid HTTP or HTTPS URL is required.")


def parse_clip_args(raw_args: str) -> ClipOptions:
    """Parse one URL and the supported flags without invoking a shell."""
    try:
        tokens = shlex.split(raw_args, posix=True)
    except ValueError as exc:
        raise ClipError("Invalid quoting in /clip arguments.") from exc

    no_browser = False
    no_git = False
    refresh = False
    save_images = "ask"
    urls: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "--no-browser":
            no_browser = True
        elif token == "--no-git":
            no_git = True
        elif token == "--refresh":
            refresh = True
        elif token == "--save-images":
            index += 1
            if index >= len(tokens):
                raise ClipError("The --save-images option requires yes, no, or ask.")
            save_images = tokens[index].strip().lower()
            if save_images not in {"yes", "no", "ask"}:
                raise ClipError("The --save-images option only accepts yes, no, or ask.")
        elif token.startswith("-"):
            raise ClipError("Unknown /clip option.")
        else:
            urls.append(token)
        index += 1
    if len(urls) != 1:
        raise ClipError(
            "Usage: /clip <url> [--refresh] [--no-browser] [--no-git] [--save-images yes|no|ask]"
        )
    _validate_http_url(urls[0])
    return ClipOptions(
        url=urls[0],
        no_browser=no_browser,
        no_git=no_git,
        refresh=refresh,
        save_images=save_images,
    )


def normalize_url(value: str) -> str:
    """Return a stable URL form used only for source identity comparisons."""
    _validate_http_url(value)
    parsed = urlsplit(value)
    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").lower()
    try:
        hostname = hostname.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ClipError("The page returned an invalid source URL.") from exc
    port = parsed.port
    default_port = (scheme == "http" and port == 80) or (
        scheme == "https" and port == 443
    )
    netloc = hostname if port is None or default_port else f"{hostname}:{port}"
    path = parsed.path or "/"
    return urlunsplit((scheme, netloc, path, parsed.query, ""))


def _is_blocked_remote_ip(address: str) -> bool:
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return True
    if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped is not None:
        parsed = parsed.ipv4_mapped
    networks = (
        _BLOCKED_IMAGE_IPV4_NETWORKS
        if isinstance(parsed, ipaddress.IPv4Address)
        else _BLOCKED_IMAGE_IPV6_NETWORKS
    )
    return any(parsed in network for network in networks)


def _remote_host_header(hostname: str, port: int, scheme: str) -> str:
    formatted = f"[{hostname}]" if ":" in hostname else hostname
    default_port = 80 if scheme == "http" else 443
    return formatted if port == default_port else f"{formatted}:{port}"


def _resolve_remote_image_url(
    value: str,
    *,
    resolver=socket.getaddrinfo,
) -> _ApprovedRemoteImageUrl:
    normalized = normalize_url(value)
    parsed = urlsplit(normalized)
    scheme = parsed.scheme.lower()
    hostname = parsed.hostname or ""
    port = parsed.port or (80 if scheme == "http" else 443)
    default_port = 80 if scheme == "http" else 443
    if parsed.port is not None and port != default_port:
        raise ClipError(_REMOTE_FETCH_POLICY_MESSAGE)
    try:
        ascii_hostname = hostname.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ClipError(_REMOTE_FETCH_POLICY_MESSAGE) from exc
    try:
        infos = resolver(
            ascii_hostname,
            port,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
    except OSError as exc:
        raise ClipError("Downloading a remote image failed.") from exc
    if not infos:
        raise ClipError("Downloading a remote image failed.")
    approved: list[tuple[int, str]] = []
    seen: set[tuple[int, str]] = set()
    for family, _socktype, _proto, _canonname, sockaddr in infos:
        if family not in {socket.AF_INET, socket.AF_INET6} or not sockaddr:
            raise ClipError(_REMOTE_FETCH_POLICY_MESSAGE)
        address = sockaddr[0]
        key = (family, address)
        if key in seen:
            continue
        seen.add(key)
        if _is_blocked_remote_ip(address):
            raise ClipError(_REMOTE_FETCH_POLICY_MESSAGE)
        approved.append(key)
    if not approved:
        raise ClipError("Downloading a remote image failed.")
    request_target = parsed.path or "/"
    if parsed.query:
        request_target = f"{request_target}?{parsed.query}"
    family, address = approved[0]
    return _ApprovedRemoteImageUrl(
        url=normalized,
        scheme=scheme,
        hostname=ascii_hostname,
        port=port,
        host_header=_remote_host_header(ascii_hostname, port, scheme),
        request_target=request_target,
        address=address,
        family=family,
    )


def _socket_target(address: str, family: int, port: int) -> tuple[object, ...]:
    return (address, port, 0, 0) if family == socket.AF_INET6 else (address, port)


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, approved: _ApprovedRemoteImageUrl, *, timeout: float):
        super().__init__(approved.hostname, approved.port, timeout=timeout)
        self._approved = approved

    def connect(self) -> None:
        sock = socket.socket(self._approved.family, socket.SOCK_STREAM)
        try:
            sock.settimeout(self.timeout)
            sock.connect(_socket_target(self._approved.address, self._approved.family, self._approved.port))
        except Exception:
            sock.close()
            raise
        self.sock = sock


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, approved: _ApprovedRemoteImageUrl, *, timeout: float):
        super().__init__(approved.hostname, approved.port, timeout=timeout)
        self._approved = approved

    def connect(self) -> None:
        raw_sock = socket.socket(self._approved.family, socket.SOCK_STREAM)
        try:
            raw_sock.settimeout(self.timeout)
            raw_sock.connect(_socket_target(self._approved.address, self._approved.family, self._approved.port))
            context = ssl.create_default_context()
            self.sock = context.wrap_socket(raw_sock, server_hostname=self._approved.hostname)
        except Exception:
            raw_sock.close()
            raise


def _read_remote_response_body(response: http.client.HTTPResponse, max_bytes: int) -> bytes:
    declared = response.getheader("Content-Length")
    if declared is not None:
        try:
            if int(declared) > max_bytes:
                raise ClipError("A remote image is too large to save safely.")
        except ValueError:
            pass
    payload = bytearray()
    while True:
        chunk = response.read(min(64 * 1024, max_bytes + 1 - len(payload)))
        if not chunk:
            break
        payload.extend(chunk)
        if len(payload) > max_bytes:
            raise ClipError("A remote image is too large to save safely.")
    return bytes(payload)


def _perform_pinned_remote_image_request(
    approved: _ApprovedRemoteImageUrl,
    *,
    timeout: int,
    max_bytes: int,
) -> _PinnedRemoteResponse:
    connection: http.client.HTTPConnection
    if approved.scheme == "https":
        connection = _PinnedHTTPSConnection(approved, timeout=timeout)
    else:
        connection = _PinnedHTTPConnection(approved, timeout=timeout)
    try:
        connection.request(
            "GET",
            approved.request_target,
            headers={
                "Host": approved.host_header,
                "User-Agent": "Mozilla/5.0 Hermes web-to-obsidian",
                "Accept": "image/*,*/*;q=0.8",
                "Accept-Encoding": "identity",
                "Connection": "close",
            },
        )
        response = connection.getresponse()
        status_code = int(response.status)
        headers = {name: value for name, value in response.getheaders()}
        body = (
            b""
            if status_code in _REMOTE_REDIRECT_STATUSES
            else _read_remote_response_body(response, max_bytes)
        )
        return _PinnedRemoteResponse(status_code=status_code, headers=headers, body=body)
    except ClipError:
        raise
    except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
        raise ClipError("Downloading a remote image failed.") from exc
    finally:
        connection.close()


def _first_remote_header(headers: Mapping[str, str], name: str) -> str | None:
    for header_name, value in headers.items():
        if header_name.lower() == name.lower():
            return value
    return None


def _fetch_remote_image(
    url: str,
    *,
    resolver=socket.getaddrinfo,
    request_impl=None,
) -> tuple[str, str, bytes]:
    current = url
    fetch = _perform_pinned_remote_image_request if request_impl is None else request_impl
    for redirect_count in range(6):
        approved = _resolve_remote_image_url(current, resolver=resolver)
        response = fetch(approved, timeout=30, max_bytes=MAX_IMAGE_BYTES)
        if response.status_code in _REMOTE_REDIRECT_STATUSES:
            location = _first_remote_header(response.headers, "Location")
            if not location or redirect_count >= 5:
                raise ClipError("Downloading a remote image failed.")
            try:
                current = normalize_url(urljoin(approved.url, location))
            except ClipError as exc:
                raise ClipError(_REMOTE_FETCH_POLICY_MESSAGE) from exc
            continue
        if response.status_code < 200 or response.status_code >= 300:
            raise ClipError(
                f"Downloading a remote image failed with HTTP {response.status_code}."
            )
        content_type = (
            _first_remote_header(response.headers, "Content-Type") or ""
        ).split(";", 1)[0].strip().lower()
        if not content_type.startswith("image/"):
            raise ClipError("A remote image URL returned non-image content.")
        return approved.url, content_type, response.body
    raise ClipError("Downloading a remote image failed.")


def _normalize_text(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def _content_hash(markdown: str) -> str:
    normalized = _normalize_text(markdown).rstrip("\n") + "\n"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


_DANGEROUS_SCHEMES = r"(?:javascript|vbscript|file|obsidian|data)"
_INLINE_CODE_SPAN = re.compile(r"(`+)([^`\n]*?)\1")


def _sanitize_active_markdown_segment(text: str) -> str:
    text = re.sub(r"(?s)<!--.*?-->", "", text)
    dangerous_tags = r"script|iframe|object|embed|form|input|button|textarea|select|option|style|link|meta|base|svg|math"
    text = re.sub(
        rf"(?is)<(?P<tag>{dangerous_tags})\b[^>]*>.*?</(?P=tag)\s*>",
        "",
        text,
    )
    text = re.sub(rf"(?is)</?(?:{dangerous_tags})\b[^>]*>", "", text)
    text = re.sub(r"(?i)<(?=/?[A-Za-z][A-Za-z0-9-]*(?:\s|/?>))", "&lt;", text)
    text = re.sub(
        rf"(?i)(\]\(\s*<?){_DANGEROUS_SCHEMES}:",
        r"\1blocked:",
        text,
    )
    text = re.sub(
        rf"(?im)^(\s*\[[^\]\n]+\]:\s*<?){_DANGEROUS_SCHEMES}:",
        r"\1blocked:",
        text,
    )
    return text.replace("[[", r"\[\[")


def _sanitize_text_preserving_inline_code(text: str) -> str:
    pieces: list[str] = []
    cursor = 0
    for match in _INLINE_CODE_SPAN.finditer(text):
        pieces.append(_sanitize_active_markdown_segment(text[cursor:match.start()]))
        pieces.append(match.group(0))
        cursor = match.end()
    pieces.append(_sanitize_active_markdown_segment(text[cursor:]))
    return "".join(pieces)


def _scan_remote_images_in_plain_text(text: str, seen: set[str], found: list[str]) -> None:
    cursor = 0
    for inline_code in _INLINE_CODE_SPAN.finditer(text):
        _collect_remote_images(text[cursor:inline_code.start()], seen, found)
        cursor = inline_code.end()
    _collect_remote_images(text[cursor:], seen, found)


def _is_indented_code_line(line: str, leading_spaces: int | None = None) -> bool:
    if line.startswith("\t"):
        return True
    if leading_spaces is None:
        leading_spaces = len(line) - len(line.lstrip(" "))
    return leading_spaces >= 4 and bool(line.strip())


def _collect_remote_images(text: str, seen: set[str], found: list[str]) -> None:
    for match in _REMOTE_MARKDOWN_IMAGE.finditer(text):
        normalized_url = normalize_url(match.group(1))
        if normalized_url in seen:
            continue
        seen.add(normalized_url)
        found.append(normalized_url)


def sanitize_markdown(markdown: str) -> str:
    text = _normalize_text(markdown)
    result: list[str] = []
    plain: list[str] = []
    fenced: list[str] = []
    in_fence = False
    fence_char = ""
    fence_length = 0

    for line in text.splitlines(keepends=True):
        leading_spaces = len(line) - len(line.lstrip(" "))
        content = line[leading_spaces:]
        fence_match = None
        if leading_spaces <= 3:
            fence_match = re.match(r"(`{3,}|~{3,})", content)

        if in_fence:
            fenced.append(line)
            if fence_match:
                marker = fence_match.group(1)
                if marker[0] == fence_char and len(marker) >= fence_length:
                    result.append("".join(fenced))
                    fenced = []
                    in_fence = False
                    fence_char = ""
                    fence_length = 0
            continue

        if fence_match:
            if plain:
                result.append(_sanitize_text_preserving_inline_code("".join(plain)))
                plain = []
            marker = fence_match.group(1)
            in_fence = True
            fence_char = marker[0]
            fence_length = len(marker)
            fenced = [line]
            continue

        if _is_indented_code_line(line, leading_spaces):
            if plain:
                result.append(_sanitize_text_preserving_inline_code("".join(plain)))
                plain = []
            result.append(line)
            continue

        plain.append(line)

    if plain:
        result.append(_sanitize_text_preserving_inline_code("".join(plain)))
    if fenced:
        result.append("".join(fenced))
    return "".join(result)


def _contains_markdown_h1(markdown: str) -> bool:
    in_fence = False
    fence_char = ""
    fence_length = 0
    previous_text_line = None
    for raw_line in _normalize_text(markdown).split("\n"):
        if raw_line.startswith("\t"):
            previous_text_line = None
            continue

        leading_spaces = len(raw_line) - len(raw_line.lstrip(" "))
        content = raw_line[leading_spaces:]

        if leading_spaces <= 3:
            fence_match = re.match(r"(`{3,}|~{3,})", content)
            if fence_match:
                marker = fence_match.group(1)
                if not in_fence:
                    in_fence = True
                    fence_char = marker[0]
                    fence_length = len(marker)
                elif marker[0] == fence_char and len(marker) >= fence_length:
                    in_fence = False
                    fence_char = ""
                    fence_length = 0
                previous_text_line = None
                continue

        if in_fence:
            continue

        if leading_spaces >= 4:
            previous_text_line = None
            continue

        if re.match(r"^#(?:\s+\S|\s*$)", content):
            return True
        if re.match(r"^=+\s*$", content) and previous_text_line:
            return True

        previous_text_line = content if content.strip() else None
    return False


def _sanitized_heading_title(title: str) -> str:
    sanitized = sanitize_markdown(title)
    return re.sub(r"\s+", " ", sanitized).strip()


def _managed_markdown(title: str, markdown: str) -> str:
    cleaned_title = _sanitized_heading_title(title)
    cleaned_markdown = _normalize_text(markdown).rstrip("\n")
    if _contains_markdown_h1(cleaned_markdown):
        return cleaned_markdown
    if not cleaned_markdown:
        return f"# {cleaned_title}"
    return f"# {cleaned_title}\n\n{cleaned_markdown}"


def render_note(
    data: Mapping[str, object],
    created: str | None = None,
    *,
    content_markdown: str | None = None,
    image_mode: str | None = None,
) -> str:
    """Render extractor data as normalized Markdown with YAML frontmatter."""
    if image_mode not in {None, "remote", "local"}:
        raise ClipError("The image save mode is invalid.")
    checked = _validate_success_payload(data)
    timestamp = created or datetime.now(timezone.utc).isoformat(timespec="seconds")
    source = normalize_url(str(checked["canonicalUrl"] or checked["url"]))
    fetched_url = normalize_url(str(checked["url"]))
    source_markdown = sanitize_markdown(str(checked["markdown"])).rstrip("\n")
    effective_markdown = (
        _normalize_text(content_markdown).rstrip("\n")
        if content_markdown is not None
        else source_markdown
    )
    managed_markdown = _managed_markdown(str(checked["title"]), effective_markdown)
    metadata = {
        "title": checked["title"],
        "url": source,
        "author": checked["author"],
        "site": checked["site"],
        "description": checked["description"],
        "keywords": checked["keywords"],
        "tags": ["web-clip"],
        "original_url": source,
        "original_host": urlsplit(source).hostname or "",
        **({"fetched_url": fetched_url} if fetched_url != source else {}),
        "extraction_method": checked["method"],
        "status": "needs-review",
        "category": "Inbox",
        "word_count": checked["wordCount"],
        "webclip_id": "sha256:" + _url_hash(source),
        "source_content_hash": "sha256:" + _content_hash(source_markdown),
        "content_hash": "sha256:" + _content_hash(effective_markdown),
        **({"image_mode": image_mode} if image_mode is not None else {}),
        "published": checked["published"],
        "created": timestamp,
    }
    frontmatter = yaml.safe_dump(
        metadata,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    ).rstrip("\n")
    return (
        f"---\n{frontmatter}\n---\n\n"
        "<!-- webclip:managed:start -->\n"
        f"{managed_markdown}\n"
        "<!-- webclip:managed:end -->\n\n"
        "<!-- webclip:manual:start -->\n"
        "<!-- webclip:manual:end -->\n"
    )


def _url_hash(url: str) -> str:
    return hashlib.sha256(normalize_url(url).encode("utf-8")).hexdigest()


def find_remote_images(markdown: str) -> list[str]:
    normalized = _normalize_text(markdown)
    found: list[str] = []
    seen: set[str] = set()
    plain: list[str] = []
    in_fence = False
    fence_char = ""
    fence_length = 0

    for line in normalized.splitlines(keepends=True):
        leading_spaces = len(line) - len(line.lstrip(" "))
        content = line[leading_spaces:]
        fence_match = None
        if leading_spaces <= 3:
            fence_match = re.match(r"(`{3,}|~{3,})", content)

        if in_fence:
            if fence_match:
                marker = fence_match.group(1)
                if marker[0] == fence_char and len(marker) >= fence_length:
                    in_fence = False
                    fence_char = ""
                    fence_length = 0
            continue

        if fence_match:
            if plain:
                _scan_remote_images_in_plain_text("".join(plain), seen, found)
                plain = []
            marker = fence_match.group(1)
            in_fence = True
            fence_char = marker[0]
            fence_length = len(marker)
            continue

        if _is_indented_code_line(line, leading_spaces):
            if plain:
                _scan_remote_images_in_plain_text("".join(plain), seen, found)
                plain = []
            continue

        plain.append(line)

    if plain:
        _scan_remote_images_in_plain_text("".join(plain), seen, found)
    return found


def github_blob_url(origin_url: str, branch: str, relative_path: str) -> str | None:
    remote = origin_url.strip()
    match = _GITHUB_REMOTE_HTTPS.fullmatch(remote) or _GITHUB_REMOTE_SSH.fullmatch(remote)
    if match is None:
        return None
    owner, repo = match.groups()
    return (
        f"https://github.com/{owner}/{repo}/blob/{quote(branch, safe='')}/"
        f"{quote(relative_path, safe='/')}"
    )


def _pending_root(env: Mapping[str, str] | None) -> Path:
    raw = (
        env.get("WEB_TO_OBSIDIAN_PENDING_ROOT")
        if env is not None and "WEB_TO_OBSIDIAN_PENDING_ROOT" in env
        else "~/.hermes/workspace/cache/url-to-obsidian/pending-state"
    )
    return Path(raw).expanduser().resolve()


def _active_pending_pointer(root: Path) -> Path:
    return root / "active.json"


def _pending_state_path(root: Path, pending_id: str) -> Path:
    return root / f"{pending_id}.json"


def _write_json(path: Path, payload: Mapping[str, object]) -> None:
    atomic_write(path, json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n")


def _clear_pending(root: Path, pending_id: str | None = None) -> None:
    pointer = _active_pending_pointer(root)
    if pending_id is None and pointer.is_file():
        try:
            data = json.loads(pointer.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                pending_id = data.get("pending_id") if isinstance(data.get("pending_id"), str) else None
        except (OSError, UnicodeError, json.JSONDecodeError):
            pending_id = None
    candidates = [pointer]
    if pending_id:
        candidates.append(_pending_state_path(root, pending_id))
    for candidate in candidates:
        try:
            candidate.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise ClipError("Could not clear the pending image confirmation state.") from exc


def _load_pending_state(root: Path) -> PendingClipState:
    pointer = _active_pending_pointer(root)
    if not pointer.is_file():
        raise ClipError("No pending image confirmation exists.")
    try:
        active = json.loads(pointer.read_text(encoding="utf-8"))
        pending_id = active.get("pending_id")
    except (OSError, UnicodeError, json.JSONDecodeError, AttributeError) as exc:
        raise ClipError("The pending image confirmation state is invalid.") from exc
    if not isinstance(pending_id, str) or not pending_id:
        raise ClipError("The pending image confirmation state is invalid.")
    path = _pending_state_path(root, pending_id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        _clear_pending(root, pending_id)
        raise ClipError("No pending image confirmation exists.") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ClipError("The pending image confirmation state is invalid.") from exc
    if not isinstance(payload, dict):
        raise ClipError("The pending image confirmation state is invalid.")
    required = {
        "pending_id": str,
        "created_at": str,
        "expires_at": str,
        "article": dict,
        "refresh": bool,
        "no_git": bool,
        "vault": str,
        "destination": str,
        "images": str,
        "sync_branch": str,
    }
    for field, expected_type in required.items():
        value = payload.get(field)
        if not isinstance(value, expected_type):
            raise ClipError("The pending image confirmation state is invalid.")
    expires_at = datetime.fromisoformat(str(payload["expires_at"]))
    if datetime.now(timezone.utc) >= expires_at:
        _clear_pending(root, pending_id)
        raise ClipError("The pending image confirmation expired; please run /clip again.")
    return PendingClipState(
        pending_id=str(payload["pending_id"]),
        created_at=str(payload["created_at"]),
        expires_at=str(payload["expires_at"]),
        article=dict(payload["article"]),
        refresh=bool(payload["refresh"]),
        no_git=bool(payload["no_git"]),
        vault=str(payload["vault"]),
        destination=str(payload["destination"]),
        images=str(payload["images"]),
        sync_branch=str(payload["sync_branch"]),
    )


def _store_pending_state(root: Path, state: PendingClipState) -> None:
    try:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError as exc:
        raise ClipError("Could not create the pending image confirmation directory.") from exc
    try:
        if root.is_symlink():
            raise ClipError("Refusing to use a symbolic-link pending state directory.")
        os.chmod(root, 0o700)
    except OSError as exc:
        raise ClipError("Could not secure the pending image confirmation directory.") from exc
    pointer = _active_pending_pointer(root)
    if pointer.exists():
        existing = _load_pending_state(root)
        raise ClipError(
            f"Another clipped article ('{existing.article.get('title', 'unknown')}') is already waiting for yes/no confirmation."
        )
    _write_json(_pending_state_path(root, state.pending_id), state.to_json())
    _write_json(pointer, {"pending_id": state.pending_id})


def _slugify_image_dir(note_path: Path, url: str) -> str:
    ascii_text = (
        unicodedata.normalize("NFKD", note_path.stem).encode("ascii", "ignore").decode("ascii")
    )
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", ascii_text).strip("-").lower()
    return cleaned or _url_hash(url)[:12]


def download_remote_image(
    url: str,
    destination_dir: Path,
    index: int,
    *,
    resolver=socket.getaddrinfo,
    request_impl=None,
) -> Path:
    final_url, content_type, payload = _fetch_remote_image(
        url,
        resolver=resolver,
        request_impl=request_impl,
    )
    suffix = Path(urlsplit(final_url).path).suffix.lower()
    if not re.fullmatch(r"\.[A-Za-z0-9]{1,8}", suffix or ""):
        subtype = content_type.removeprefix("image/")
        suffix = ".jpg" if subtype == "jpeg" else f".{subtype or 'img'}"
    base_name = Path(urlsplit(final_url).path).stem or "image"
    base_name = re.sub(r"[^A-Za-z0-9._-]+", "-", base_name).strip("-._") or "image"
    file_name = f"{index:02d}-{base_name}{suffix}"
    target = destination_dir / file_name
    atomic_write_bytes(target, payload)
    return target


def _rewrite_remote_image_references(markdown: str, replacements: Mapping[str, str]) -> str:
    def replace_markdown(match: re.Match[str]) -> str:
        url = normalize_url(match.group(1))
        replacement = replacements.get(url)
        return match.group(0).replace(match.group(1), replacement, 1) if replacement else match.group(0)

    return _REMOTE_MARKDOWN_IMAGE.sub(replace_markdown, markdown)


def _cleanup_localized_files(paths: Sequence[Path], *, images_root: Path) -> None:
    root = images_root.resolve()
    resolved_paths = sorted({path.resolve() for path in paths}, key=lambda path: len(path.parents), reverse=True)
    for path in resolved_paths:
        try:
            _require_within(path, root, "Generated image path escaped the configured image directory.")
        except ClipError:
            continue
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            continue
        parent = path.parent
        while parent != root:
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent


def _localize_images(
    markdown: str,
    title: str,
    source_url: str,
    images_root: Path,
    note_path: Path,
) -> tuple[str, list[Path]]:
    remote_images = find_remote_images(markdown)
    if not remote_images:
        return markdown, []
    article_dir = images_root / _slugify_image_dir(note_path, source_url)
    try:
        article_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ClipError("Could not create the configured image directory.") from exc
    replacements: dict[str, str] = {}
    generated_paths: list[Path] = []
    try:
        for index, image_url in enumerate(remote_images, start=1):
            downloaded = download_remote_image(image_url, article_dir, index)
            relative = os.path.relpath(downloaded, start=note_path.parent)
            replacements[image_url] = Path(relative).as_posix()
            generated_paths.append(downloaded)
    except Exception:
        _cleanup_localized_files(generated_paths, images_root=images_root)
        raise
    return _rewrite_remote_image_references(markdown, replacements), generated_paths


def _truncate_utf8(value: str, byte_limit: int) -> str:
    used = 0
    result: list[str] = []
    for char in value:
        encoded_size = len(char.encode("utf-8"))
        if used + encoded_size > byte_limit:
            break
        result.append(char)
        used += encoded_size
    return "".join(result)


def safe_filename(title: str, url: str, max_bytes: int = MAX_FILENAME_BYTES) -> str:
    """Build a portable Unicode Markdown filename capped by encoded byte size."""
    if max_bytes < 16:
        raise ClipError("The configured filename limit is too small.")
    cleaned_chars = []
    for char in _normalize_text(title):
        if char in _FORBIDDEN_FILENAME or unicodedata.category(char).startswith("C"):
            continue
        cleaned_chars.append(char)
    stem = "".join(cleaned_chars)
    stem = re.sub(r"\s+", " ", stem)
    stem = re.sub(r"\.+", ".", stem).strip(" .")
    if not stem:
        stem = _url_hash(url)[:12]
    if stem.split(".", 1)[0].upper() in DOS_RESERVED:
        stem = f"_{stem}"

    extension = ".md"
    stem = _truncate_utf8(stem, max_bytes - len(extension)).rstrip(" .")
    if not stem:
        stem = _truncate_utf8(_url_hash(url)[:12], max_bytes - len(extension))
    return stem + extension


def _filename_with_suffix(title: str, url: str, suffix: str) -> str:
    base = safe_filename(title, url, MAX_FILENAME_BYTES).removesuffix(".md")
    tail = f"-{suffix}.md"
    base = _truncate_utf8(base, MAX_FILENAME_BYTES - len(tail)).rstrip(" .")
    if not base:
        base = _url_hash(url)[:12]
    return base + tail


def _frontmatter_from_text(text: str) -> dict[str, object] | None:
    normalized = _normalize_text(text)
    if not normalized.startswith("---\n"):
        return None
    end = normalized.find("\n---\n", 4)
    if end < 0:
        return None
    try:
        parsed = yaml.safe_load(normalized[4:end])
    except yaml.YAMLError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _source_from_note(path: Path) -> str | None:
    try:
        with path.open("rb") as handle:
            raw = handle.read(64 * 1024 + 1)
        if len(raw) > 64 * 1024:
            return None
        text = raw.decode("utf-8")
    except (OSError, UnicodeError):
        return None
    metadata = _frontmatter_from_text(text)
    source = None
    if metadata is not None:
        for field in ("url", "original_url", "source"):
            candidate = metadata.get(field)
            if isinstance(candidate, str) and candidate:
                source = candidate
                break
    try:
        return normalize_url(source) if isinstance(source, str) else None
    except ClipError:
        return None


def _checked_candidate(destination: Path, filename: str) -> Path:
    candidate = destination / filename
    if candidate.is_symlink():
        raise ClipError("Refusing to replace a symbolic-link note.")
    resolved = candidate.resolve()
    _require_within(resolved, destination, "Generated note path escaped its destination.")
    return candidate


def choose_target(
    destination: Path,
    title: str,
    source: str,
    *,
    capture_date: str | None = None,
) -> Path:
    """Choose an idempotent target without scanning outside the destination."""
    destination = destination.resolve()
    if not destination.is_dir():
        raise ClipError("The configured clip destination is unavailable.")
    normalized_source = normalize_url(source)
    entries: list[Path] = []
    for index, existing in enumerate(destination.iterdir(), start=1):
        if index > MAX_DESTINATION_SCAN_ENTRIES:
            raise ClipError("The clip destination contains too many entries to scan safely.")
        entries.append(existing)

    matches: list[Path] = []
    for existing in sorted(entries, key=lambda path: path.name):
        if existing.suffix.lower() != ".md":
            continue
        if existing.is_symlink():
            raise ClipError("Refusing to scan a symbolic-link note.")
        if existing.is_file() and _source_from_note(existing) == normalized_source:
            matches.append(existing)
    if len(matches) > 1:
        raise ClipError("The clip destination contains multiple notes for this source.")
    if matches:
        return matches[0]

    if capture_date is None:
        capture_date = datetime.now(timezone.utc).date().isoformat()
    try:
        parsed_date = datetime.strptime(capture_date, "%Y-%m-%d").date().isoformat()
    except ValueError as exc:
        raise ClipError("The capture date is invalid.") from exc
    if parsed_date != capture_date:
        raise ClipError("The capture date is invalid.")

    dated_title = f"{capture_date}-{title}"
    base = _checked_candidate(destination, safe_filename(dated_title, source))
    if not base.exists() or _source_from_note(base) == normalized_source:
        return base

    digest = _url_hash(source)
    for length in range(8, len(digest) + 1, 4):
        candidate = _checked_candidate(
            destination, _filename_with_suffix(dated_title, source, digest[:length])
        )
        if not candidate.exists() or _source_from_note(candidate) == normalized_source:
            return candidate
    raise ClipError("Unable to choose a unique filename for the clip.")


def atomic_write(target: Path, content: str) -> None:
    """Durably replace a note using a temporary file in the same directory."""
    parent = target.parent.resolve()
    if not parent.is_dir():
        raise ClipError("The configured clip destination is unavailable.")
    if target.is_symlink():
        raise ClipError("Refusing to replace a symbolic-link note.")
    _require_within(target.resolve(), parent, "Generated note path escaped its destination.")

    fd = -1
    temporary: str | None = None
    try:
        fd, temporary = tempfile.mkstemp(prefix=".clip-", suffix=".tmp", dir=parent)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            fd = -1
            handle.write(_normalize_text(content))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        temporary = None
        try:
            flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            directory_fd = os.open(parent, flags)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
    except ClipError:
        raise
    except OSError as exc:
        raise ClipError("Could not safely write the clipped note.") from exc
    finally:
        if fd >= 0:
            os.close(fd)
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            except OSError:
                pass


def atomic_write_bytes(target: Path, content: bytes) -> None:
    """Durably replace a binary asset using a temporary file in the same directory."""
    parent = target.parent.resolve()
    if not parent.is_dir():
        raise ClipError("The configured clip destination is unavailable.")
    if target.is_symlink():
        raise ClipError("Refusing to replace a symbolic-link note.")
    _require_within(target.resolve(), parent, "Generated note path escaped its destination.")

    fd = -1
    temporary: str | None = None
    try:
        fd, temporary = tempfile.mkstemp(prefix=".clip-", suffix=".tmp", dir=parent)
        with os.fdopen(fd, "wb") as handle:
            fd = -1
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        temporary = None
        try:
            flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            directory_fd = os.open(parent, flags)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
    except ClipError:
        raise
    except OSError as exc:
        raise ClipError("Could not safely write the clipped asset.") from exc
    finally:
        if fd >= 0:
            os.close(fd)
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            except OSError:
                pass


def _manual_section(text: str) -> str:
    start_marker = "<!-- webclip:manual:start -->\n"
    end_marker = "<!-- webclip:manual:end -->"
    start = text.find(start_marker)
    if start < 0:
        return ""
    start += len(start_marker)
    end = text.find(end_marker, start)
    return text[start:end] if end >= 0 else ""


def _managed_section(text: str) -> str | None:
    start_marker = "<!-- webclip:managed:start -->\n"
    end_marker = "<!-- webclip:managed:end -->"
    start = text.find(start_marker)
    if start < 0:
        return None
    start += len(start_marker)
    end = text.find(end_marker, start)
    return text[start:end] if end >= 0 else None


def _note_semantic_state(text: str) -> tuple[dict[str, object], str] | None:
    metadata = _frontmatter_from_text(text)
    managed = _managed_section(text)
    if metadata is None or managed is None:
        return None
    comparable_metadata = dict(metadata)
    comparable_metadata.pop("created", None)
    return comparable_metadata, managed


def _with_manual_section(note: str, manual: str) -> str:
    marker = "<!-- webclip:manual:start -->\n"
    start = note.find(marker)
    end_marker = "<!-- webclip:manual:end -->"
    end = note.find(end_marker, start + len(marker)) if start >= 0 else -1
    if start < 0 or end < 0:
        raise ClipError("The generated note is missing managed boundaries.")
    return note[: start + len(marker)] + manual + note[end:]


def write_managed_note(target: Path, content: str, *, refresh: bool) -> str:
    """Write a new managed note, no-op identical content, or require refresh."""
    if not target.exists():
        atomic_write(target, content)
        return "written"
    if target.is_symlink():
        raise ClipError("Refusing to replace a symbolic-link note.")
    try:
        existing = target.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ClipError("Could not read the existing clipped note.") from exc
    old_state = _note_semantic_state(existing)
    new_state = _note_semantic_state(content)
    if old_state is None or new_state is None:
        raise ClipError("The existing clipped note has invalid managed metadata.")
    old_meta, _ = old_state
    new_meta, _ = new_state
    if old_meta.get("webclip_id") != new_meta.get("webclip_id"):
        raise ClipError("Refusing to replace a note managed for a different URL.")
    if old_state == new_state:
        return "unchanged"
    if not refresh:
        raise ClipError("The saved page changed; rerun with --refresh to update it.")
    atomic_write(target, _with_manual_section(content, _manual_section(existing)))
    return "written"


def _run_bounded(
    command: Sequence[str],
    *,
    timeout: int,
    stdout_limit: int,
    stderr_limit: int,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> ProcessResult:
    """Run without a shell while draining pipes into strictly capped buffers."""
    if not isinstance(command, (list, tuple)) or not all(
        isinstance(part, str) for part in command
    ):
        raise ClipError("Internal command construction failed.")
    try:
        process = subprocess.Popen(
            list(command),
            cwd=str(cwd) if cwd is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            env=dict(env) if env is not None else None,
            start_new_session=True,
        )
    except OSError as exc:
        raise ClipError("A required local command could not be started.") from exc

    stdout_buffer = bytearray()
    stderr_buffer = bytearray()
    exceeded = threading.Event()
    termination_lock = threading.Lock()

    def terminate_group() -> None:
        with termination_lock:
            if process.poll() is not None:
                return
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except (OSError, ProcessLookupError):
                pass
            try:
                process.wait(timeout=1)
                return
            except subprocess.TimeoutExpired:
                pass
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass

    def drain(stream, buffer: bytearray, limit: int) -> None:
        if stream is None:
            return
        while True:
            chunk = stream.read(64 * 1024)
            if not chunk:
                return
            remaining = limit - len(buffer)
            if remaining > 0:
                buffer.extend(chunk[:remaining])
            if len(chunk) > remaining:
                exceeded.set()
                terminate_group()
                return

    threads = [
        threading.Thread(
            target=drain, args=(process.stdout, stdout_buffer, stdout_limit), daemon=True
        ),
        threading.Thread(
            target=drain, args=(process.stderr, stderr_buffer, stderr_limit), daemon=True
        ),
    ]
    for thread in threads:
        thread.start()
    timed_out = False
    try:
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        terminate_group()
        returncode = process.returncode if process.returncode is not None else -1
    finally:
        for thread in threads:
            thread.join(timeout=2)
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()
    if timed_out:
        raise ClipError("The local command timed out.")
    if exceeded.is_set():
        raise ClipError("The local command returned too much output.")
    return ProcessResult(returncode, bytes(stdout_buffer), bytes(stderr_buffer))


def _decode_json_object(raw: bytes) -> Mapping[str, object]:
    try:
        text = raw.decode("utf-8")
        payload = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ClipError("The extractor returned an invalid response.") from exc
    if not isinstance(payload, dict):
        raise ClipError("The extractor returned an invalid response.")
    return payload


def _extractor_failure(payload: Mapping[str, object]) -> ClipError:
    code = payload.get("code")
    if isinstance(code, str) and _SAFE_ERROR_CODE.fullmatch(code):
        return ClipError(f"Web extraction failed ({code}).")
    return ClipError("Web extraction failed.")


def _validate_success_payload(data: Mapping[str, object]) -> dict[str, object]:
    limits = {
        "title": 10_000,
        "author": 100_000,
        "published": 10_000,
        "description": 1_000_000,
        "site": 100_000,
        "canonicalUrl": 8192,
        "url": 8192,
        "markdown": 10 * 1024 * 1024,
        "method": 1000,
    }
    checked: dict[str, object] = {}
    for field, limit in limits.items():
        value = data.get(field)
        if not isinstance(value, str) or len(value.encode("utf-8")) > limit:
            raise ClipError("The extractor returned incomplete or invalid article data.")
        checked[field] = value
    if not checked["title"] or not checked["method"]:
        raise ClipError("The extractor returned incomplete or invalid article data.")
    keywords = data.get("keywords")
    if not isinstance(keywords, list) or len(keywords) > 128:
        raise ClipError("The extractor returned incomplete or invalid article data.")
    normalized_keywords: list[str] = []
    for keyword in keywords:
        if not isinstance(keyword, str) or not keyword or len(keyword.encode("utf-8")) > 256:
            raise ClipError("The extractor returned incomplete or invalid article data.")
        normalized_keywords.append(keyword)
    checked["keywords"] = normalized_keywords
    word_count = data.get("wordCount")
    if isinstance(word_count, bool) or not isinstance(word_count, int) or word_count < 0:
        raise ClipError("The extractor returned incomplete or invalid article data.")
    checked["wordCount"] = word_count
    if "ok" in data and data.get("ok") is not True:
        raise _extractor_failure(data)
    source = checked["canonicalUrl"] or checked["url"]
    if not isinstance(source, str):
        raise ClipError("The extractor returned incomplete or invalid article data.")
    normalize_url(source)
    normalize_url(str(checked["url"]))
    return checked


def _extractor_environment() -> dict[str, str]:
    allowed = (
        "HOME",
        "PATH",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TMPDIR",
        "TZ",
        "PLAYWRIGHT_BROWSERS_PATH",
    )
    child = {key: os.environ[key] for key in allowed if os.environ.get(key)}
    child.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    child.setdefault("HOME", str(Path.home()))
    return child


def _extractor_dir(plugin_root: Path) -> Path:
    """Locate the standalone Node.js extractor package.

    The extractor is a sibling of the Hermes plugin package in the source
    repository (``extractor/`` at the repo root, ``plugin/`` beside it). A
    legacy layout where the plugin lived directly at the repo root is also
    tolerated, in which case the extractor is a direct subdirectory of
    *plugin_root*.
    """
    direct = plugin_root / "extractor"
    if (direct / "src" / "cli.mjs").is_file():
        return direct
    return plugin_root.parent / "extractor"


def run_extractor(
    plugin_root: Path, url: str, no_browser: bool = False
) -> dict[str, object]:
    extractor_dir = _extractor_dir(plugin_root)
    command = ["node", str(extractor_dir / "src" / "cli.mjs"), url]
    if no_browser:
        command.append("--no-browser")
    result = _run_bounded(
        command,
        timeout=EXTRACTOR_TIMEOUT,
        stdout_limit=MAX_STDOUT_BYTES,
        stderr_limit=MAX_STDERR_BYTES,
        cwd=extractor_dir,
        env=_extractor_environment(),
    )
    try:
        payload = _decode_json_object(result.stdout)
    except ClipError:
        raise
    if result.returncode != 0 or payload.get("ok") is not True:
        raise _extractor_failure(payload)
    return _validate_success_payload(payload)


_WECHAT_HOST_RE = re.compile(r"(?:^|\.)weixin\.qq\.com$", re.IGNORECASE)
_WECHAT_TITLE_RE = re.compile(r"var\s+msg_title\s*=\s*[\"'](.+?)[\"']")
_WECHAT_AUTHOR_RE = re.compile(r"var\s+nickname\s*=\s*[\"'](.+?)[\"']")
_WECHAT_TIME_RE = re.compile(r"var\s+ct\s*=\s*[\"']?(\d+)[\"']?")
_WECHAT_BODY_RE = re.compile(
    r"<div[^>]+id=[\"']js_content[\"'][^>]*>(.*?)</div>",
    re.DOTALL | re.IGNORECASE,
)


def _is_wechat_url(url: str) -> bool:
    """Return True when *url* points to a WeChat public-account article."""
    try:
        return bool(_WECHAT_HOST_RE.search(urlsplit(url).hostname or ""))
    except Exception:
        return False


def _fetch_wechat_html(url: str, *, timeout: int = 30) -> str:
    """Fetch the raw HTML of a WeChat article via curl."""
    result = _run_bounded(
        [
            "curl",
            "-sL",
            "-H",
            "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
            "-H",
            "Accept-Language: zh-CN,zh;q=0.9",
            "--max-time",
            str(timeout),
            url,
        ],
        timeout=timeout + 5,
        stdout_limit=MAX_STDOUT_BYTES,
        stderr_limit=MAX_STDERR_BYTES,
    )
    if result.returncode != 0:
        raise ClipError("WeChat article fetch failed.")
    html = result.stdout.decode("utf-8", errors="replace")
    if len(html) < 500:
        raise ClipError("WeChat article response is too short.")
    return html


def _wechat_html_to_markdown(html: str) -> str:
    """Convert WeChat article body HTML to Markdown."""
    text = html
    # Images: <img data-src="URL"> or <img src="URL">
    text = re.sub(
        r'<img[^>]+(?:data-src|src)=["\']([^"\']+)["\'][^>]*alt=["\']([^"\']*)["\'][^>]*/?>',
        r"![\2](\1)",
        text,
        flags=re.IGNORECASE,
    )
    # Images without alt text
    text = re.sub(
        r'<img[^>]+(?:data-src|src)=["\']([^"\']+)["\'][^>]*/?>',
        r"![](\1)",
        text,
        flags=re.IGNORECASE,
    )
    # Headings
    for level in range(6, 0, -1):
        prefix = "#" * level
        text = re.sub(
            rf"<h{level}[^>]*>(.*?)</h{level}>",
            rf"\n{prefix} \1\n",
            text,
            flags=re.DOTALL | re.IGNORECASE,
        )
    # Bold and italic
    text = re.sub(
        r"<strong[^>]*>(.*?)</strong>",
        r"**\1**",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    text = re.sub(
        r"<em[^>]*>(.*?)</em>",
        r"*\1*",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    # Line breaks and paragraphs
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<p[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "\n", text, flags=re.IGNORECASE)
    # Strip remaining HTML tags
    text = re.sub(r"<[^>]+>", "", text)
    # Unescape HTML entities
    for old, new in (
        ("&amp;", "&"),
        ("&lt;", "<"),
        ("&gt;", ">"),
        ("&quot;", '"'),
        ("&#39;", "'"),
        ("&nbsp;", " "),
    ):
        text = text.replace(old, new)
    # Collapse excessive blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _count_words(markdown: str) -> int:
    """Count words/tokens in Markdown text (CJK characters counted individually)."""
    return len(re.findall(r"[\u4e00-\u9fff\u3400-\u4dbf]|\w+", markdown, re.UNICODE))


def _parse_wechat_html(html: str, url: str) -> dict[str, object]:
    """Parse metadata and body from a WeChat article HTML page."""
    title_m = _WECHAT_TITLE_RE.search(html)
    author_m = _WECHAT_AUTHOR_RE.search(html)
    time_m = _WECHAT_TIME_RE.search(html)

    title = title_m.group(1) if title_m else ""
    author = author_m.group(1) if author_m else ""
    publish_time = ""
    if time_m:
        try:
            publish_time = datetime.fromtimestamp(
                int(time_m.group(1)), tz=timezone.utc
            ).strftime("%Y-%m-%d")
        except (ValueError, OSError):
            pass

    body_m = _WECHAT_BODY_RE.search(html)
    body_html = body_m.group(1) if body_m else ""

    if not title or not body_html:
        raise ClipError("WeChat article content could not be parsed.")

    markdown = _wechat_html_to_markdown(body_html)
    return {
        "ok": True,
        "title": title,
        "author": author,
        "published": publish_time,
        "description": "",
        "site": "mp.weixin.qq.com",
        "canonicalUrl": url,
        "url": url,
        "keywords": [],
        "markdown": markdown,
        "wordCount": _count_words(markdown),
        "method": "wechat-curl",
    }


def run_extractor_with_fallback(
    plugin_root: Path, url: str, no_browser: bool = False
) -> dict[str, object]:
    """Run the Node.js extractor; fall back to curl for WeChat URLs on failure."""
    try:
        return run_extractor(plugin_root, url, no_browser=no_browser)
    except ClipError:
        if not _is_wechat_url(url):
            raise
        html = _fetch_wechat_html(url)
        return _parse_wechat_html(html, url)


def _decode_output(result: ProcessResult) -> str:
    try:
        return result.stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ClipError("Git returned an invalid local response.") from exc


def _status_paths(raw: bytes) -> set[str]:
    try:
        chunks = raw.decode("utf-8").split("\0")
    except UnicodeDecodeError as exc:
        raise ClipError("Git returned an invalid status response.") from exc
    paths: set[str] = set()
    index = 0
    while index < len(chunks):
        entry = chunks[index]
        index += 1
        if not entry:
            continue
        if len(entry) < 4 or entry[2] != " ":
            raise ClipError("Git returned an invalid status response.")
        status = entry[:2]
        paths.add(entry[3:])
        if "R" in status or "C" in status:
            if index >= len(chunks) or not chunks[index]:
                raise ClipError("Git returned an invalid status response.")
            paths.add(chunks[index])
            index += 1
    return paths


class VaultLock:
    """A non-blocking cross-process lock shared by all Vault writers."""

    def __init__(self, path: Path):
        self.path = path.expanduser().resolve()
        self._handle = None

    def __enter__(self) -> "VaultLock":
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            self._handle = self.path.open("a+", encoding="utf-8")
            os.chmod(self.path, 0o600)
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._handle.seek(0)
            self._handle.truncate()
            self._handle.write(f"pid={os.getpid()}\n")
            self._handle.flush()
        except BlockingIOError as exc:
            if self._handle is not None:
                self._handle.close()
                self._handle = None
            raise ClipError("Another Vault write operation is already running.") from exc
        except OSError as exc:
            if self._handle is not None:
                self._handle.close()
                self._handle = None
            raise ClipError("Could not acquire the shared Vault write lock.") from exc
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self._handle is None:
            return
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None


class GitSync:
    """A preflight-approved Git repository that may receive one clip note."""

    def __init__(self, vault: Path, repo_root: Path, branch: str):
        self.vault = vault
        self.repo_root = repo_root
        self.branch = branch

    @staticmethod
    def _git(repo: Path, *args: str, timeout: int = GIT_TIMEOUT) -> ProcessResult:
        return _run_bounded(
            ["git", "-C", str(repo), *args],
            timeout=timeout,
            stdout_limit=MAX_GIT_OUTPUT_BYTES,
            stderr_limit=MAX_STDERR_BYTES,
        )

    @classmethod
    def preflight(
        cls, vault: Path, expected_branch: str | None = None
    ) -> "GitSync":
        vault = vault.resolve()
        top = cls._git(vault, "rev-parse", "--show-toplevel")
        if top.returncode != 0:
            raise ClipError("Git protection requires the vault to be a Git repository.")
        repo_root = Path(_decode_output(top).strip()).resolve()
        _require_within(vault, repo_root, "The vault Git repository is invalid.")

        branch_result = cls._git(repo_root, "symbolic-ref", "--quiet", "--short", "HEAD")
        if branch_result.returncode != 0:
            raise ClipError("Git protection requires an active branch.")
        branch = _decode_output(branch_result).strip()
        if expected_branch is not None and branch != expected_branch:
            raise ClipError("The Vault is not on the configured clip sync branch.")

        for state_name in (
            "MERGE_HEAD",
            "CHERRY_PICK_HEAD",
            "REVERT_HEAD",
            "rebase-merge",
            "rebase-apply",
            "sequencer",
        ):
            state = cls._git(repo_root, "rev-parse", "--git-path", state_name)
            if state.returncode != 0:
                raise ClipError("Could not verify the Git operation state.")
            state_path = Path(_decode_output(state).strip())
            if not state_path.is_absolute():
                state_path = repo_root / state_path
            if state_path.exists():
                raise ClipError("Refusing to clip during an in-progress Git operation.")

        status = cls._git(
            repo_root, "status", "--porcelain=v1", "-z", "--untracked-files=all"
        )
        if status.returncode != 0:
            raise ClipError("Could not verify the Git worktree state.")
        if status.stdout:
            raise ClipError("Git protection requires an entirely clean worktree.")
        if expected_branch is not None:
            remote = cls._git(repo_root, "remote", "get-url", "origin")
            if remote.returncode != 0:
                raise ClipError("Git protection requires an origin remote.")
            fetched = cls._git(repo_root, "fetch", "--prune", "origin")
            if fetched.returncode != 0:
                raise ClipError("Could not fetch the Vault Git remote.")
            upstream = cls._git(
                repo_root,
                "rev-parse",
                "--abbrev-ref",
                "--symbolic-full-name",
                "@{u}",
            )
            if upstream.returncode != 0:
                raise ClipError("The clip sync branch has no upstream.")
            rebased = cls._git(repo_root, "rebase", _decode_output(upstream).strip())
            if rebased.returncode != 0:
                cls._git(repo_root, "rebase", "--abort")
                raise ClipError("Vault Git rebase conflicted; no clip was written.")
        return cls(vault, repo_root, branch)

    def _relative_paths(self, generated_paths: Sequence[Path]) -> set[str]:
        relative: set[str] = set()
        for path in generated_paths:
            resolved = path.resolve()
            _require_within(resolved, self.vault, "Generated note is outside the vault.")
            _require_within(resolved, self.repo_root, "Generated note is outside Git.")
            relative.add(resolved.relative_to(self.repo_root).as_posix())
        if not relative:
            raise ClipError("No generated note was supplied to Git.")
        return relative

    def finalize(
        self, generated_paths: Sequence[Path], *, commit_message: str | None = None
    ) -> GitOutcome:
        """Synchronize generated paths, preserving the note on every Git failure."""
        try:
            return self._finalize(generated_paths, commit_message=commit_message)
        except ClipError:
            return GitOutcome("verification_failed", "not_attempted")

    def _finalize(
        self, generated_paths: Sequence[Path], *, commit_message: str | None = None
    ) -> GitOutcome:
        expected = self._relative_paths(generated_paths)
        status = self._git(
            self.repo_root,
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
        )
        if status.returncode != 0:
            return GitOutcome("verification_failed", "not_attempted")
        try:
            changed = _status_paths(status.stdout)
        except ClipError:
            return GitOutcome("verification_failed", "not_attempted")
        if not changed.issubset(expected):
            return GitOutcome("refused", "not_attempted")
        if not changed:
            return GitOutcome("unchanged", "not_needed")

        add = self._git(self.repo_root, "add", "--", *sorted(changed))
        if add.returncode != 0:
            return GitOutcome("stage_failed", "not_attempted")

        staged = self._git(self.repo_root, "diff", "--cached", "--name-only", "-z")
        unstaged = self._git(self.repo_root, "diff", "--name-only", "-z")
        if staged.returncode != 0 or unstaged.returncode != 0:
            return GitOutcome("verification_failed", "not_attempted")
        staged_paths = {part for part in _decode_output(staged).split("\0") if part}
        unstaged_paths = {part for part in _decode_output(unstaged).split("\0") if part}
        if staged_paths != changed or unstaged_paths:
            return GitOutcome("verification_failed", "not_attempted")

        commit = self._git(
            self.repo_root,
            "commit",
            "-m",
            commit_message or "clip: save web article",
        )
        if commit.returncode != 0:
            return GitOutcome("commit_failed", "not_attempted")
        committed = self._git(
            self.repo_root,
            "diff-tree",
            "--no-commit-id",
            "--name-only",
            "-z",
            "-r",
            "HEAD",
        )
        post_status = self._git(
            self.repo_root,
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
        )
        if committed.returncode != 0 or post_status.returncode != 0:
            return GitOutcome("committed_unverified", "not_attempted")
        committed_paths = {
            part for part in _decode_output(committed).split("\0") if part
        }
        if committed_paths != changed or post_status.stdout:
            return GitOutcome("committed_unverified", "not_attempted")
        push = self._git(self.repo_root, "push", "-u", "origin", "HEAD")
        if push.returncode != 0:
            return GitOutcome("committed", "push_failed")
        return GitOutcome("committed", "pushed")


_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
)


def _ensure_no_secret_markers(note: str) -> None:
    if any(pattern.search(note) for pattern in _SECRET_PATTERNS):
        raise ClipError("The extracted page contains a credential-like marker; refusing to save it.")


class ClipService:
    def __init__(self, plugin_root: Path, env: Mapping[str, str] | None = None):
        self.plugin_root = plugin_root.resolve()
        self.env = env

    def _load_config(self) -> ClipConfig:
        return (
            ClipConfig.from_env(self.env)
            if self.env is not None
            else ClipConfig.from_file(self.plugin_root / "config.toml")
        )

    def run(self, raw_args: str) -> ClipResult | PendingClipResult:
        options = parse_clip_args(raw_args)
        config = self._load_config()
        with VaultLock(config.lock_file):
            return self._run_locked(options, config)

    def resume_pending(self, decision: str) -> ClipResult:
        normalized = decision.strip().lower()
        if normalized not in {"yes", "no"}:
            raise ClipError("The resume tool requires decision=yes or decision=no.")
        config = self._load_config()
        with VaultLock(config.lock_file):
            return self._resume_locked(config, normalized)

    def _ensure_destination(self, config: ClipConfig) -> Path:
        try:
            config.destination.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ClipError("Could not create the configured clip destination.") from exc
        destination = config.destination.resolve()
        _require_within(
            destination,
            config.vault,
            "Configured destination escaped the Obsidian vault.",
        )
        return destination

    @staticmethod
    def _publish_date(article: Mapping[str, object], fallback: str) -> str:
        """Return the article's published date if valid, otherwise *fallback*."""
        published = str(article.get("published") or "").strip()
        if published:
            # Accept full ISO timestamps like "2026-05-20T01:20:46+00:00" and bare dates
            date_part = published[:10]
            try:
                datetime.strptime(date_part, "%Y-%m-%d")
                return date_part
            except ValueError:
                pass
        return fallback

    def _target_for(self, config: ClipConfig, article: Mapping[str, object], capture_date: str) -> Path:
        destination = self._ensure_destination(config)
        source = str(article["canonicalUrl"] or article["url"])
        date_prefix = self._publish_date(article, capture_date)
        return choose_target(
            destination,
            str(article["title"]),
            source,
            capture_date=date_prefix,
        )

    def _result_with_github_url(
        self,
        config: ClipConfig,
        target: Path,
        outcome: GitOutcome,
        git_sync: GitSync | None,
    ) -> ClipResult:
        relative_path = target.resolve().relative_to(config.vault).as_posix()
        github_url = None
        if git_sync is not None:
            remote = GitSync._git(git_sync.repo_root, "remote", "get-url", "origin")
            if remote.returncode == 0:
                github_url = github_blob_url(
                    _decode_output(remote).strip(),
                    git_sync.branch,
                    relative_path,
                )
        return ClipResult(relative_path, outcome.commit_state, outcome.push_state, github_url)

    def _persist_article(
        self,
        *,
        config: ClipConfig,
        article: Mapping[str, object],
        captured_at: datetime,
        refresh: bool,
        git_sync: GitSync | None,
        content_markdown: str,
        image_mode: str | None,
        generated_paths: Sequence[Path] = (),
    ) -> ClipResult:
        source = str(article["canonicalUrl"] or article["url"])
        target = self._target_for(config, article, captured_at.date().isoformat())
        note = render_note(
            article,
            created=captured_at.isoformat(timespec="seconds"),
            content_markdown=content_markdown,
            image_mode=image_mode,
        )
        _ensure_no_secret_markers(note)
        write_managed_note(target, note, refresh=refresh)
        short_title = str(article.get("title", "web article"))[:60]
        commit_msg = f"clip: {short_title}"
        outcome = (
            GitOutcome("disabled", "disabled")
            if git_sync is None
            else git_sync.finalize(
                [target, *generated_paths], commit_message=commit_msg
            )
        )
        return self._result_with_github_url(config, target, outcome, git_sync)

    def _run_locked(
        self, options: ClipOptions, config: ClipConfig
    ) -> ClipResult | PendingClipResult:
        git_sync = (
            None
            if options.no_git
            else GitSync.preflight(config.vault, config.sync_branch)
        )
        article = _validate_success_payload(
            run_extractor_with_fallback(
                self.plugin_root, options.url, no_browser=options.no_browser
            )
        )
        captured_at = datetime.now(timezone.utc)
        source_markdown = sanitize_markdown(str(article["markdown"])).rstrip("\n")
        remote_images = find_remote_images(source_markdown)
        if options.save_images == "ask" and remote_images:
            pending_id = _url_hash(str(article["canonicalUrl"] or article["url"]))[:16]
            state = PendingClipState(
                pending_id=pending_id,
                created_at=captured_at.isoformat(timespec="seconds"),
                expires_at=(
                    captured_at.replace(microsecond=0)
                    + timedelta(seconds=PENDING_TTL_SECONDS)
                ).isoformat(),
                article=article,
                refresh=options.refresh,
                no_git=options.no_git,
                vault=str(config.vault),
                destination=str(config.destination),
                images=str(config.images),
                sync_branch=config.sync_branch,
            )
            _store_pending_state(_pending_root(self.env), state)
            return PendingClipResult(str(article["title"]), len(remote_images))
        content_markdown = source_markdown
        image_mode = None
        generated_paths: list[Path] = []
        if options.save_images == "yes" and remote_images:
            target = self._target_for(config, article, captured_at.date().isoformat())
            content_markdown, generated_paths = _localize_images(
                source_markdown,
                str(article["title"]),
                str(article["canonicalUrl"] or article["url"]),
                config.images.resolve(),
                target,
            )
            image_mode = "local"
        elif remote_images:
            image_mode = "remote"
        try:
            return self._persist_article(
                config=config,
                article=article,
                captured_at=captured_at,
                refresh=options.refresh,
                git_sync=git_sync,
                content_markdown=content_markdown,
                image_mode=image_mode,
                generated_paths=generated_paths,
            )
        except ClipError:
            if generated_paths:
                _cleanup_localized_files(generated_paths, images_root=config.images.resolve())
            raise

    def _resume_locked(self, config: ClipConfig, decision: str) -> ClipResult:
        pending_root = _pending_root(self.env)
        state = _load_pending_state(pending_root)
        if not state.matches_config(config):
            raise ClipError(
                "The pending image confirmation belongs to a different vault or clip configuration."
            )
        article = _validate_success_payload(state.article)
        git_sync = None if state.no_git else GitSync.preflight(config.vault, config.sync_branch)
        captured_at = datetime.fromisoformat(state.created_at)
        source_markdown = sanitize_markdown(str(article["markdown"])).rstrip("\n")
        content_markdown = source_markdown
        image_mode = "remote"
        generated_paths: list[Path] = []
        if decision == "yes":
            target = self._target_for(config, article, captured_at.date().isoformat())
            content_markdown, generated_paths = _localize_images(
                source_markdown,
                str(article["title"]),
                str(article["canonicalUrl"] or article["url"]),
                config.images.resolve(),
                target,
            )
            image_mode = "local"
        try:
            result = self._persist_article(
                config=config,
                article=article,
                captured_at=captured_at,
                refresh=state.refresh,
                git_sync=git_sync,
                content_markdown=content_markdown,
                image_mode=image_mode,
                generated_paths=generated_paths,
            )
        except ClipError:
            if generated_paths:
                _cleanup_localized_files(generated_paths, images_root=config.images.resolve())
            raise
        _clear_pending(pending_root, state.pending_id)
        return result


def build_handler(plugin_root: Path):
    """Create the exception boundary required by Hermes slash commands."""
    service = ClipService(plugin_root)

    def handle(raw_args: str) -> str:
        try:
            return service.run(raw_args).user_message()
        except ClipError as exc:
            return f"Clip failed: {exc}"
        except BaseException:
            # The slash-command boundary must never leak stacks, stderr, or secrets.
            return "Clip failed due to an unexpected local error."

    return handle


def build_resume_tool(plugin_root: Path):
    service = ClipService(plugin_root)

    def handle(
        arguments: Mapping[str, object] | None = None,
        decision: str | None = None,
        **_: object,
    ) -> str:
        chosen = decision
        if chosen is None and isinstance(arguments, Mapping):
            raw = arguments.get("decision")
            if isinstance(raw, str):
                chosen = raw
        try:
            return service.resume_pending(chosen or "").user_message()
        except ClipError as exc:
            return f"Clip failed: {exc}"
        except BaseException:
            return "Clip failed due to an unexpected local error."

    return handle
