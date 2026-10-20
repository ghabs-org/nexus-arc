"""Every supported dialect selects a driver with bounded connection/query I/O."""

import pytest
import sqlalchemy as sa

from nexus.core.studio_sql_connectors import DRIVERS, driver_options


@pytest.mark.parametrize("dialect", sorted(DRIVERS))
def test_default_driver_has_explicit_bounded_connect_policy(dialect):
    url, options = driver_options(
        sa.engine.make_url(dialect + "://user:password@localhost/example")
    )
    assert url.drivername == dialect + "+" + DRIVERS[dialect]
    assert options and all(value for value in options.values())
    assert (
        "connect_timeout" in options
        or "login_timeout" in options
        or "tcp_connect_timeout" in options
    )
    assert driver_options(url) == (url, options)


@pytest.mark.parametrize("dialect", sorted(DRIVERS))
def test_unreviewed_driver_fails_before_pool_creation(dialect):
    with pytest.raises(ValueError, match="supported SQL driver"):
        driver_options(sa.engine.make_url(dialect + "+unbounded://localhost/example"))
