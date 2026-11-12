"""Screen crashes reported by the web app, so support can find them in the server log.

The web app's error screens show a short reference; the same reference lands here with the
screen path and the error name. Nothing is stored in the database and nothing is sent anywhere
else: it is one log line. Only signed-in staff can report, a few per minute, every field capped.
"""

from typing import Annotated

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field, StringConstraints

from .. import ratelimit
from ..deps import PrincipalDep
from ..errors import ApiError, log

router = APIRouter(prefix="/client-errors", tags=["errors"])

Short = Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)]


class ClientErrorIn(BaseModel):
    reference: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9._:-]{1,64}$")]
    kind: Annotated[str, StringConstraints(pattern=r"^(render|chunk|unhandled)$")]
    path: Short = ""
    name: Short = ""
    message: Annotated[str, StringConstraints(max_length=500)] = ""
    stack: Annotated[str, StringConstraints(max_length=4000)] = Field(default="")


@router.post("", status_code=204)
def report(body: ClientErrorIn, request: Request, p: PrincipalDep) -> Response:
    if ratelimit.attempt("client-errors", str(p.employee.id), 60) > 20:
        raise ApiError(429, "RATE_LIMITED", "Too many reports at once; wait a moment")
    log.error(
        "client error",
        extra={
            "ctx": {
                "request_id": getattr(request.state, "request_id", None),
                "reference": body.reference,
                "kind": body.kind,
                "screen": body.path,
                "error": body.name,
                "detail": body.message,
                "stack": body.stack,
                "actor_id": str(p.employee.id),
            }
        },
    )
    return Response(status_code=204)
