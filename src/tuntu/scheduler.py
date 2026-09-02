from __future__ import annotations

import re
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger


_DAILY_TIME = re.compile(r"^(\d{2}):(\d{2})(?::(\d{2}))?$")


@dataclass(frozen=True, slots=True)
class ScheduleSyncResult:
    profile_jobs: int
    invalid_profiles: tuple[int, ...]
    watchlist_jobs: int = 0
    invalid_watchlists: tuple[int, ...] = ()


class TuntuScheduler:
    def __init__(
        self,
        *,
        repository,
        run_service,
        download_poller,
        watchlist_runner=None,
        timezone_name: str,
        poll_interval_seconds: int,
        max_workers: int = 2,
        backend=None,
    ):
        if poll_interval_seconds <= 0 or max_workers <= 0:
            raise ValueError("scheduler intervals and limits must be positive")
        self.repository = repository
        self.run_service = run_service
        self.download_poller = download_poller
        self.watchlist_runner = watchlist_runner
        self.timezone = ZoneInfo(timezone_name)
        self.poll_interval_seconds = poll_interval_seconds
        self._backend = backend or BackgroundScheduler(
            timezone=self.timezone,
            executors={"default": ThreadPoolExecutor(max_workers=max_workers)},
        )
        self._profile_job_ids: set[str] = set()
        self._watchlist_job_ids: set[str] = set()
        self._audited_invalid_profiles: set[int] = set()
        self._audited_invalid_watchlists: set[int] = set()
        self._started = False

    def start(self) -> int:
        if self._started:
            return 0
        recovered = self.repository.fail_interrupted_runs()
        self.sync()
        self._backend.start()
        self._started = True
        return recovered

    def shutdown(self, *, wait: bool = True) -> None:
        if self._started:
            self._backend.shutdown(wait=wait)
            self._started = False

    def sync(self) -> ScheduleSyncResult:
        desired: set[str] = set()
        invalid_profiles = []
        for profile in self.repository.list_schedulable_profiles():
            try:
                hour, minute, second = self._parse_daily_time(profile.daily_time)
            except ValueError:
                invalid_profiles.append(profile.id)
                if profile.id not in self._audited_invalid_profiles:
                    self.repository.add_audit_event(
                        "profile_schedule_invalid",
                        entity_type="profile",
                        entity_id=str(profile.id),
                        details={"error_code": "invalid_daily_time"},
                    )
                    self._audited_invalid_profiles.add(profile.id)
                continue
            job_id = f"profile:{profile.id}"
            desired.add(job_id)
            self._backend.add_job(
                self._run_profile,
                CronTrigger(
                    hour=hour,
                    minute=minute,
                    second=second,
                    timezone=self.timezone,
                ),
                args=(profile.id,),
                id=job_id,
                replace_existing=True,
                max_instances=1,
                coalesce=True,
                misfire_grace_time=1,
            )

        for job_id in self._profile_job_ids - desired:
            try:
                self._backend.remove_job(job_id)
            except KeyError:
                pass
        self._profile_job_ids = desired
        desired_watchlists: set[str] = set()
        invalid_watchlists = []
        if self.watchlist_runner is not None:
            for watchlist in self.repository.list_schedulable_watchlists():
                watchlist_id = watchlist["id"]
                try:
                    hour, minute, second = self._parse_daily_time(
                        watchlist["automation"].get("daily_time")
                    )
                except ValueError:
                    invalid_watchlists.append(watchlist_id)
                    if watchlist_id not in self._audited_invalid_watchlists:
                        self.repository.add_audit_event(
                            "watchlist_schedule_invalid",
                            entity_type="watchlist",
                            entity_id=str(watchlist_id),
                            details={"error_code": "invalid_daily_time"},
                        )
                        self._audited_invalid_watchlists.add(watchlist_id)
                    continue
                job_id = f"watchlist:{watchlist_id}"
                desired_watchlists.add(job_id)
                self._backend.add_job(
                    self._run_watchlist,
                    CronTrigger(
                        hour=hour,
                        minute=minute,
                        second=second,
                        timezone=self.timezone,
                    ),
                    args=(watchlist_id,),
                    id=job_id,
                    replace_existing=True,
                    max_instances=1,
                    coalesce=True,
                    misfire_grace_time=1,
                )
        for job_id in self._watchlist_job_ids - desired_watchlists:
            try:
                self._backend.remove_job(job_id)
            except KeyError:
                pass
        self._watchlist_job_ids = desired_watchlists
        self._backend.add_job(
            self.download_poller.poll_once,
            IntervalTrigger(
                seconds=self.poll_interval_seconds,
                timezone=self.timezone,
            ),
            id="downloads:poll",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=1,
        )
        return ScheduleSyncResult(
            len(desired),
            tuple(invalid_profiles),
            len(desired_watchlists),
            tuple(invalid_watchlists),
        )

    def reconfigure_timezone(self, timezone_name: str) -> ScheduleSyncResult:
        self.timezone = ZoneInfo(timezone_name)
        return self.sync()

    def _run_profile(self, profile_id: int) -> None:
        self.run_service.execute(profile_id, trigger="scheduled")

    def _run_watchlist(self, watchlist_id: int) -> None:
        self.watchlist_runner.run(
            watchlist_id, force_dry_run=False, trigger="scheduled"
        )

    @staticmethod
    def _parse_daily_time(value: str | None) -> tuple[int, int, int]:
        match = _DAILY_TIME.fullmatch(value or "")
        if match is None:
            raise ValueError("invalid daily time")
        hour, minute, second = (
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3) or 0),
        )
        if hour > 23 or minute > 59 or second > 59:
            raise ValueError("invalid daily time")
        return hour, minute, second
