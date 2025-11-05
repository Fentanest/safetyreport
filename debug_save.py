import os
import sys
from sqlalchemy import create_engine
import settings.settings as settings
import items
import logger

if __name__ == "__main__":
    # Initialize logger
    logger.LoggerFactory.create_logger()
    print("--- Save/Load Debug Script Started ---")

    # Check if db file exists
    db_path = settings.db_path
    if not os.path.exists(db_path):
        print(f"Database file not found at: {db_path}")
        print("Please run the main script and the merge debug script first.")
        sys.exit(1)
    
    print(f"Using database: {db_path}")
    engine = create_engine(f'sqlite:///{db_path}')

    try:
        print("Calling items.load_results()...")
        df = items.load_results(engine=engine)
        print("items.load_results() executed.")

        print("\n--- Loaded DataFrame for Saving ---")
        print(f"DataFrame shape: {df.shape}")
        print("DataFrame head:")
        print(df.head())
        print("-----------------------------------\n")

        if df.empty:
            print("DataFrame is empty. Skipping save_results() to avoid clearing the sheet.")
        else:
            print("DataFrame is not empty. Calling items.save_results()...")
            items.save_results(df=df)
            print("items.save_results() executed successfully.")

    except Exception as e:
        print(f"An error occurred: {e}")

    print("--- Save/Load Debug Script Finished ---")
