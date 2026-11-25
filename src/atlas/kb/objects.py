"""S3-compatible object storage (MinIO locally, S3/GCS in cloud) for raw + parsed artifacts.

Keeping raw bytes and parsed JSON means re-chunking or re-embedding never re-downloads or
re-parses: re-processing starts from the cheapest valid stage.
"""

from __future__ import annotations

import asyncio
import io

from minio import Minio
from minio.error import S3Error

from atlas.common.settings import Settings


class ObjectStore:
    def __init__(self, s: Settings) -> None:
        self.bucket = s.s3_bucket
        self.client = Minio(s.s3_endpoint, access_key=s.s3_access_key, secret_key=s.s3_secret_key.get_secret_value(),
                            secure=s.s3_secure)

    async def ensure_bucket(self) -> None:
        def _ensure() -> None:
            if not self.client.bucket_exists(self.bucket):
                self.client.make_bucket(self.bucket)
        await asyncio.to_thread(_ensure)

    async def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        await asyncio.to_thread(self.client.put_object, self.bucket, key, io.BytesIO(data), len(data),
                                content_type=content_type)

    async def get(self, key: str) -> bytes | None:
        def _get() -> bytes | None:
            try:
                resp = self.client.get_object(self.bucket, key)
            except S3Error as e:
                if e.code in ("NoSuchKey", "NoSuchObject"):
                    return None
                raise
            try:
                return resp.read()
            finally:
                resp.close()
                resp.release_conn()
        return await asyncio.to_thread(_get)

    async def delete_prefix(self, prefix: str) -> None:
        def _del() -> None:
            for obj in self.client.list_objects(self.bucket, prefix=prefix, recursive=True):
                self.client.remove_object(self.bucket, obj.object_name)
        await asyncio.to_thread(_del)
