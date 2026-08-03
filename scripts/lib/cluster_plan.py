from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


class ConfirmationRequired(RuntimeError):
    pass


@dataclass(frozen=True)
class ClusterPlan:
    targets: list[str]
    production_targets: list[str]
    filters: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "targets": list(self.targets),
            "target_count": len(self.targets),
            "production_targets": list(self.production_targets),
            "filters": dict(self.filters),
        }


def _normalize(values: Iterable[str] | None) -> list[str] | None:
    if values is None:
        return None
    return sorted({value.strip() for value in values if value and value.strip()})


def resolve_cluster_plan(
    loader,
    aliases: Iterable[str] | None,
    environment: str | None,
    tags: Iterable[str] | None,
) -> ClusterPlan:
    requested_aliases = _normalize(aliases)
    requested_tags = _normalize(tags)
    known_aliases = sorted(set(loader.list_hosts()))
    candidates = (
        [alias for alias in requested_aliases if alias in known_aliases]
        if requested_aliases is not None
        else known_aliases
    )

    targets = []
    production_targets = []
    for alias in candidates:
        metadata = loader.load_metadata(alias)
        host_environment = str(metadata.get("environment", "")).lower()
        host_tags = {str(tag) for tag in metadata.get("tags", [])}
        normalized_host_tags = {tag.lower() for tag in host_tags}
        if environment and host_environment != environment.lower():
            continue
        if requested_tags and not any(tag in host_tags for tag in requested_tags):
            continue
        targets.append(alias)
        if (
            host_environment in {"production", "prod"}
            or normalized_host_tags.intersection({"production", "prod"})
        ):
            production_targets.append(alias)

    return ClusterPlan(
        targets=sorted(targets),
        production_targets=sorted(production_targets),
        filters={
            "aliases": requested_aliases,
            "environment": environment,
            "tags": requested_tags,
        },
    )


def validate_cluster_apply(
    plan: ClusterPlan,
    apply: bool,
    confirm_production: bool,
) -> None:
    if not apply:
        raise ConfirmationRequired("cluster execution requires --apply")
    if plan.production_targets and not confirm_production:
        raise ConfirmationRequired(
            "production targets require --confirm-production"
        )
