#!/usr/bin/env python3
"""Publish a verified result using CNB's upload URL / PUT / confirmation API."""
import json
import os
from pathlib import Path
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from builder_lib import https_url, require, sha256


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class CNBClient:
    def __init__(self):
        self.base = os.environ.get("CNB_API_ENDPOINT", "https://api.cnb.cool").rstrip("/")
        https_url(self.base, "CNB_API_ENDPOINT")
        require(not urlsplit(self.base).query and urlsplit(self.base).path in ("", "/"), "CNB API 地址只能包含 HTTPS 域名")
        self.token = os.environ.get("CNB_TOKEN", "")
        require(self.token and not any(c.isspace() for c in self.token), "上传需要 CNB 流水线临时令牌")
        repo = os.environ.get("CNB_REPO_SLUG", "")
        parts = repo.split("/")
        require(len(parts) >= 2 and all(p and p not in (".", "..") for p in parts) and not any(c in repo for c in "\\?#\r\n"), "CNB_REPO_SLUG 不合法")
        commit = os.environ.get("CNB_COMMIT", "")
        require(re.fullmatch(r"[a-fA-F0-9]{40}|[a-fA-F0-9]{64}", commit), "缺少完整 CNB_COMMIT")
        self.prefix = "/" + "/".join(quote(p, safe="") for p in parts) + "/-/git/commit-assets/" + commit
        self.opener = build_opener(NoRedirect)

    def api(self, method, path, body=None):
        headers = {"Authorization": "Bearer " + self.token, "Accept": "application/vnd.cnb.api+json"}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        request = Request(self.base + path, data=data, headers=headers, method=method)
        with self.opener.open(request, timeout=120) as response:
            raw = response.read(16 * 1024 * 1024 + 1)
            require(len(raw) <= 16 * 1024 * 1024, "CNB JSON 响应过大")
            return json.loads(raw) if raw else None

    def upload(self, url, path):
        https_url(url, "附件上传地址")
        # A pre-signed storage URL must never receive CNB Authorization headers.
        with open(path, "rb") as stream:
            request = Request(url, data=stream, method="PUT", headers={
                "Content-Type": "application/octet-stream", "Content-Length": str(path.stat().st_size)})
            with self.opener.open(request, timeout=1800) as response:
                require(200 <= response.status < 300, "对象存储未确认上传")

    def verify_path(self, url):
        parsed = urlsplit(urljoin(self.base + "/", url))
        base = urlsplit(self.base)
        require((parsed.scheme, parsed.netloc) == (base.scheme, base.netloc)
                and not parsed.fragment
                and parsed.path.startswith(self.prefix + "/asset-upload-confirmation/"), "拒绝向非当前 CNB 提交的地址发送上传确认和令牌")
        return parsed.path


def publish(kind):
    require(kind in ("prepare", "kernel"), "只允许发布 prepare 或 kernel 结果")
    name = f"source-{os.environ['REQUEST_ID']}.tar.gz" if kind == "prepare" else "boot.img"
    require(re.fullmatch(r"source-[A-Za-z0-9-]+\.tar\.gz|boot\.img", name), "附件名称不合法")
    path = Path(os.environ["ARTIFACT_DIR"]) / name
    require(path.is_file() and not path.is_symlink(), "缺少本次构建的有效附件")
    result = json.loads((Path(os.environ["STATE_DIR"]) / f"{kind}-success.json").read_text())
    require(result.get("request_id") == os.environ["REQUEST_ID"]
            and result.get("ci_commit") == os.environ["CI_COMMIT_SHA"]
            and result.get("build_id") == os.environ.get("CNB_BUILD_ID", "")
            and result.get("name") == name and result.get("sha256") == sha256(path), "附件缺少当前构建的校验成功记录，拒绝上传旧文件")
    client = CNBClient()
    require(os.environ["CI_COMMIT_SHA"] == os.environ["CNB_COMMIT"], "上传 commit 与构建 commit 不一致")
    ttl = os.environ.get("ARTIFACT_TTL_DAYS", "30")
    require(ttl.isdecimal() and 0 <= int(ttl) <= 180, "ARTIFACT_TTL_DAYS 必须为 0–180")
    if kind == "kernel":
        assets = client.api("GET", client.prefix)
        require(isinstance(assets, list), "CNB 未返回有效的附件列表")
        for asset in assets:
            if asset.get("name") == "boot.img":
                asset_id = asset.get("id")
                require(isinstance(asset_id, str) and asset_id, "旧附件缺少 ID，停止清理")
                client.api("DELETE", client.prefix + "/" + quote(asset_id, safe=""))
    urls = client.api("POST", client.prefix + "/asset-upload-url", {"asset_name": name, "size": path.stat().st_size, "ttl": int(ttl)})
    require(isinstance(urls, dict) and isinstance(urls.get("upload_url"), str) and isinstance(urls.get("verify_url"), str), "CNB 未返回完整的上传地址")
    verify = client.verify_path(urls["verify_url"])
    client.upload(urls["upload_url"], path)
    client.api("POST", verify + "?ttl=" + ttl)
    print(f"附件已上传并确认：{name}")


if __name__ == "__main__":
    try:
        publish(sys.argv[1])
    except HTTPError as error:
        # Upstream URLs may contain pre-signed secrets; do not print the URL/body.
        sys.exit(f"附件发布失败：HTTP {error.code}；请检查流水线权限和上传状态")
    except URLError:
        sys.exit("附件发布网络失败；请检查 CNB 附件状态后重试，旧文件可能已被清理")
    except (ValueError, OSError, KeyError) as error:
        sys.exit(f"附件发布失败：{error}")
