from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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
    account_ids: list[str] = Field(default_factory=list, max_length=100)
    source_ids: list[str] = Field(default_factory=list, max_length=100)
    regions: list[str] = Field(default_factory=list, max_length=100)
    exclude_failed: bool = False
    dedupe: bool = True
    max_latency: int | None = Field(default=None, ge=1, le=10000)
    max_per_country: int | None = Field(default=None, ge=1, le=500)
    enabled: bool = True

    @model_validator(mode="after")
    def has_origin(self):
        if not self.account_ids and not self.source_ids:
            raise ValueError("至少选择一个账号或订阅源")
        return self


class SourceInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    url: str = Field(min_length=8, max_length=2000)

    @field_validator("name", "url")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("内容不能为空")
        return value.strip()


class SourcePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=80)
    url: str | None = Field(default=None, min_length=8, max_length=2000)
    enabled: bool | None = None

    @field_validator("name", "url")
    @classmethod
    def nonblank(cls, value):
        if value is not None and not value.strip():
            raise ValueError("内容不能为空")
        return value.strip() if value is not None else None


class SourceFileInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    content: str = Field(min_length=1, max_length=5_000_000)

    @field_validator("name")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("名称不能为空")
        return value.strip()


class SourceContentInput(BaseModel):
    content: str = Field(min_length=1, max_length=5_000_000)


class SourceImportInput(BaseModel):
    sources: list[SourceInput] = Field(min_length=1, max_length=50)


class ProbeInput(BaseModel):
    node_ids: list[str] = Field(min_length=1, max_length=5000)


class SubscriptionProbeInput(BaseModel):
    keys: list[str] | None = Field(default=None, max_length=5000)  # 留空测全部节点


class LoginInput(BaseModel):
    username: str = Field(max_length=100)
    password: str = Field(max_length=200)


class PreferencesInput(BaseModel):
    refresh_minutes: int = Field(ge=5, le=30)
    probe_minutes: int = Field(default=30, ge=5, le=60)
    # 内核测速时单个节点的超时；超过即判为不可用
    probe_timeout_ms: int = Field(default=5000, ge=1000, le=10000)
