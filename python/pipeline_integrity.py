from __future__ import annotations

from dataclasses import dataclass


LOCAL_REQUEST_INTEGRITY_ERROR = "LOCAL_REQUEST_INTEGRITY_ERROR"
FROZEN_REQUEST_MUTATION = "FROZEN_REQUEST_MUTATION"
REQUEST_IDENTITY_ERROR = "REQUEST_IDENTITY_ERROR"
CANDIDATE_IDENTITY_ERROR = "CANDIDATE_IDENTITY_ERROR"
LOCAL_PIPELINE_ERROR = "LOCAL_PIPELINE_ERROR"


@dataclass(frozen=True)
class PipelineFailureDetails:
    category: str
    stage: str
    provider_call_attempted: bool
    http_request_sent: bool


class PipelineIntegrityError(RuntimeError):
    category = LOCAL_REQUEST_INTEGRITY_ERROR

    def __init__(
        self,
        message: str,
        *,
        stage: str,
        provider_call_attempted: bool = False,
        http_request_sent: bool = False,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.provider_call_attempted = bool(provider_call_attempted)
        self.http_request_sent = bool(http_request_sent)


class FrozenRequestMutationError(PipelineIntegrityError):
    category = FROZEN_REQUEST_MUTATION


class RequestIdentityError(PipelineIntegrityError):
    category = REQUEST_IDENTITY_ERROR


class CandidateIdentityError(PipelineIntegrityError):
    category = CANDIDATE_IDENTITY_ERROR


def classify_local_pipeline_failure(exc: BaseException) -> PipelineFailureDetails:
    if isinstance(exc, PipelineIntegrityError):
        return PipelineFailureDetails(
            category=exc.category,
            stage=exc.stage,
            provider_call_attempted=exc.provider_call_attempted,
            http_request_sent=exc.http_request_sent,
        )
    return PipelineFailureDetails(
        category=LOCAL_PIPELINE_ERROR,
        stage="local_pipeline",
        provider_call_attempted=False,
        http_request_sent=False,
    )
