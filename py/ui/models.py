"""Pydantic request models for the API."""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel


class ConfigBody(BaseModel):
    locations: List[str]
    downloads: List[str]


class PlayBody(BaseModel):
    item_id: str
    file:    Optional[str] = None   # must be one of the item's files


class NfoCopyBody(BaseModel):
    source_id: str
    target_id: str
    fields:    Optional[List[str]] = None   # None/empty → copy whole file


class NotDuplicateBody(BaseModel):
    ids: List[str]


class NotDupRemoveBody(BaseModel):
    pairs: List[List[str]]


class MoveBody(BaseModel):
    item_ids:    List[str]
    target_base: str
    dry_run:     bool = True


class RenameBody(BaseModel):
    item_id:         str
    new_name:        str
    new_folder_name: Optional[str] = None   # used when rename_target=="both" to allow different folder vs file names
    rename_target:   str = "folder"         # "folder" | "files" | "both"
    dry_run:         bool = True


class DeleteBody(BaseModel):
    item_ids: List[str]
    dry_run:  bool = True
