from pydantic import BaseModel, ConfigDict, Field, field_validator


class AccountInput(BaseModel):
    name: str = Field(default="GHelper", min_length=1, max_length=80)
    token: str = Field(pattern=r"^[a-fA-F0-9]{32}$")

    @field_validator("name")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("名称不能为空")
        return value.strip()


class ImportInput(BaseModel):
    accounts: list[AccountInput] = Field(min_length=1, max_length=100)


class AccountPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=80)
    token: str | None = Field(default=None, pattern=r"^[a-fA-F0-9]{32}$")
    enabled: bool | None = None


class SubscriptionInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    account_ids: list[str] = Field(min_length=1, max_length=100)
    regions: list[str] = Field(default_factory=list, max_length=100)
    exclude_failed: bool = False
    enabled: bool = True


class ProbeInput(BaseModel):
    node_ids: list[str] = Field(min_length=1, max_length=5000)


class LoginInput(BaseModel):
    username: str = Field(max_length=100)
    password: str = Field(max_length=200)


class PreferencesInput(BaseModel):
    refresh_minutes: int = Field(ge=5, le=30)
