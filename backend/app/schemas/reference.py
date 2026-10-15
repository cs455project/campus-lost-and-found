"""Shapes for the reference lists: offices and categories."""

from pydantic import BaseModel, ConfigDict


class OfficeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    location_description: str
    opening_hours: str
    contact_phone: str | None
    is_main: bool


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    slug: str
