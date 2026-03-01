from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict


SUBDIRS = ["contents", "outputs", "logs", "cache", "manifests"]


@dataclass
class WorkspaceManager:
    root_dir: Path

    def user_root(self, username: str) -> Path:
        return self.root_dir / username

    def resolve_paths(self, username: str) -> Dict[str, Path]:
        ur = self.user_root(username)
        return {
            "user_root": ur,
            "contents": ur / "contents",
            "outputs": ur / "outputs",
            "logs": ur / "logs",
            "cache": ur / "cache",
            "manifests": ur / "manifests",
        }

    def ensure(self, username: str, with_topics: bool = True) -> Dict[str, Path]:
        paths = self.resolve_paths(username)
        for p in paths.values():
            p.mkdir(parents=True, exist_ok=True)

        if with_topics:
            (paths["contents"] / "faktaunik").mkdir(parents=True, exist_ok=True)
            (paths["contents"] / "automotif").mkdir(parents=True, exist_ok=True)
            (paths["contents"] / "custom").mkdir(parents=True, exist_ok=True)

        return paths

    def delete_workspace(self, username: str) -> None:
        ur = self.user_root(username)
        if ur.exists() and ur.is_dir():
            shutil.rmtree(ur)
