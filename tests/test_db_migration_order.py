from src.db.migrate import DDL_ORDER


def test_ops_schema_created_before_migration_tracking():
    assert DDL_ORDER[0] == "ddl_ops.sql"
