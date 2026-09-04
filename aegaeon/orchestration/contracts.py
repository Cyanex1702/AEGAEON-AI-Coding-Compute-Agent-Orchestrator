from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from aegaeon.models.results import ContractProposal


class ContractApplicationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class MaterializedContract:
    path: str
    version: int
    content: str


class ProjectContractMaterializer:
    """Deterministically renders and semantically updates Core-owned contracts."""

    path = "contracts/CanonicalContract.json"

    def render(self, version: int, content: dict[str, Any]) -> MaterializedContract:
        payload = {"contract_version": version, **deepcopy(content)}
        return MaterializedContract(
            path=self.path,
            version=version,
            content=json.dumps(payload, indent=2, sort_keys=True) + "\n",
        )

    def apply(
        self,
        content: dict[str, Any],
        proposal: ContractProposal,
        current_version: int,
    ) -> tuple[dict[str, Any], object | None]:
        if proposal.expected_revision is not None and proposal.expected_revision != current_version:
            raise ContractApplicationError(
                f"proposal expected contract v{proposal.expected_revision}; "
                f"canonical is v{current_version}"
            )
        updated = deepcopy(content)
        container, leaf = self._resolve_target(updated, proposal.target, create=True)
        current = container.get(leaf)
        if proposal.current_value is not None and current != proposal.current_value:
            raise ContractApplicationError(
                f"proposal current value does not match canonical target {proposal.target}"
            )
        if proposal.type.startswith("add_") and leaf in container and current is not None:
            raise ContractApplicationError(f"contract target already exists: {proposal.target}")
        if proposal.type in {"remove", "rename", "change_type", "other"}:
            raise ContractApplicationError(
                f"proposal type {proposal.type} requires explicit advanced review"
            )
        container[leaf] = deepcopy(proposal.proposed_value)
        return updated, current

    @staticmethod
    def _resolve_target(
        content: dict[str, Any], target: str, *, create: bool
    ) -> tuple[dict[str, Any], str]:
        parts = [part for part in target.split(".") if part]
        if not parts:
            raise ContractApplicationError("contract proposal target is empty")
        roots = ("entities", "services", "api", "shared_types", "state", "ui")
        if parts[0] in roots:
            path = parts
        else:
            root = next(
                (
                    candidate
                    for candidate in roots
                    if isinstance(content.get(candidate), dict) and parts[0] in content[candidate]
                ),
                "shared_types",
            )
            path = [root, *parts]
        node: dict[str, Any] = content
        for part in path[:-1]:
            value = node.get(part)
            if value is None and create:
                value = {}
                node[part] = value
            if not isinstance(value, dict):
                raise ContractApplicationError(f"contract target is not an object: {target}")
            node = value
        return node, path[-1]
