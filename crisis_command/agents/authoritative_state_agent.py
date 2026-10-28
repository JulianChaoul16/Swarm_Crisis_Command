from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from math import isfinite
from typing import Mapping

from ..model import City, Road


class VerificationStatus(str, Enum):
    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    DISPUTED = "disputed"
    REJECTED = "rejected"


@dataclass(frozen=True)
class Claim:
    subject: str
    attribute: str
    value: object
    source: str
    timestamp: datetime
    world_version: int
    verification_status: VerificationStatus
    confidence: float
    evidence_reference: str

    def __post_init__(self) -> None:
        for field_name in ("subject", "attribute", "source", "evidence_reference"):
            if not getattr(self, field_name).strip():
                raise ValueError(f"Claim {field_name} cannot be empty.")
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("Claim timestamp must include a timezone.")
        if self.world_version < 1:
            raise ValueError("Claim world version must be positive.")
        if not isinstance(self.verification_status, VerificationStatus):
            raise ValueError("Claim verification status is invalid.")
        if not isfinite(self.confidence) or not 0 <= self.confidence <= 1:
            raise ValueError("Claim confidence must be between zero and one.")
        if self.attribute == "is_closed" and self.value is not None and not isinstance(
            self.value, bool
        ):
            raise ValueError("Road closure claims must be boolean or unknown.")


@dataclass(frozen=True)
class ClaimResolution:
    subject: str
    attribute: str
    claims: tuple[Claim, ...]
    selected_claim: Claim | None
    disputed: bool
    requires_review: bool = False
    status_unknown: bool = False

    @property
    def value(self) -> object | None:
        return self.selected_claim.value if self.selected_claim is not None else None


@dataclass(frozen=True)
class StateReview:
    world_version: int
    claims: tuple[Claim, ...]
    resolutions: tuple[ClaimResolution, ...]
    ignored_stale_claims: tuple[Claim, ...]
    unknown_road_ids: tuple[str, ...]

    @property
    def claims_requiring_review(self) -> tuple[ClaimResolution, ...]:
        return tuple(resolution for resolution in self.resolutions if resolution.requires_review)


class EvidenceArbitrator:
    def __init__(
        self,
        source_reliability: Mapping[str, float] | None = None,
        authoritative_sources: frozenset[str] | None = None,
    ) -> None:
        self._source_reliability = {"city_network": 0.5}
        self._source_reliability.update(source_reliability or {})
        for source, reliability in self._source_reliability.items():
            if (
                not source.strip()
                or not isfinite(reliability)
                or not 0 <= reliability <= 1
            ):
                raise ValueError("Source reliability must be between zero and one.")
        self.authoritative_sources = (
            authoritative_sources
            if authoritative_sources is not None
            else frozenset({"road_authority", "authoritative_state_agent"})
        )
        if any(not source.strip() for source in self.authoritative_sources):
            raise ValueError("Authoritative source names cannot be empty.")

    def source_reliability(self, source: str) -> float:
        return self._source_reliability.get(source, 0.5)

    def is_authoritative_closure(self, claim: Claim) -> bool:
        return (
            claim.attribute == "is_closed"
            and claim.value is True
            and claim.verification_status == VerificationStatus.VERIFIED
            and claim.source in self.authoritative_sources
        )

    def resolve(self, claims: tuple[Claim, ...]) -> tuple[ClaimResolution, ...]:
        grouped: dict[tuple[str, str], list[Claim]] = {}
        for claim in claims:
            grouped.setdefault((claim.subject, claim.attribute), []).append(claim)

        resolutions: list[ClaimResolution] = []
        for (subject, attribute), group in sorted(grouped.items()):
            eligible = [
                claim
                for claim in group
                if claim.verification_status != VerificationStatus.REJECTED
            ]
            if not eligible:
                resolutions.append(
                    ClaimResolution(subject, attribute, tuple(group), None, True, True)
                )
                continue

            def evidence_rank(claim: Claim) -> tuple[int, int, float, float, float]:
                verification = {
                    VerificationStatus.VERIFIED: 2,
                    VerificationStatus.UNVERIFIED: 1,
                    VerificationStatus.DISPUTED: 0,
                    VerificationStatus.REJECTED: -1,
                }[claim.verification_status]
                authoritative_closure = int(self.is_authoritative_closure(claim))
                return (
                    authoritative_closure,
                    verification,
                    self.source_reliability(claim.source),
                    claim.confidence,
                    claim.timestamp.timestamp(),
                )

            best_rank = max(evidence_rank(claim) for claim in eligible)
            strongest = [
                claim for claim in eligible if evidence_rank(claim) == best_rank
            ]
            values = [claim.value for claim in strongest]
            disputed = (
                any(value != values[0] for value in values[1:])
                or all(
                    claim.verification_status == VerificationStatus.DISPUTED
                    for claim in strongest
                )
            )
            selected = None if disputed else min(
                strongest,
                key=lambda claim: (claim.source, claim.evidence_reference),
            )
            conflicts = any(claim.value != values[0] for claim in eligible)
            requires_review = disputed or (
                conflicts
                and any(
                    claim.verification_status
                    in {VerificationStatus.UNVERIFIED, VerificationStatus.DISPUTED}
                    for claim in eligible
                )
            )
            status_unknown = any(claim.value is None for claim in eligible) or (
                attribute == "is_closed" and requires_review
            )
            resolutions.append(
                ClaimResolution(
                    subject,
                    attribute,
                    tuple(group),
                    selected,
                    disputed,
                    requires_review,
                    status_unknown,
                )
            )
        return tuple(resolutions)


class AuthoritativeStateAgent:
    """Reconciles versioned claims into a reviewed, evidence-backed state view."""

    name = "authoritative_state_agent"

    def __init__(self, arbitrator: EvidenceArbitrator | None = None) -> None:
        self.arbitrator = arbitrator or EvidenceArbitrator()

    def review(
        self,
        claims: tuple[Claim, ...],
        city: City | None,
        world_version: int,
    ) -> StateReview:
        if world_version < 1:
            raise ValueError("World version must be positive.")
        future_claims = tuple(
            claim for claim in claims if claim.world_version > world_version
        )
        if future_claims:
            raise ValueError("A claim cannot come from a future world version.")

        snapshot_claims = self._road_snapshot_claims(city, world_version)
        all_claims = claims + snapshot_claims
        superseded_closures = {
            (claim.subject, claim.attribute)
            for claim in claims
            if claim.world_version == world_version
            and claim.attribute == "is_closed"
            and claim.value is False
            and claim.verification_status == VerificationStatus.VERIFIED
            and claim.source in self.arbitrator.authoritative_sources
        }
        active_claims = tuple(
            claim
            for claim in all_claims
            if (
                claim.world_version == world_version
                or (
                    self.arbitrator.is_authoritative_closure(claim)
                    and (claim.subject, claim.attribute) not in superseded_closures
                )
            )
        )
        ignored_stale_claims = tuple(
            claim for claim in all_claims if claim not in active_claims
        )
        resolutions = self.arbitrator.resolve(active_claims)
        if city is not None:
            road_ids = {road.id for road in city.roads}
            unknown_roads = {
                resolution.subject
                for resolution in resolutions
                if resolution.attribute == "is_closed"
                and resolution.subject not in road_ids
            }
            if unknown_roads:
                raise ValueError(
                    "Road status claims reference unknown roads: "
                    f"{', '.join(sorted(unknown_roads))}."
                )
        elif any(resolution.attribute == "is_closed" for resolution in resolutions):
            raise ValueError("Road status claims require a city road network.")

        unknown_road_ids = tuple(
            sorted(
                resolution.subject
                for resolution in resolutions
                if resolution.attribute == "is_closed"
                and (
                    resolution.disputed
                    or resolution.status_unknown
                    or resolution.selected_claim is None
                    or resolution.value is None
                )
            )
        )
        return StateReview(
            world_version=world_version,
            claims=all_claims,
            resolutions=resolutions,
            ignored_stale_claims=ignored_stale_claims,
            unknown_road_ids=unknown_road_ids,
        )

    @staticmethod
    def _road_snapshot_claims(
        city: City | None,
        world_version: int,
    ) -> tuple[Claim, ...]:
        if city is None:
            return ()
        observed_at = datetime.now(timezone.utc)
        return tuple(
            Claim(
                subject=road.id,
                attribute="is_closed",
                value=road.is_closed,
                source="city_network",
                timestamp=observed_at,
                world_version=world_version,
                verification_status=VerificationStatus.VERIFIED,
                confidence=0.75,
                evidence_reference=f"city:{city.name}:road:{road.id}",
            )
            for road in city.roads
        )

    @staticmethod
    def city_with_evidence(
        city: City | None,
        resolutions: tuple[ClaimResolution, ...],
    ) -> City | None:
        road_updates = {
            resolution.subject: resolution
            for resolution in resolutions
            if resolution.attribute == "is_closed"
        }
        if city is None:
            if road_updates:
                raise ValueError("Road status claims require a city road network.")
            return None
        if not road_updates:
            return city
        road_ids = {road.id for road in city.roads}
        unknown_roads = set(road_updates) - road_ids
        if unknown_roads:
            raise ValueError(
                "Road status claims reference unknown roads: "
                f"{', '.join(sorted(unknown_roads))}."
            )
        updated_city = City(city.name)
        for road in city.roads:
            resolution = road_updates.get(road.id)
            is_closed = road.is_closed
            if resolution is not None:
                if (
                    not resolution.disputed
                    and resolution.selected_claim is not None
                    and resolution.value is not None
                    and not isinstance(resolution.value, bool)
                ):
                    raise ValueError(
                        f"Road closure claim for {road.id!r} must be boolean or unknown."
                    )
                is_closed = (
                    True
                    if (
                        resolution.disputed
                        or resolution.status_unknown
                        or resolution.value is None
                    )
                    else resolution.value
                )
            updated_city.add_road(
                Road(road.id, road.start, road.end, is_closed=is_closed)
            )
        return updated_city
