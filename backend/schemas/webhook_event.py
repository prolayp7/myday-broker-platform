from typing import Literal

from pydantic import BaseModel

Source = Literal["meta", "tiktok"]


class WebhookEventRecord(BaseModel):
    source: Source
    dedup_key: str
    raw_payload: dict
    signature_valid: bool
