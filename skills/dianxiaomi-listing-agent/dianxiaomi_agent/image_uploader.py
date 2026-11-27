from __future__ import annotations

import mimetypes
from pathlib import Path

from .net import multipart_body, request_json
from .utils import is_url, local_path


class ImageUploader:
    """Upload local images using the supplied upload.php contract."""

    def __init__(self, upload_url: str, timeout: int = 60, batch_size: int = 10):
        self.upload_url = upload_url
        self.timeout = timeout
        self.batch_size = min(max(batch_size, 1), 30)

    def resolve(self, values: list[str]) -> list[str]:
        output = [""] * len(values)
        pending_indices: list[int] = []
        pending: list[Path] = []
        for index, value in enumerate(values):
            if is_url(value):
                output[index] = value
            else:
                pending_indices.append(index)
                pending.append(local_path(value))

        for index, url in zip(pending_indices, self._upload_batches(pending)):
            output[index] = url
        return output

    def _upload_batches(self, paths: list[Path]) -> list[str]:
        urls: list[str] = []
        for start in range(0, len(paths), self.batch_size):
            batch = paths[start : start + self.batch_size]
            files: list[tuple[str, str, bytes, str]] = []
            for path in batch:
                if not path.is_file():
                    raise ValueError(f"图片不存在: {path}")
                content = path.read_bytes()
                mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                files.append(("images[]", path.name, content, mime))
            body, content_type = multipart_body({}, files)
            payload = request_json(
                self.upload_url,
                method="POST",
                headers={"Content-Type": content_type, "Accept": "application/json"},
                body=body,
                timeout=self.timeout,
            )

            if payload.get("success") is not True:
                raise RuntimeError(payload.get("message") or "图片上传失败")

            results = payload.get("results")
            if not isinstance(results, list) or len(results) != len(batch):
                raise RuntimeError("图片服务器返回数量与上传数量不一致")

            batch_urls: list[str] = []
            for path, result in zip(batch, results):
                if not isinstance(result, dict) or result.get("success") is not True or not result.get("url"):
                    raise RuntimeError(f"图片上传失败: {path.name}，{result}")
                url = str(result["url"]).strip()
                if not is_url(url):
                    raise RuntimeError(f"图片上传接口未返回有效 URL: {path.name}")
                batch_urls.append(url)
            urls.extend(batch_urls)
        return urls
