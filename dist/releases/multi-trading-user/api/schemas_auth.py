from __future__ import annotations

from pydantic import BaseModel, Field


class AuthRegisterBody(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=6, max_length=128)


class AuthLoginBody(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class AuthApiKeyCreateBody(BaseModel):
    name: str = Field(default="default", min_length=1, max_length=128)

