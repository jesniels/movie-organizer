"""Pydantic request models for the API."""
from __future__ import annotations

from copy import deepcopy
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field, model_validator

from .settings import DEFAULT_NAMING, DEFAULT_TRANSCODE

Encoder     = Literal["hevc_amf", "hevc_nvenc", "libx265"]
AudioPrefer = Literal["first", "default", "channels"]


def _default(key: str, **constraints):
    return Field(default_factory=lambda: deepcopy(DEFAULT_TRANSCODE[key]), **constraints)


class TranscodeConfig(BaseModel):
    ffmpeg_path:        str         = _default("ffmpeg_path")
    ffprobe_path:       str         = _default("ffprobe_path")
    output_root:        str         = _default("output_root")
    encoder:            Encoder     = _default("encoder")
    qp:                 int         = _default("qp", ge=0, le=51)
    audio_languages:    List[str]   = _default("audio_languages")
    audio_reject_words: List[str]   = _default("audio_reject_words")
    audio_prefer:       AudioPrefer = _default("audio_prefer")


class NamingConfig(BaseModel):
    movie_folder:       str  = DEFAULT_NAMING["movie_folder"]
    movie_file:         str  = DEFAULT_NAMING["movie_file"]
    series_folder:      str  = DEFAULT_NAMING["series_folder"]
    file_equals_folder: bool = DEFAULT_NAMING["file_equals_folder"]
    rename_files:       bool = DEFAULT_NAMING["rename_files"]
    resolve_nfos:       bool = DEFAULT_NAMING["resolve_nfos"]
    delete_images:      bool = DEFAULT_NAMING["delete_images"]
    sanitize_names:     bool = DEFAULT_NAMING["sanitize_names"]

    @model_validator(mode="after")
    def _sync_file_to_folder(self):
        if self.file_equals_folder:
            self.movie_file = self.movie_folder
        return self


class ConfigBody(BaseModel):
    locations: List[str]
    downloads: List[str]
    transcode: Optional[TranscodeConfig] = None
    naming:    Optional[NamingConfig] = None       # None → leave unchanged (older frontend)
    server_mode:  Optional[bool] = None            # None → leave unchanged (older frontend)
    local_shares: Optional[Dict[str, str]] = None  # server path → local share path


class TranscodeToolsBody(BaseModel):
    ffmpeg_path:  str = ""
    ffprobe_path: str = ""


class EncoderTestBody(BaseModel):
    ffmpeg_path: str
    encoders:    List[Encoder] = Field(min_length=1)
    qp:          int = Field(ge=0, le=51)


class TranscodeProbeBody(BaseModel):
    item_ids: List[str]


class TranscodeFile(BaseModel):
    item_id:     str
    src:         str
    audio_index: int = Field(ge=0)


class TranscodeStartBody(BaseModel):
    files: List[TranscodeFile]


class UseTranscodedBody(BaseModel):
    original_id:   str
    transcoded_id: str
    dry_run:       bool = True


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


class ReorganizeProposalsBody(BaseModel):
    item_ids: List[str]   # the items shown by the current left-bar filters


class ReorganizeChange(BaseModel):
    """The user's choices for one proposal row; everything else is recomputed server-side."""
    item_id:       Optional[str] = None   # None → the “Parent folders” group (sanitize only)
    folder_name:   Optional[str] = None   # edited folder name; None → the analysed one
    file_name:     Optional[str] = None   # edited video stem (extension kept); None → the analysed one
    apply_folder:  bool = False
    apply_file:    bool = False
    apply_nfo:     bool = False           # check 4a: rename the lone NFO to <video>.nfo
    nfo_action:    Literal["use_best", "delete_all", "leave"] = "leave"
    nfo_files:     List[str] = []         # the NFO names the user saw (re-checked before any NFO action)
    delete_images: bool = False
    sanitize:      List[str] = []         # paths of the selected sanitize entries


class ReorganizeBody(BaseModel):
    changes: List[ReorganizeChange]
    dry_run: bool = True
