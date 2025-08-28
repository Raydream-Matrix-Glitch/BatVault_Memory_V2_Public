from core_config.constants import CACHE_TTL_RESOLVER_SEC, EMBEDDING_DIM, HEALTH_PORT


def test_gateway_defaults_are_ints():
    assert isinstance(CACHE_TTL_RESOLVER_SEC, int)
    assert isinstance(EMBEDDING_DIM, int)
    assert isinstance(HEALTH_PORT, int)