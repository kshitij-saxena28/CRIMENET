from pydantic import BaseModel, Field

class LoginIn(BaseModel):
    username: str = Field(max_length=100); password: str = Field(max_length=200)
class EntityIn(BaseModel):
    external_id: str; name: str; entity_type: str; confidence: float = Field(1, ge=0, le=1)
    attributes: dict = Field(default_factory=dict); latitude: float | None = None; longitude: float | None = None
    case_number: str = Field(default="", max_length=100)
class RelationshipIn(BaseModel):
    source_id: str; target_id: str; relation_type: str; confidence: float = Field(1, ge=0, le=1)
    source_ref: str = ""; event_time: str | None = None; metadata: dict = Field(default_factory=dict); verification_state: str = "unreviewed"
    case_number: str = Field(default="", max_length=100)
class EventIn(BaseModel):
    event_id: str; event_type: str; entity_id: str; related_entity_id: str = ""; event_time: str | None = None
    latitude: float | None = None; longitude: float | None = None; amount: float | None = None
    duration_seconds: float | None = None; source_ref: str = ""; metadata: dict = Field(default_factory=dict)
    case_number: str = Field(default="", max_length=100)
class CaseIn(BaseModel):
    case_number: str = Field(max_length=100); title: str = Field(max_length=255); summary: str = Field(default="", max_length=5000); status: str = "Active"
class TaskIn(BaseModel): case_number: str; title: str; assignee: str = ""
class CopilotIn(BaseModel): query: str = Field(min_length=1, max_length=1000); case_number: str | None = None
class AlertUpdate(BaseModel): status: str
class VerifyIn(BaseModel): status: str; actor: str = ""

class DocumentReviewIn(BaseModel):
    status: str
    entities: list[dict] = Field(default_factory=list)
    relationships: list[dict] = Field(default_factory=list)

class CaseScopeIn(BaseModel):
    case_number: str | None = None


class UserCreateIn(BaseModel):
    username: str = Field(min_length=3, max_length=100)
    password: str = Field(min_length=6, max_length=200)
    role: str = "investigator"

class PasswordChangeIn(BaseModel):
    current_password: str = ""
    new_password: str = Field(min_length=6, max_length=200)

class UsernameChangeIn(BaseModel):
    current_password: str = ""
    new_username: str = Field(min_length=3, max_length=100)

class NoteIn(BaseModel):
    case_number: str
    body: str = Field(min_length=1, max_length=5000)

class RoleChangeIn(BaseModel):
    role: str

class AdminResetPasswordIn(BaseModel):
    new_password: str = Field(min_length=6, max_length=200)


class CaseMemberIn(BaseModel):
    username: str = Field(min_length=3, max_length=100)

class CaseVisibilityIn(BaseModel):
    visibility: str = Field(pattern="^(restricted|shared)$")
