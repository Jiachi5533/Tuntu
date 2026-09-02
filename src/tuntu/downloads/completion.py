from __future__ import annotations

from dataclasses import dataclass, replace

from tuntu.downloaders.clouddrive import DirectorySnapshot


@dataclass(frozen=True, slots=True, repr=False)
class CompletionState:
    baseline: DirectorySnapshot
    destination_path: str = "/"
    required_stable_observations: int = 2
    owned_paths: tuple[str, ...] = ()
    last_fingerprint: tuple[tuple[str, int], ...] = ()
    stable_observations: int = 0

    def __post_init__(self) -> None:
        if self.required_stable_observations < 2:
            raise ValueError("at least two stable observations are required")
        if not self.destination_path.startswith("/"):
            raise ValueError("destination_path must be absolute")

    def __repr__(self) -> str:
        return (
            "CompletionState("
            f"baseline={self.baseline!r}, owned_path_count={len(self.owned_paths)}, "
            f"stable_observations={self.stable_observations})"
        )


@dataclass(frozen=True, slots=True)
class CompletionObservation:
    state: CompletionState
    completed: bool
    changed_file_count: int
    changed_total_size: int


def observe_completion(
    state: CompletionState, current: DirectorySnapshot
) -> CompletionObservation:
    baseline = state.baseline.as_size_map()
    current_sizes = current.as_size_map()
    if state.owned_paths:
        changed = {
            path: size
            for path, size in current_sizes.items()
            if any(
                path == owned or path.startswith(owned.rstrip("/") + "/")
                for owned in state.owned_paths
            )
            and size != baseline.get(path)
        }
        owned_paths = state.owned_paths
    else:
        changed = {
            path: size
            for path, size in current_sizes.items()
            if size != baseline.get(path)
        }
        owned_paths = (
            tuple(
                sorted(
                    {
                        _top_level_owner(state.destination_path, path)
                        for path in changed
                    }
                )
            )
            if changed
            else ()
        )

    fingerprint = tuple(sorted(changed.items()))
    total_size = sum(changed.values())
    if not fingerprint or total_size <= 0:
        next_state = replace(
            state,
            owned_paths=owned_paths,
            last_fingerprint=(),
            stable_observations=0,
        )
        return CompletionObservation(next_state, False, len(changed), total_size)

    stable_count = (
        state.stable_observations + 1
        if fingerprint == state.last_fingerprint
        else 1
    )
    next_state = replace(
        state,
        owned_paths=owned_paths,
        last_fingerprint=fingerprint,
        stable_observations=stable_count,
    )
    return CompletionObservation(
        state=next_state,
        completed=stable_count >= state.required_stable_observations,
        changed_file_count=len(changed),
        changed_total_size=total_size,
    )


def _top_level_owner(destination: str, path: str) -> str:
    normalized_destination = destination.rstrip("/") or "/"
    if normalized_destination == "/":
        relative = path.lstrip("/")
        first = relative.split("/", 1)[0]
        return f"/{first}"
    prefix = normalized_destination + "/"
    if not path.startswith(prefix):
        raise ValueError("observed path is outside destination")
    relative = path[len(prefix) :]
    first = relative.split("/", 1)[0]
    return prefix + first
