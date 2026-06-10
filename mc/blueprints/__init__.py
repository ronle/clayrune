# mc.blueprints — Flask blueprints extracted from server.py
# (MODERNIZATION_PLAN.md Phase 1). One module per route family; registered on
# the existing `app` in server.py. Modules here import mc.core / mc.state
# only — NEVER server.py. Cross-family deps that haven't extracted yet are
# late-bound via each module's wire() (see local_auth._is_cf_tunneled_request).
