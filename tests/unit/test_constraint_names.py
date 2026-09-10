from home_radar_models import Base
from sqlalchemy import CheckConstraint


def test_check_constraint_names_fit_postgresql_identifier_limit() -> None:
    oversized = [
        f"{table.name}.{constraint.name}"
        for table in Base.metadata.tables.values()
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
        and constraint.name is not None
        and len(str(constraint.name)) > 63
    ]

    assert oversized == []
