import os
import sys

# Add project root to sys.path
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)
from sqlalchemy import create_engine
import settings.settings as settings
from core.database import database
from core.utils import export
from core.utils import logger

def main():
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
        df = database.load_results(engine=engine)
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
            export.save_results(df=df)
            print("items.save_results() executed successfully.")

    except Exception as e:
        print(f"An error occurred: {e}")

    print("--- Save/Load Debug Script Finished ---")

if __name__ == "__main__":
    main()
