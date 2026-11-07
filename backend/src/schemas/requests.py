from pydantic import BaseModel, Field


class SetupRequest(BaseModel):
    camtrap_dir: str
    # Shown in Recent sessions and on the species page to tell sessions apart.
    session_name: str = Field("", max_length=120)
    image_base_dir: str = ""
    flat_search: bool = False
    output_dir: str = ""
    target_species: list[str]
    study_start: str
    study_end: str
    occasion_days: int = 5
    total_iterations: int = 100000
    gap_seconds: int = 60
    min_score: float = 0.5
    # Per-species overrides of min_score (scientificName -> threshold).
    min_score_by_species: dict[str, float] = {}
    include_burst_context: bool = False
    classified_by: str = "expert_review"
    extended_confirmation: bool = False


class DecisionsRequest(BaseModel):
    species: str
    iteration: int
    confirmed: list[str]
    unsure: list[str] = []


class CrossRequest(BaseModel):
    species: str
    site_id: str
    occasion: int
    media_id: str = ""
    observation_id: str = ""
    source_species: str = ""


class ProgressRequest(BaseModel):
    mode: str
    iteration: int
    decisions: dict[str, str] = {}
    chosen_rep: dict[str, str] = {}


class PendingDecisionsRequest(BaseModel):
    confirmed: list[str] = []
    rejected_keys: list[str] = []


class RelinkRequest(BaseModel):
    camtrap_dir: str
    image_base_dir: str = ""
    flat_search: bool = False


class ImportSessionRequest(BaseModel):
    zip_path: str


class RejectRequest(BaseModel):
    mediaId: str


class UnrejectRequest(BaseModel):
    media: list[str]


class ReviewDecisionsRequest(BaseModel):
    confirmed_keys: list[str]


class RenameSessionRequest(BaseModel):
    session_dir: str = ""          # empty: the active session
    name: str = Field("", max_length=120)


class LoadSessionRequest(BaseModel):
    session_dir: str


class TrapperLoginRequest(BaseModel):
    url: str
    username: str
    password: str


class TrapperGenerateRequest(BaseModel):
    classification_project_pk: int
    output_dir: str
    clear_cache: bool = False
