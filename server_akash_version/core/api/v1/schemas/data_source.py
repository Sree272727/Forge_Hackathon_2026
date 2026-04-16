"""Schemas for data source upload and metadata responses."""

from datetime import datetime

from pydantic import BaseModel, Field


class DataSourceResponse(BaseModel):
    """Response schema for a user data source."""

    id: str
    user_id: str
    name: str
    original_file_name: str
    mime_type: str | None
    file_extension: str
    file_size_bytes: int
    sheet_count: int
    sheet_names: list[str]
    file_checksum_sha256: str
    meta_info: dict = Field(default_factory=dict)
    created_at: datetime

    model_config = {"from_attributes": True}


class DataSourceListResponse(BaseModel):
    """List response schema for data sources."""

    items: list[DataSourceResponse]
    total: int
