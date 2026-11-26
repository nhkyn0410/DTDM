"""Public GitHub repositories as the source of a deployed app (like Vercel's "import project").

The customer gives a link; minicloud builds the download address itself, always on
codeload.github.com, so a link cannot make the control plane fetch anything else (SSRF).
"""

import http.client
import re
import socket
from dataclasses import dataclass
from urllib.parse import quote, unquote, urlsplit

from ..errors import BadGateway, InvalidUpload, PayloadTooLarge

DOWNLOAD_HOST = "codeload.github.com"
OWNER = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
REPO = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
REF = re.compile(r"^[A-Za-z0-9._-]{1,100}$")  # one path segment: a branch, tag or commit
FOLDER = re.compile(r"^[A-Za-z0-9._ -]{1,100}$")
LINK_FORM = "https://github.com/<chủ>/<repo>, hoặc …/tree/<nhánh>/<thư mục>"


@dataclass(frozen=True)
class GitSource:
    owner: str
    repo: str
    ref: str = ""  # "" is the default branch
    subdir: str = ""  # only this folder of the repository is deployed

    @property
    def url(self) -> str:
        """The canonical link, stored with the deployment to fetch it again later."""
        link = f"https://github.com/{self.owner}/{self.repo}"
        if self.ref:
            link += f"/tree/{self.ref}"
            if self.subdir:
                link += f"/{self.subdir}"
        return link

    @property
    def label(self) -> str:
        text = f"github.com/{self.owner}/{self.repo}"
        if self.ref:
            text += f"@{self.ref}"
        if self.subdir:
            text += f"/{self.subdir}"
        return text


def parse_url(link: str) -> GitSource:
    """Read a github.com link; refuse anything else with a message for the customer."""
    raw = link.strip()
    if not raw:
        raise InvalidUpload("Chưa có link GitHub.")
    if "://" not in raw:
        raw = "https://" + raw
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError:
        raise InvalidUpload(f"Link không hợp lệ. Dạng đúng: {LINK_FORM}.") from None
    host = (parts.hostname or "").lower()
    if (
        parts.scheme not in ("http", "https")
        or host not in ("github.com", "www.github.com")
        or port is not None
        or parts.username is not None
    ):
        raise InvalidUpload(f"Chỉ nhận link repo công khai trên GitHub: {LINK_FORM}.")
    segments = [unquote(s) for s in parts.path.split("/") if s]
    if len(segments) < 2:
        raise InvalidUpload(f"Link cần có cả tên chủ và tên repo: {LINK_FORM}.")
    owner, repo = segments[0], segments[1].removesuffix(".git")
    if not OWNER.match(owner) or not REPO.match(repo) or repo in (".", ".."):
        raise InvalidUpload("Tên chủ hoặc tên repo trong link không hợp lệ.")
    rest = segments[2:]
    if not rest:
        return GitSource(owner, repo)
    if rest[0] != "tree" or len(rest) < 2:
        raise InvalidUpload(f"Link chỉ được trỏ tới repo, hoặc tới một nhánh: {LINK_FORM}.")
    ref, folders = rest[1], rest[2:]
    if not REF.match(ref) or ref in (".", ".."):
        raise InvalidUpload("Tên nhánh không hợp lệ (nhánh có dấu / chưa được hỗ trợ).")
    if any(not FOLDER.match(f) or f in (".", "..") for f in folders):
        raise InvalidUpload("Tên thư mục trong link không hợp lệ.")
    return GitSource(owner, repo, ref, "/".join(folders))


class _IPv4HTTPSConnection(http.client.HTTPSConnection):
    """Looks up A records only. On staging (WSL), an AAAA query for GitHub waits 15 s for
    nothing, while the A record comes back in 50 ms; the laptops have no IPv6 anyway."""

    def connect(self) -> None:
        last: OSError | None = None
        for *_, address in socket.getaddrinfo(
            self.host, self.port, socket.AF_INET, socket.SOCK_STREAM
        ):
            try:
                sock = socket.create_connection(address, self.timeout)
                break
            except OSError as exc:
                last = exc
        else:
            raise last or OSError(f"no IPv4 address for {self.host}")
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def download(source: GitSource, limit: int, timeout: float = 60.0) -> bytes:
    """The repository (or branch) as a zip, refused once it grows past `limit` bytes."""
    ref = quote(source.ref or "HEAD", safe="")
    path = f"/{quote(source.owner)}/{quote(source.repo)}/zip/{ref}"
    too_big = PayloadTooLarge(f"{source.label} nén lại lớn hơn {limit // 2**20} MB.")
    conn = _IPv4HTTPSConnection(DOWNLOAD_HOST, timeout=timeout)
    try:
        conn.request("GET", path, headers={"User-Agent": "minicloud-deploy"})
        resp = conn.getresponse()
        if resp.status == 404:
            raise InvalidUpload(
                f"Không tải được {source.label}: repo không tồn tại, không công khai, "
                "hoặc sai tên nhánh."
            )
        if resp.status != 200:
            raise BadGateway(f"GitHub trả lời HTTP {resp.status} khi tải {source.label}.")
        declared = resp.getheader("Content-Length") or ""
        if declared.isdigit() and int(declared) > limit:
            raise too_big
        data = bytearray()
        while chunk := resp.read(64 * 1024):
            data += chunk
            if len(data) > limit:
                raise too_big
        return bytes(data)
    except (OSError, http.client.HTTPException) as exc:
        raise BadGateway(f"Không tải được từ GitHub: {exc}") from None
    finally:
        conn.close()
