"""Validation for the Profile and Workflow forms. Pure pydantic models, so
the rules can be tested without a web server or a database."""
import os
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from . import config, media, paths

LABELS = {
    "name": "Name", "target_fraction": "Target length",
    "event_start_sec": "Skip at start", "end_trim_sec": "Trim at end",
    "intro_sec": "Always keep after start", "outro_sec": "Always keep before end",
    "clip_before": "Clip padding before", "clip_after": "Clip padding after",
    "min_separation": "Minimum gap between moments", "languages": "Audio language preference",
    "video_preset": "Encoding speed", "video_crf": "Video quality", "video_encoder": "Encoder",
    "profile_id": "Profile", "source_path": "Source folder", "dest_path": "Destination folder",
}

PROFILE_DEFAULTS = {
    "name": "", "target_fraction": 0.25, "event_start_sec": 0, "end_trim_sec": 0,
    "intro_sec": 150, "outro_sec": 120, "clip_before": 20, "clip_after": 25,
    "min_separation": 60, "languages": "nld,eng", "delete_original": False, "reencode": False,
    "video_preset": media.DEFAULT_PRESET, "video_crf": media.DEFAULT_CRF,
    "video_encoder": media.ENCODER_SOFTWARE,
}


class ProfileForm(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    name: str = Field(min_length=1, max_length=100)
    target_fraction: float = Field(gt=0, le=1)
    event_start_sec: int = Field(ge=0)
    end_trim_sec: int = Field(ge=0)
    intro_sec: int = Field(ge=0)
    outro_sec: int = Field(ge=0)
    clip_before: int = Field(ge=0)
    clip_after: int = Field(ge=0)
    min_separation: int = Field(ge=0)
    languages: str = ""
    delete_original: bool = False   # checkboxes: absent from the form = False
    reencode: bool = False
    video_preset: Literal[*media.X264_PRESETS] = media.DEFAULT_PRESET
    video_encoder: Literal[*media.ENCODERS] = media.ENCODER_SOFTWARE
    video_crf: int = Field(default=media.DEFAULT_CRF, ge=media.CRF_RANGE[0], le=media.CRF_RANGE[1])

    @field_validator("languages")
    @classmethod
    def _normalise_languages(cls, v: str) -> str:
        codes = [c.strip().lower() for c in v.split(",")]
        return ",".join(c for c in codes if c)


class WorkflowForm(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    name: str = Field(min_length=1, max_length=100)
    profile_id: int
    source_path: str
    dest_path: str
    recursive: bool = False
    enabled: bool = False

    @field_validator("source_path", "dest_path")
    @classmethod
    def _check_path(cls, v: str) -> str:
        if not os.path.isabs(v):
            raise ValueError("must be an absolute path (starting with /)")
        v = os.path.normpath(v)
        if not paths.is_within_media(v):
            raise ValueError("must be inside a mounted media folder "
                             f"({', '.join(config.MEDIA_ROOTS)})")
        return v


def _friendly(err: dict) -> str:
    ctx = err.get("ctx", {})
    match err["type"]:
        case "missing" | "string_too_short":
            return "is required"
        case "int_parsing" | "int_from_float" | "int_type":
            return "must be a whole number"
        case "float_parsing" | "float_type":
            return "must be a number"
        case "greater_than":
            return f"must be greater than {ctx.get('gt')}"
        case "greater_than_equal":
            return f"must be at least {ctx.get('ge')}"
        case "less_than_equal":
            return f"must be at most {ctx.get('le')}"
        case "literal_error":
            return "is not one of the available choices"
        case "string_too_long":
            return f"must be at most {ctx.get('max_length')} characters"
        case "value_error":
            return str(err["msg"]).removeprefix("Value error, ")
        case _:
            return err["msg"]


def validate(model: type[BaseModel], data: dict) -> tuple[BaseModel | None, list[str]]:
    """Returns (parsed, []) or (None, [readable error, ...])."""
    try:
        return model.model_validate(data), []
    except ValidationError as exc:
        messages = []
        for err in exc.errors():
            field = err["loc"][0] if err["loc"] else None
            text = _friendly(err)
            messages.append(f"{LABELS.get(field, field)} {text}" if field else text)
        return None, messages
