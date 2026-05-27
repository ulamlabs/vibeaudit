from dataclasses import dataclass


@dataclass
class InstallationInfo:
    account_login: str
    account_type: str


@dataclass
class RepoInfo:
    id: int
    full_name: str
    private: bool
    description: str
