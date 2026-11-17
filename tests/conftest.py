# Share the fakes-backed fixtures (rag, store, client, sid) across all test modules.
from tests.test_app import client, rag, sid, store  # noqa: F401
