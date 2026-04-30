"""Admin endpoints (operator JWT, behind Google IAP).

PROPRIETARY AND CONFIDENTIAL.
Copyright (c) 2026 Clayrune. All rights reserved.

Routes (skeletons):
  POST /v1/admin/versions
  POST /v1/admin/versions/{mc_version}/revoke
  POST /v1/admin/client_keys
  POST /v1/admin/client_keys/{key_id}/revoke
  POST /v1/admin/users/{user_id}/suspend
  POST /v1/admin/users/{user_id}/unsuspend
  GET  /v1/admin/devices
  POST /v1/admin/maintenance

Plus deprecated aliases /admin/builds and /admin/builds/{id}/revoke
that forward to /admin/versions for one transitional release.

See `docs/remote-access/03-control-plane-api.md` §3.14.
"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()

# TODO: implement with operator-JWT dependency + Cloud Logging audit trail
