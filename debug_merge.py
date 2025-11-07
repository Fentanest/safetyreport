import os
import sys
from sqlalchemy import create_engine, select, func
import settings.settings as settings
import database
import logger

if __name__ == "__main__":
    # Initialize logger
    logger.LoggerFactory.create_logger()
    print("--- Merge Debug Script Started ---")

    # Check if db file exists
    db_path = settings.db_path
    if not os.path.exists(db_path):
        print(f"Database file not found at: {db_path}")
        print("Please run the main script first to generate the database and tables.")
        sys.exit(1)
    
    print(f"Using database: {db_path}")
    engine = create_engine(f'sqlite:///{db_path}')

    try:
        print("Calling items.merge_final()...")
        database.merge_final(engine=engine)
        print("items.merge_final() executed successfully.")

        # Verify if merge_table has data now
        with engine.connect() as conn:
            row_count_merge = conn.execute(select(func.count()).select_from(database.merge_table)).scalar()
            print(f"Verification: The final merge_table now contains {row_count_merge} rows.")

    except Exception as e:
        print(f"An error occurred during merge_final: {e}")

    print("--- Merge Debug Script Finished ---")
