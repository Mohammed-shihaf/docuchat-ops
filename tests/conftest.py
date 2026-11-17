# Share the fakes-backed fixtures (rag, store, client, guest, sid) across all test modules.
from tests.test_app import client, guest, rag, sid, store  # noqa: F401
