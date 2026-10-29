#!/usr/bin/env python3
"""Generate the small CNB config stored in each user's kernel_build repository."""
import argparse
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit


def render(repo_url, ref, image):
    url = urlsplit(repo_url)
    if not (url.scheme == "https" and url.hostname and not url.username and not url.password
            and not url.query and not url.fragment and not any(c.isspace() for c in repo_url)):
        raise ValueError("脚本仓库必须是无凭据的 HTTPS 地址")
    if not re.fullmatch(r"[a-fA-F0-9]{40}|[a-fA-F0-9]{64}", ref):
        raise ValueError("--ref 必须是脚本仓库完整提交 SHA，不能填写 main 或 latest")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9./:@_-]+", image):
        raise ValueError("镜像地址不合法")
    template = (Path(__file__).resolve().parents[1] / "examples/kernel_build.cnb.yml.in").read_text(encoding="utf-8")
    for key, value in {"BUILDER_REPO_URL": repo_url, "BUILDER_REF": ref.lower(), "BUILDER_IMAGE": image}.items():
        template = template.replace("@@" + key + "@@", json.dumps(value))
    return template


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-url", required=True)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    try:
        config = render(args.repo_url, args.ref, args.image)
        with open(args.output, "w" if args.force else "x", encoding="utf-8") as stream:
            stream.write(config)
        print(f"已生成 {args.output}")
    except (OSError, ValueError) as error:
        parser.exit(1, f"配置生成失败：{error}\n")
