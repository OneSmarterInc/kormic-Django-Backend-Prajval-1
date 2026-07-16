from __future__ import annotations

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsStudentRole, IsTOTPEnrolled
from verification import services

STUDENT_PERMISSIONS = [IsAuthenticated, IsTOTPEnrolled, IsStudentRole]


def _api_error(message: str, http_status=status.HTTP_400_BAD_REQUEST) -> Response:
    return Response({"status": "error", "message": str(message)}, status=http_status)


class VerificationStatusAPIView(APIView):
    """
    GET /api/verification/status/
    Full verification state, including every item (open and resolved).
    Always recomputed live -- safe to poll after any reupload.
    """

    permission_classes = STUDENT_PERMISSIONS

    def get(self, request):
        student_id = request.user.account.student_id
        return Response(services.run_verification(student_id, user=request.user), status=status.HTTP_200_OK)


class VerificationReanalyzeAPIView(APIView):
    """
    POST /api/verification/reanalyze/
    Identical result to GET /status/ -- exposed as its own POST action so
    the frontend has a natural "Reanalyze" button to call after a student
    edits their profile or reuploads a resume/GitHub/LinkedIn source.
    """

    permission_classes = STUDENT_PERMISSIONS

    def post(self, request):
        student_id = request.user.account.student_id
        return Response(services.run_verification(student_id, user=request.user), status=status.HTTP_200_OK)


class VerificationItemListAPIView(APIView):
    """
    GET /api/verification/items/?status=open|resolved|all (default: open)
    The "verification pending list" -- one row per flagged disagreement.
    Does not trigger a reanalysis; call /reanalyze/ first if the data might
    be stale.
    """

    permission_classes = STUDENT_PERMISSIONS
    VALID_FILTERS = {"open", "resolved", "all"}

    def get(self, request):
        student_id = request.user.account.student_id
        filter_status = str(request.query_params.get("status", "open")).strip().lower()

        if filter_status not in self.VALID_FILTERS:
            return _api_error(f"status must be one of {sorted(self.VALID_FILTERS)}.")

        items = services.list_items(student_id, filter_status)
        return Response(
            {"student_id": student_id, "status_filter": filter_status, "count": len(items), "items": items},
            status=status.HTTP_200_OK,
        )


class VerificationItemDecisionAPIView(APIView):
    """
    POST /api/verification/items/<item_id>/decision/
    Body: {"action": "confirm" | "ignore" | "clarify", "note": "..."}
    Resolves exactly one flagged item. `note` is required for "clarify"
    (optional free text otherwise). Once resolved, an item is immutable --
    calling this again on the same item returns 400.
    """

    permission_classes = STUDENT_PERMISSIONS
    VALID_ACTIONS = {"confirm", "ignore", "clarify"}

    def post(self, request, item_id):
        student_id = request.user.account.student_id
        action = str(request.data.get("action", "")).strip().lower()
        note = str(request.data.get("note", "") or "").strip()

        if action not in self.VALID_ACTIONS:
            return _api_error(f"action must be one of {sorted(self.VALID_ACTIONS)}.")
        if action == "clarify" and not note:
            return _api_error("note is required when action is 'clarify'.")

        try:
            result = services.resolve_item(student_id=student_id, item_id=item_id, action=action, note=note)
        except services.ItemNotFound:
            return _api_error("Verification item not found.", status.HTTP_404_NOT_FOUND)
        except services.ItemNotOwned:
            return _api_error("You may only resolve your own verification items.", status.HTTP_403_FORBIDDEN)
        except services.ItemAlreadyResolved:
            return _api_error("This item has already been resolved.")

        return Response(result, status=status.HTTP_200_OK)
